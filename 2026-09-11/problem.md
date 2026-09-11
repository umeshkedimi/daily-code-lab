# Problem

**Category:** `genai-agentic` / `backend`

## Statement

Write a **FastAPI endpoint that calls an OpenAI model through a LangChain integration**, plus a **Dockerfile** and **Terraform** to deploy it. (Framed as an interview question: implement the service, then show how you'd containerize and deploy it.)

**Requirements:**
- A FastAPI endpoint (`POST /generate`) that takes a prompt and returns the model's response.
- Actually use LangChain to talk to OpenAI, not just call the raw OpenAI SDK.
- A Dockerfile to containerize the service.
- Terraform to deploy the container to real infrastructure.

## Constraints

- The API key must never be baked into the Docker image or committed anywhere — injected at runtime only.
- Errors from the OpenAI call (auth, rate limit, timeout, bad request, unreachable) must map to sensible, distinct HTTP status codes for the endpoint's own caller — not leak a raw stack trace or a generic 500 for everything.
- Input must be validated (non-blank prompt, bounded length, sane temperature/token ranges) before any call to OpenAI is attempted.
- The Docker image must run as a non-root user and expose a health check.
- The Terraform must at least be internally consistent (type-correct, all references resolvable) even without a real AWS account to apply it against.

## Approach

1. **LangChain's actual value here is the composition, not the wrapper.** `ChatOpenAI` alone is barely different from calling the OpenAI SDK directly. What LangChain (specifically LCEL — the `prompt | llm | parser` pipe syntax) buys is a standard place to compose a prompt template with a model and an output parser as one `Runnable` — a structure that stays the same if the model swapped from OpenAI to a different provider tomorrow. `build_chain()` returns exactly that pipeline: a `ChatPromptTemplate` (separating system/user content) piped into `ChatOpenAI` piped into `StrOutputParser` (unwraps the model's message object into a plain string).

2. **Map the real `openai` SDK exception hierarchy to HTTP status codes, deliberately, not generically.** `AuthenticationError`/`RateLimitError`/`APITimeoutError`/`APIStatusError`/`APIConnectionError` are all distinct, typed exceptions in the `openai` package — each maps to a different HTTP status because each means something different to *this* endpoint's own caller (a 500 for "we're misconfigured," a 429 for "back off and retry," a 504 for "upstream was too slow," a 502 for "upstream broke or was unreachable").

3. **Catch construction failures, not just call failures.** `ChatOpenAI(...)` raises immediately if no API key is configured — before any network call happens. That construction call has to live *inside* the same try/except as the actual `ainvoke()` call, or a missing-credentials misconfiguration leaks as an unhandled exception instead of the same clean, logged error path as every other failure mode. (Caught this exact gap by testing rather than assuming the happy path was the only path through the code — see notes.md.)

4. **Test the endpoint's plumbing without spending real API calls.** No OpenAI API key is available in this environment, so `build_chain` is the mocking seam: tests substitute a stub `Runnable` to verify request validation, response shape, and — critically — that each `openai` exception type really does map to the intended HTTP status, all without hitting the network. The one thing tested *for real* is the missing-API-key path, since that needs no network access at all and is exactly the bug the mocking approach caught.

5. **Docker: dependency layer separated from code layer, non-root user, no baked-in secret, and a real health check** — built and actually run in this environment to confirm it works, not just written and assumed correct (see notes.md for what was verified).

6. **Terraform: ECS Fargate behind an ALB, secret injected via Secrets Manager at container-start** — scoped to a single-service demo (default VPC, public-IP tasks, no NAT gateway) rather than a full production network topology, with that simplification named explicitly rather than silently assumed. Validated with `terraform init`/`validate`/`plan` in this environment (no AWS account available, so it was confirmed structurally correct up to the point of needing real AWS credentials, not applied).
