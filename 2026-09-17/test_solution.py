"""Tests the gateway against realistic, documented provider response shapes
via httpx.MockTransport -- real HTTP request-building/serialization and
response-parsing, not just Python-level mocking of internal functions. No
real API keys are needed or used.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import solution
from solution import (
    AllModelsFailedError,
    AnthropicAdapter,
    LLMGateway,
    OpenAIAdapter,
    RetryPolicy,
    UnknownProviderError,
)

FAST_RETRY = RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05)


def _openai_response(content="hello", model="gpt-4o-mini-2024-07-18"):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        },
    )


def _anthropic_response(text="hello", model="claude-3-5-haiku-20241022"):
    return httpx.Response(
        200,
        json={
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 8, "output_tokens": 5},
        },
    )


def _error(status_code):
    return httpx.Response(status_code, json={"error": {"message": f"simulated {status_code}"}})


# --- Adapters translate real, documented wire formats correctly -----------


@pytest.mark.anyio
async def test_openai_adapter_parses_real_response_shape():
    adapter = OpenAIAdapter(transport=httpx.MockTransport(lambda r: _openai_response("hi there")))
    result = await adapter.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}], 512, 0.7)
    assert result.content == "hi there"
    assert result.provider == "openai"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 4


@pytest.mark.anyio
async def test_anthropic_adapter_parses_real_response_shape():
    adapter = AnthropicAdapter(transport=httpx.MockTransport(lambda r: _anthropic_response("hi there")))
    result = await adapter.complete("claude-3-5-haiku-20241022", [{"role": "user", "content": "hi"}], 512, 0.7)
    assert result.content == "hi there"
    assert result.provider == "anthropic"
    assert result.prompt_tokens == 8  # translated from Anthropic's "input_tokens"
    assert result.completion_tokens == 5  # translated from Anthropic's "output_tokens"


@pytest.mark.anyio
async def test_anthropic_adapter_separates_system_message_and_sets_max_tokens():
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = httpx.Request(request.method, request.url, content=request.content).content
        import json

        captured["json"] = json.loads(request.content)
        return _anthropic_response()

    adapter = AnthropicAdapter(transport=httpx.MockTransport(capture))
    await adapter.complete(
        "claude-3-5-haiku-20241022",
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}],
        max_tokens=256,
        temperature=0.5,
    )
    sent = captured["json"]
    assert sent["system"] == "be terse"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]  # system message removed from the list
    assert sent["max_tokens"] == 256  # Anthropic requires this; OpenAI's shape treats it as optional


# --- Gateway: routing -------------------------------------------------------


@pytest.mark.anyio
async def test_gateway_routes_by_provider_prefix():
    gateway = LLMGateway(
        {
            "openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _openai_response("from openai"))),
            "anthropic": AnthropicAdapter(transport=httpx.MockTransport(lambda r: _anthropic_response("from anthropic"))),
        }
    )
    r1 = await gateway.complete("openai/gpt-4o-mini", [{"role": "user", "content": "hi"}])
    r2 = await gateway.complete("anthropic/claude-3-5-haiku-20241022", [{"role": "user", "content": "hi"}])
    assert r1.content == "from openai"
    assert r2.content == "from anthropic"


@pytest.mark.anyio
async def test_gateway_unknown_provider_raises_all_models_failed_with_detail():
    gateway = LLMGateway({"openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _openai_response()))})
    with pytest.raises(AllModelsFailedError) as excinfo:
        await gateway.complete("cohere/command-r", [{"role": "user", "content": "hi"}])
    assert "cohere/command-r" in excinfo.value.attempts
    assert "no adapter configured" in excinfo.value.attempts["cohere/command-r"]


# --- Gateway: retry-then-succeed vs retry-exhaustion-then-fallback ---------


@pytest.mark.anyio
async def test_gateway_recovers_via_retry_without_needing_fallback():
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _error(500) if calls["n"] == 1 else _openai_response("recovered")

    gateway = LLMGateway(
        {"openai": OpenAIAdapter(transport=httpx.MockTransport(flaky))}, retry_policy=FAST_RETRY
    )
    result = await gateway.complete("openai/gpt-4o-mini", [{"role": "user", "content": "hi"}])
    assert result.content == "recovered"
    assert calls["n"] == 2  # first call failed, second (retry) succeeded -- no fallback needed


@pytest.mark.anyio
async def test_gateway_falls_back_when_primary_exhausts_retries():
    gateway = LLMGateway(
        {
            "openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _error(500))),
            "anthropic": AnthropicAdapter(transport=httpx.MockTransport(lambda r: _anthropic_response("fallback answer"))),
        },
        retry_policy=FAST_RETRY,
    )
    result = await gateway.complete(
        "openai/gpt-4o-mini",
        [{"role": "user", "content": "hi"}],
        fallback_models=["anthropic/claude-3-5-haiku-20241022"],
    )
    assert result.content == "fallback answer"
    assert result.provider == "anthropic"


@pytest.mark.anyio
async def test_gateway_non_retryable_error_fails_fast_to_fallback_without_exhausting_retry_budget():
    calls = {"n": 0}

    def bad_request(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _error(400)

    gateway = LLMGateway(
        {
            "openai": OpenAIAdapter(transport=httpx.MockTransport(bad_request)),
            "anthropic": AnthropicAdapter(transport=httpx.MockTransport(lambda r: _anthropic_response("fallback"))),
        },
        retry_policy=FAST_RETRY,  # max_attempts=3 -- if this fired, calls["n"] would be 3
    )
    result = await gateway.complete(
        "openai/gpt-4o-mini",
        [{"role": "user", "content": "hi"}],
        fallback_models=["anthropic/claude-3-5-haiku-20241022"],
    )
    assert result.provider == "anthropic"
    assert calls["n"] == 1  # a 400 is not retryable -- one wasted call, not a whole retry budget


@pytest.mark.anyio
async def test_gateway_raises_when_every_model_fails():
    gateway = LLMGateway(
        {"openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _error(500)))},
        retry_policy=FAST_RETRY,
    )
    with pytest.raises(AllModelsFailedError) as excinfo:
        await gateway.complete(
            "openai/gpt-4o-mini", [{"role": "user", "content": "hi"}], fallback_models=["openai/gpt-4o"]
        )
    assert set(excinfo.value.attempts) == {"openai/gpt-4o-mini", "openai/gpt-4o"}


# --- FastAPI endpoint --------------------------------------------------------


def test_endpoint_success(monkeypatch):
    gateway = LLMGateway({"openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _openai_response("via http")))})
    monkeypatch.setattr(solution, "_gateway", gateway)
    client = TestClient(solution.app)
    response = client.post(
        "/v1/chat/completions", json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 200
    assert response.json()["content"] == "via http"


def test_endpoint_all_models_failed_returns_502_with_attempts(monkeypatch):
    gateway = LLMGateway(
        {"openai": OpenAIAdapter(transport=httpx.MockTransport(lambda r: _error(500)))}, retry_policy=FAST_RETRY
    )
    monkeypatch.setattr(solution, "_gateway", gateway)
    client = TestClient(solution.app)
    response = client.post(
        "/v1/chat/completions", json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 502
    assert "openai/gpt-4o-mini" in response.json()["detail"]["attempts"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
