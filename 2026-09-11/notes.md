## Implementation

- `GenerateRequest` (Pydantic) validates `prompt` (1-8000 chars, rejected if blank/whitespace-only via a `field_validator`), optional `system_prompt`/`model` overrides, and bounded `temperature`/`max_tokens` — all rejected with a `422` before `/generate`'s body even runs, via FastAPI's automatic request validation.
- `build_chain(model, temperature, max_tokens)` constructs a fresh `ChatOpenAI` + `ChatPromptTemplate` + `StrOutputParser` LCEL pipeline per call (not a module-level singleton), which is what makes per-request `model`/`temperature`/`max_tokens` overrides possible without any shared mutable state between concurrent requests.
- `generate()` builds the chain *inside* the `try` block, not before it — this was a real bug I caught while testing: `ChatOpenAI(...)` raises `openai.OpenAIError` immediately (at construction, not at call time) if no API key is configured, so building it outside the try meant a missing-credentials misconfiguration would have leaked as an unhandled exception instead of the same clean, logged, typed error path as every other failure.
- Exception handling is ordered most-specific-first: `AuthenticationError` and `RateLimitError` (both subclass `APIStatusError`) are caught before the general `APIStatusError` catch-all; `APITimeoutError` (subclasses `APIConnectionError`) is caught before the general `APIConnectionError` catch-all; a final `openai.OpenAIError` catches anything else from the SDK, including the construction-time missing-credentials case. Order matters here — Python's `except` matches the first applicable clause, so a more general exception type listed before a more specific subclass would silently swallow it into the wrong branch.
- `lifespan()` logs a warning at startup if `OPENAI_API_KEY` isn't set, but deliberately doesn't fail startup — the service still starts and serves `/healthz` correctly; only `/generate` calls fail, with a clear error. This means a load balancer's health check still passes even in a misconfigured deployment, which is arguably itself a trade-off worth naming (see below).
- `/healthz` is intentionally trivial (no downstream check) — it answers "is the process up and serving," which is what an ALB/ECS health check needs to decide whether to route traffic to this task, not "is OpenAI currently reachable" (a downstream dependency check would make this service's own health falsely depend on OpenAI's availability).
- Edge case handled: `test_generate_without_api_key_returns_clean_500` exercises the real code path (no mocking) since it needs no network access — confirming the fix above actually works, not just that the code compiles.
- Not handled (explicitly out of scope): no per-request rate limiting or auth on this endpoint itself (anyone who can reach it can spend the configured OpenAI budget) — layering in [[redis-rate-limiter]] in front of this endpoint would be the natural next step for a real deployment, not attempted here to keep today's scope to the LangChain/FastAPI/Docker/Terraform chain itself.

## Complexity

- Time: O(1) local work per request; dominated entirely by the OpenAI API round-trip time, which this service doesn't control.
- Space: O(1) per request — no per-request state retained after the response is returned (no conversation memory, no session state).

## Follow-up Questions

**Why?**
A raw OpenAI SDK call embedded directly in an endpoint works for a demo, but doesn't scale as a codebase's prompting logic grows — templates, few-shot examples, output parsing, and eventually multi-step chains/agents all need one composable place to live. LangChain's `Runnable`/LCEL abstraction is exactly that composition layer, and wrapping it behind a FastAPI service is the standard shape of a "model gateway" microservice that other internal services call instead of each one holding its own OpenAI credentials and prompt logic.

**How exactly?**
A client POSTs `{"prompt": "..."}` to `/generate`. FastAPI validates the body against `GenerateRequest`. The handler builds an LCEL chain (`prompt | llm | parser`), calls `await chain.ainvoke(...)` (non-blocking on the network wait), and either returns a `GenerateResponse` or maps whatever `openai` exception surfaced to an `HTTPException` with an appropriate status code.

**Which algorithm?**
None in the classical sense — this is service composition and error-mapping, not computation. The one deliberate "algorithmic" structure is the exception-hierarchy walk (most specific to least specific) used to classify a failure into the right HTTP status.

**Which library?**
`fastapi` for the HTTP layer (async-native, Pydantic-based validation, automatic OpenAPI docs), `langchain-core`/`langchain-openai` for the prompt/model/parser composition, and the `openai` package indirectly (as `langchain-openai`'s underlying client) for the actual API calls and its typed exception hierarchy, which this service's error handling depends on directly.

**What happens internally?**
`chain.ainvoke(...)` on `prompt | llm | parser` runs each `Runnable` in sequence: the prompt template formats `{system_prompt}`/`{prompt}` into a list of chat messages, `ChatOpenAI` serializes those into an OpenAI chat-completions request body and awaits the HTTP response via its internal `httpx` client, and `StrOutputParser` extracts the plain text content from the returned message object. None of this blocks FastAPI's event loop — the `await` on the network call yields control back to serve other concurrent requests, the same underlying mechanism as [[async-multi-api-fetch]] and [[async-retry-backoff]].

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- OpenAI auth/config broken → `500` (a server-side misconfiguration, not the caller's fault; not something retrying will fix).
- OpenAI rate limit → `429` — a caller can reasonably retry this with backoff, which is exactly where wiring in [[async-retry-backoff]] at the *caller* of this endpoint (or inside `generate()` itself, wrapping the `ainvoke` call) would belong; not implemented today to keep this exercise's scope to the gateway service itself.
- OpenAI too slow → `504`, bounded by `REQUEST_TIMEOUT_SECONDS` passed into `ChatOpenAI` — without an explicit timeout, a hung upstream call could tie up a request indefinitely.
- OpenAI unreachable / other API error → `502` — signals "the problem is upstream," distinct from `500` ("the problem is us").
- `/healthz` doesn't fail even if OpenAI is completely unreachable, which means a load balancer will keep routing traffic to a task whose actual `/generate` calls are all failing. This is a real, named trade-off (see below), not an oversight.

**What trade-offs did you consider?**
- `/healthz` as liveness-only (chosen) vs. a readiness check that also probes OpenAI: probing OpenAI on every health check would add latency and cost to every check interval, and would make this service's reported health falsely depend on an external vendor's uptime — but it does mean a misconfigured or upstream-down deployment still looks "healthy" to the orchestrator. A production version might add a separate `/readyz` doing a cheap upstream check, rather than conflating it with `/healthz`.
- Terraform: default VPC + public-IP Fargate tasks (chosen for this exercise) vs. a dedicated VPC with private subnets and a NAT gateway for the tasks: the private-subnet version is the real production posture (tasks aren't directly internet-addressable), but a NAT gateway has an hourly cost and meaningfully more moving parts for what's meant to be a single-service demo stack — named explicitly as a scope simplification rather than presented as the production-ready shape.
- Terraform: OpenAI key passed as a Terraform variable and written via `aws_secretsmanager_secret_version` (chosen) vs. creating the secret out-of-band and only referencing its ARN via a data source: the chosen approach is simpler to apply end-to-end from one `terraform apply`, but it means the plaintext key passes through and is stored in Terraform state — state itself then needs to be encrypted and access-controlled (e.g. an S3 backend with SSE + a restrictive bucket policy) as a consequence. The out-of-band approach avoids the key ever touching state, at the cost of a manual step outside Terraform's control. Documented as an open trade-off in `variables.tf` itself, not just in these notes.
- Retry-at-the-gateway vs. retry-at-the-caller for OpenAI rate limits: this service surfaces a `429` rather than silently retrying rate-limited calls itself, so that the decision of whether/how to retry stays with whoever is calling this gateway (who may have their own budget/backoff policy) rather than this service making that call unilaterally and potentially compounding latency.

**How do you debug it?**
- `/healthz` plus the container's `HEALTHCHECK` (which curls `/healthz` via a Python one-liner, no extra `curl` binary needed in the slim image) is the first signal — confirmed both via `docker inspect`'s health status and by watching it transition from `starting` to `healthy` in this environment.
- The structured logging in `generate()` logs the *type* of OpenAI failure at the point it's caught (`logger.error`/`logger.warning` with the exception), which is what a real deployment's CloudWatch log group (wired up in the Terraform) would surface — this is the actual debugging surface once deployed, not step-through debugging.
- `test_solution.py`'s parametrized error-mapping tests are themselves a debugging tool: if a future `openai` SDK upgrade reshuffles the exception hierarchy (a real risk — SDK exception hierarchies do change across major versions), these tests would fail immediately and precisely, pointing at exactly which status mapping broke.
- For the Terraform side, `terraform validate` catches type/reference errors without needing AWS credentials at all, and `terraform plan` (even without valid credentials) confirms the configuration gets *past* all local validation and only fails at the AWS API boundary — a useful signal that the HCL itself, not just its syntax, is coherent.

**How do you evaluate it?**
- Request validation: blank prompt and out-of-range temperature both correctly return `422` before any OpenAI call is attempted (verified).
- Missing-credentials path: verified for real (no mocking, no network needed) — both via `pytest` and via actually running the container without `OPENAI_API_KEY` set and confirming the same clean `500` over real HTTP.
- Success path and all error-status mappings: verified via mocking `build_chain` with a stub `Runnable`, since no real OpenAI API key was available in this environment — 9/9 tests pass, covering the happy path and four distinct `openai` exception types mapped to their intended status codes.
- Docker: image builds, runs as a non-root user (`uid=1000`, confirmed via `docker exec ... id`), its `HEALTHCHECK` transitions to `healthy`, `/healthz` and `/generate` both respond correctly over a real published port, and passing `OPENAI_API_KEY` via `-e` at `docker run` time is correctly picked up (confirmed by the missing-key startup warning *not* appearing) — proving the "inject at runtime, never bake in" design actually works, not just that it's intended.
- Terraform: `fmt`, `init`, and `validate` all pass; `plan` progresses past all local configuration checks and fails only at the AWS credentials boundary — the furthest this can be verified without a real AWS account, and stated as exactly that rather than claimed as "tested."
- Not evaluated here, and explicitly named as gaps: an actual successful call to the real OpenAI API (no API key available in this environment), and any real `terraform apply` against live AWS infrastructure.

## Key Learnings

- Reaching for a mocking seam (`build_chain`) exposed a real bug — the missing-credentials construction call sitting outside the try/except — that reading the code alone hadn't caught. Writing the "no API key" test case specifically, rather than only testing the mocked happy/error paths, is what surfaced it.
- LangChain earns its place here specifically because of the *composition* (prompt template + model + parser as one `Runnable`), not because it wraps the OpenAI SDK — a plain SDK call would have been simpler for exactly this one endpoint, and the honest answer to "why LangChain" has to be about where the codebase is going (more templates, parsers, eventual chains/agents), not about this one endpoint in isolation.
- Infrastructure-as-code can be meaningfully verified in stages even without the target cloud account: `validate` proves internal consistency, `plan` (even failing on auth) proves the configuration is coherent enough to reach the provider API boundary — both are real, honest checkpoints short of a full `apply`, worth doing rather than skipping just because deployment itself isn't possible here.
