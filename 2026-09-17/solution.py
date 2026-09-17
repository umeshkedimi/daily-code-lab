"""A LiteLLM-style unified LLM gateway: one request shape, many real
provider wire formats underneath, with per-provider retry and cross-model
fallback.

Ties together three things built on earlier days rather than reinventing
them: the provider-adapter/unified-interface pattern from the tool-calling
agent loop, exponential-backoff-with-jitter retry, and the FastAPI gateway
shape from the LangChain exercise.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

import httpx


# --- Unified request/response shape ----------------------------------------


@dataclass(frozen=True)
class UnifiedResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ProviderAdapter(Protocol):
    async def complete(
        self, model_name: str, messages: List[dict], max_tokens: int, temperature: float
    ) -> UnifiedResponse: ...


# --- Retry (same shape as the standalone retry exercise, kept local/
# self-contained here rather than importing across day-folders) -----------


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 2.0


class RetryError(Exception):
    def __init__(self, attempts: int, last_exception: Exception):
        super().__init__(f"gave up after {attempts} attempt(s): {last_exception!r}")
        self.attempts = attempts
        self.last_exception = last_exception


def is_retryable_http_error(exc: Exception) -> bool:
    """5xx and 429 (rate limited) are worth retrying; 4xx otherwise is not --
    a bad request or bad model name won't fix itself on retry."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def _compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    ceiling = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)


async def retry_async(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    policy: RetryPolicy = RetryPolicy(),
    classify: Callable[[Exception], bool] = is_retryable_http_error,
) -> Any:
    import asyncio

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func(*args)
        except Exception as exc:
            if not classify(exc):
                raise
            if attempt == policy.max_attempts:
                raise RetryError(attempt, exc) from exc
            await asyncio.sleep(_compute_backoff(attempt, policy))


# --- Provider adapters: real wire formats, real HTTP calls (a MockTransport
# stands in for the network in tests -- see test_solution.py) --------------


class OpenAIAdapter:
    """Talks OpenAI's actual chat.completions shape -- already what this
    gateway's unified request looks like, so this adapter does the least
    translation work of the two."""

    def __init__(self, api_key: str = "", transport: Optional[httpx.AsyncBaseTransport] = None):
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=30.0,
        )

    async def complete(
        self, model_name: str, messages: List[dict], max_tokens: int, temperature: float
    ) -> UnifiedResponse:
        start = time.monotonic()
        response = await self._client.post(
            "/chat/completions",
            json={"model": model_name, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return UnifiedResponse(
            content=choice["content"],
            model=data.get("model", model_name),
            provider="openai",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicAdapter:
    """Talks Anthropic's actual Messages API shape -- meaningfully
    different from the unified request: system prompt is a separate
    top-level field (not a message with role 'system'), max_tokens is
    required, auth is 'x-api-key' + 'anthropic-version' (not Bearer), and
    the response content is a list of typed blocks, not a single string."""

    def __init__(self, api_key: str = "", transport: Optional[httpx.AsyncBaseTransport] = None):
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            transport=transport,
            timeout=30.0,
        )

    async def complete(
        self, model_name: str, messages: List[dict], max_tokens: int, temperature: float
    ) -> UnifiedResponse:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        conversation = [m for m in messages if m["role"] != "system"]

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)

        start = time.monotonic()
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        data = response.json()
        text = "".join(block["text"] for block in data["content"] if block["type"] == "text")
        usage = data.get("usage", {})
        return UnifiedResponse(
            content=text,
            model=data.get("model", model_name),
            provider="anthropic",
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


# --- Gateway: routing + retry + fallback ------------------------------------


class UnknownProviderError(Exception):
    pass


class AllModelsFailedError(Exception):
    def __init__(self, attempts: Dict[str, str]):
        super().__init__(f"every configured model failed: {attempts}")
        self.attempts = attempts


class LLMGateway:
    def __init__(self, adapters: Dict[str, ProviderAdapter], retry_policy: RetryPolicy = RetryPolicy()):
        self._adapters = adapters
        self._retry_policy = retry_policy

    def _resolve(self, model: str) -> Tuple[ProviderAdapter, str]:
        if "/" not in model:
            raise UnknownProviderError(f"model must be '<provider>/<model_name>', got {model!r}")
        provider, model_name = model.split("/", 1)
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise UnknownProviderError(f"no adapter configured for provider {provider!r}")
        return adapter, model_name

    async def complete(
        self,
        model: str,
        messages: List[dict],
        fallback_models: List[str] = (),
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> UnifiedResponse:
        """Tries `model`, then each of `fallback_models` in order. Each
        candidate gets its own retry budget for transient failures; a
        candidate that fails for any reason (exhausted retries, a
        non-retryable error, an unconfigured provider) is recorded and the
        next candidate is tried -- one bad model/provider never blocks the
        rest of the chain.
        """
        attempts: Dict[str, str] = {}
        for candidate in (model, *fallback_models):
            try:
                adapter, model_name = self._resolve(candidate)
            except UnknownProviderError as exc:
                attempts[candidate] = str(exc)
                continue

            try:
                return await retry_async(
                    adapter.complete, model_name, messages, max_tokens, temperature, policy=self._retry_policy
                )
            except RetryError as exc:
                attempts[candidate] = f"exhausted retries: {exc.last_exception!r}"
            except Exception as exc:  # noqa: BLE001 -- deliberate: isolate this candidate's failure, try the next
                attempts[candidate] = f"{type(exc).__name__}: {exc}"

        raise AllModelsFailedError(attempts)


# --- FastAPI: OpenAI-compatible /v1/chat/completions, backed by the gateway -


import os

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = Field(..., description="'<provider>/<model_name>', e.g. 'openai/gpt-4o-mini'")
    messages: List[ChatMessage]
    fallback_models: List[str] = Field(default_factory=list)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class ChatCompletionResponse(BaseModel):
    content: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


def build_default_gateway() -> LLMGateway:
    return LLMGateway(
        {
            "openai": OpenAIAdapter(api_key=os.environ.get("OPENAI_API_KEY", "")),
            "anthropic": AnthropicAdapter(api_key=os.environ.get("ANTHROPIC_API_KEY", "")),
        }
    )


app = FastAPI(title="LLM Gateway")
_gateway: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = build_default_gateway()
    return _gateway


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(body: ChatCompletionRequest) -> ChatCompletionResponse:
    gateway = get_gateway()
    try:
        result = await gateway.complete(
            model=body.model,
            messages=[m.model_dump() for m in body.messages],
            fallback_models=body.fallback_models,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
    except AllModelsFailedError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {"error": "all models failed", "attempts": exc.attempts}) from exc
    return ChatCompletionResponse(**result.__dict__)


# --- Demo --------------------------------------------------------------------


def _mock_transport(responder: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(responder)


def _openai_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-demo",
            "model": "gpt-4o-mini-2024-07-18",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello from OpenAI (mocked)."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        },
    )


def _anthropic_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg-demo",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-haiku-20241022",
            "content": [{"type": "text", "text": "Hello from Anthropic (mocked)."}],
            "usage": {"input_tokens": 10, "output_tokens": 7},
        },
    )


def _always_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "internal server error"})


async def demo() -> None:
    print("1) Both providers healthy: request goes to the primary model")
    gateway = LLMGateway(
        {
            "openai": OpenAIAdapter(transport=_mock_transport(_openai_success)),
            "anthropic": AnthropicAdapter(transport=_mock_transport(_anthropic_success)),
        }
    )
    result = await gateway.complete("openai/gpt-4o-mini", [{"role": "user", "content": "hi"}])
    print(f"   provider={result.provider} model={result.model} content={result.content!r}")

    print("\n2) Primary provider down (persistent 500s): falls back to a different provider")
    gateway = LLMGateway(
        {
            "openai": OpenAIAdapter(transport=_mock_transport(_always_500)),
            "anthropic": AnthropicAdapter(transport=_mock_transport(_anthropic_success)),
        },
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.05, max_delay=0.2),
    )
    result = await gateway.complete(
        "openai/gpt-4o-mini", [{"role": "user", "content": "hi"}], fallback_models=["anthropic/claude-3-5-haiku-20241022"]
    )
    print(f"   served by provider={result.provider} model={result.model} content={result.content!r}")

    print("\n3) Every configured model fails: raises with full per-model attempt detail")
    gateway = LLMGateway(
        {"openai": OpenAIAdapter(transport=_mock_transport(_always_500))},
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.02, max_delay=0.1),
    )
    try:
        await gateway.complete("openai/gpt-4o-mini", [{"role": "user", "content": "hi"}], fallback_models=["openai/gpt-4o"])
    except AllModelsFailedError as exc:
        print(f"   raised as expected, attempts: {exc.attempts}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(demo())
