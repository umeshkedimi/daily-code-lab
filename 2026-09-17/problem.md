# Problem

**Category:** `genai-agentic` / `backend` / `system-design`

## Statement

Build a **LiteLLM-style unified LLM gateway** — one request shape that can call multiple, genuinely different LLM provider APIs underneath, with per-provider retry and cross-model fallback, exposed as an OpenAI-compatible `/v1/chat/completions` endpoint.

**Requirements:**
- One unified request/response shape for the caller, regardless of which provider actually serves it.
- Real translation to and from at least two different providers' actual wire formats (not two instances of the same fake format).
- Route by a `"<provider>/<model_name>"` string (LiteLLM's real routing convention).
- Retry transient failures per-provider; if a provider/model is exhausted or otherwise fails, fall back to the next configured model instead of failing the whole request.
- Full failure (every configured model failed) must be reported with detail on *why each one* failed, not just a generic error.

## Constraints

- The Anthropic and OpenAI translations must be faithful to their real, documented request/response shapes — including the parts that actually differ (system prompt handling, required fields, auth header shape, response content structure) — not simplified into one shared internal shape that happens to look like OpenAI's.
- A non-retryable failure (e.g. a 400) on one candidate must not consume that candidate's full retry budget before moving to the next fallback — retrying a client error wastes time for no benefit.
- One candidate's failure — of any kind, including a class of exception the code didn't specifically anticipate — must never take down the whole request if there's still a fallback candidate left to try.

## Approach

1. **Route on a `provider/model` string, resolve to an adapter, delegate.** `LLMGateway._resolve()` splits the string, looks up the registered `ProviderAdapter` for that provider prefix, and everything downstream just calls `adapter.complete(model_name, messages, max_tokens, temperature)` against one common `ProviderAdapter` protocol — the same "swap the implementation behind one interface" shape as the `LLMClient` protocol in the tool-calling agent exercise, applied here to whole providers instead of one model.

2. **Each adapter owns real, provider-specific translation, not a shared abstraction that quietly assumes OpenAI's shape is universal.** `AnthropicAdapter` pulls system-role messages out of the message list into Anthropic's required top-level `system` field, always sets `max_tokens` (required by Anthropic, optional by convention for OpenAI), authenticates via `x-api-key`/`anthropic-version` instead of `Authorization: Bearer`, and parses a list of typed content blocks instead of a single message string — every one of these is a genuine, documented difference between the two APIs, not an invented complication.

3. **Retry and fallback are two different mechanisms answering two different questions.** Retry (reusing the exponential-backoff-with-jitter design from the standalone retry exercise, kept self-contained here) answers "is this specific call to this specific model worth trying again" — for transient failures (timeouts, connection errors, 5xx, 429) on the *same* candidate. Fallback answers "should we give up on this model/provider entirely and try a different one" — for anything else, including retry exhaustion, an unconfigured provider, and any exception at all that isn't explicitly classified. Conflating these would either waste a full retry budget on a hopeless client error, or give up on a model too early after one transient blip.

4. **The fallback loop's exception handling is deliberately broad** (`except Exception`, not a specific list) at exactly one place: isolating one candidate's failure from the rest of the chain. This isn't sloppy error handling — it's the correct boundary for "no matter what goes wrong with this one candidate, record it and try the next" — and it was validated as necessary, not just theoretical, by a real finding during testing (see notes.md): a live, unauthenticated call surfaced an `httpx.LocalProtocolError` (a malformed-header client-side error, not a server response) that neither adapter's code anticipated, and the broad boundary caught it correctly anyway.

5. **Tested against realistic, documented provider response fixtures over `httpx.MockTransport`**, not Python-level mocking of internal functions — this exercises the real HTTP request-building and JSON serialization/deserialization path, so a bug in how a request is actually built (e.g. forgetting to strip the system message from Anthropic's `messages` array) would be caught by inspecting the request MockTransport actually received, not just by trusting the code reads correctly. Additionally smoke-tested against the real, live OpenAI and Anthropic endpoints (unauthenticated, no API keys in this environment) to see genuine failure behavior, not just simulated failure.
