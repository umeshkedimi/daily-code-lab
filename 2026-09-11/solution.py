"""FastAPI endpoint that calls an OpenAI chat model through a LangChain
(LCEL) pipeline: a prompt template piped into a chat model piped into a
string parser.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import openai
from fastapi import FastAPI, HTTPException, status
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("genai_gateway")
logging.basicConfig(level=logging.INFO)

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "30"))
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000, description="User prompt")
    system_prompt: Optional[str] = Field(default=None, max_length=2000)
    model: Optional[str] = Field(default=None, description="Overrides OPENAI_MODEL for this call")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)

    @field_validator("prompt")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class GenerateResponse(BaseModel):
    output: str
    model: str
    latency_ms: int


def build_chain(model: str, temperature: float, max_tokens: int) -> Runnable:
    """prompt template -> chat model -> string parser (LangChain Expression Language).

    The prompt template is the actual reason to reach for LangChain instead
    of calling the OpenAI SDK directly: it's one place that standardizes how
    system context and user input are composed, independent of which model
    or provider sits behind `llm` -- swapping ChatOpenAI for another chat
    model later wouldn't touch this composition at all.
    """
    llm = ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens, timeout=REQUEST_TIMEOUT_SECONDS)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{prompt}"),
        ]
    )
    return prompt | llm | StrOutputParser()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY is not set -- /generate will fail at call time, not at startup")
    yield


app = FastAPI(title="GenAI Gateway", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest) -> GenerateResponse:
    model = body.model or DEFAULT_MODEL
    system_prompt = body.system_prompt or DEFAULT_SYSTEM_PROMPT

    start = time.monotonic()
    try:
        # Built inside the try block deliberately: constructing ChatOpenAI
        # raises immediately (not lazily) if no API key is configured, so
        # that failure needs to be caught here too, not just call failures.
        chain = build_chain(model, body.temperature, body.max_tokens)
        output = await chain.ainvoke({"system_prompt": system_prompt, "prompt": body.prompt})
    except openai.AuthenticationError as exc:
        logger.error("OpenAI authentication failed: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "upstream authentication misconfigured") from exc
    except openai.RateLimitError as exc:
        logger.warning("OpenAI rate limit hit: %s", exc)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "upstream rate limit exceeded, retry later") from exc
    except openai.APITimeoutError as exc:
        logger.warning("OpenAI request timed out: %s", exc)
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "upstream request timed out") from exc
    except openai.APIStatusError as exc:
        logger.error("OpenAI API error (status %s): %s", exc.status_code, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "upstream API error") from exc
    except openai.APIConnectionError as exc:
        logger.error("Could not reach OpenAI: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "could not reach upstream API") from exc
    except openai.OpenAIError as exc:
        # Catches client configuration errors (e.g. missing API key) raised
        # at construction time, before any network call is even attempted.
        logger.error("OpenAI client misconfigured: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "upstream client misconfigured") from exc

    latency_ms = int((time.monotonic() - start) * 1000)
    return GenerateResponse(output=output, model=model, latency_ms=latency_ms)
