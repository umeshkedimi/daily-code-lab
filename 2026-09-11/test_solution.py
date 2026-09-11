"""Tests the endpoint's plumbing (validation, response shape, error mapping)
without spending real OpenAI credits -- the LangChain chain is mocked at
the seam `solution.build_chain`, and the missing-API-key path is exercised
for real, since it needs no network access at all.
"""

import os

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

import solution

client = TestClient(solution.app)


class _StubChain:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    async def ainvoke(self, _inputs):
        if self._error:
            raise self._error
        return self._result


def _status_error(cls, status_code: int) -> Exception:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls(f"simulated {status_code}", response=response, body=None)


def _timeout_error() -> Exception:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return openai.APITimeoutError(request=request)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_rejects_blank_prompt():
    response = client.post("/generate", json={"prompt": "   "})
    assert response.status_code == 422


def test_generate_rejects_out_of_range_temperature():
    response = client.post("/generate", json={"prompt": "hi", "temperature": 5.0})
    assert response.status_code == 422


def test_generate_without_api_key_returns_clean_500(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/generate", json={"prompt": "hello"})
    assert response.status_code == 500
    assert response.json()["detail"] == "upstream client misconfigured"


def test_generate_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(
        solution, "build_chain", lambda model, temperature, max_tokens: _StubChain(result="mocked output")
    )
    response = client.post("/generate", json={"prompt": "hello", "model": "gpt-4o-mini"})
    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "mocked output"
    assert body["model"] == "gpt-4o-mini"
    assert body["latency_ms"] >= 0


@pytest.mark.parametrize(
    "error_factory, expected_status",
    [
        (lambda: _status_error(openai.AuthenticationError, 401), 500),
        (lambda: _status_error(openai.RateLimitError, 429), 429),
        (_timeout_error, 504),
        (lambda: _status_error(openai.BadRequestError, 400), 502),
    ],
)
def test_generate_maps_openai_errors_to_http_status(monkeypatch, error_factory, expected_status):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(
        solution, "build_chain", lambda model, temperature, max_tokens: _StubChain(error=error_factory())
    )
    response = client.post("/generate", json={"prompt": "hello"})
    assert response.status_code == expected_status
