## Implementation

- `Bird.__init__` calls a private `_fetch()` method that does the HTTP GET, checks the status, decodes JSON, and assigns `self.image` / `self.fact`. Fetch-on-construct means there's no partially-initialized `Bird` floating around — it either fully succeeds or raises during construction.
- `response.raise_for_status()` converts HTTP error codes (4xx/5xx) into a `requests.HTTPError` immediately, instead of silently storing an error page as if it were bird data.
- A fixed `HEADERS` dict with a browser-style `User-Agent` is sent on every request — required because the API's edge (Cloudflare) 403s the default `python-requests/x.x` UA.
- `REQUEST_TIMEOUT = 10` bounds how long a single call can hang; without it, a stalled connection blocks the whole script indefinitely.
- Edge case handled: each `Bird()` call is a fully independent HTTP request, so three instances can (and did, in testing) get three different image/fact pairs — no accidental sharing of one response across instances.
- Edge case not handled (by design, for a 1-problem-scope exercise): retries on transient failure, response schema validation beyond `data["image"]`/`data["fact"]` key lookups (a `KeyError` is the deliberate failure mode if the API ever changes shape).

## Complexity

- Time: O(1) network calls per `Bird` instance → O(n) total for n instances (n=3 here). Dominated by network latency, not computation — there's no algorithmic component to this problem.
- Space: O(1) per instance (two string fields); O(n) for the list of n birds.

## Follow-up Questions

**Why?**
To practice the pattern behind almost every real backend integration: a class whose state is populated from an external, unreliable, network-bound source rather than from arguments passed in memory. This is the shape of any service client (payment gateway, weather service, third-party data enrichment).

**How exactly?**
`requests.get(url, headers=..., timeout=...)` opens a TCP connection, performs the TLS handshake, sends an HTTP GET with the given headers, and blocks until a response arrives or the timeout fires. `response.json()` reads the body and runs it through `json.loads`. The values are assigned directly to instance attributes — no transformation needed since the API's field names already match the class's field names.

**Which algorithm?**
None in the traditional sense — this is I/O orchestration, not computation. The closest thing to "algorithmic" here is the fetch-then-validate-then-assign control flow (call → check status → parse → extract → assign), which is a standard synchronous request/response pattern.

**Which library?**
`requests`, over the standard-library `urllib.request`. Trade-off considered below.

**What happens internally?**
`requests` builds on `urllib3`, which manages the underlying connection pool and socket. On `.get()`: a connection is opened (or reused from the pool), the request line/headers are written to the socket, the server's TLS cert is validated, the response is read and buffered, and `raise_for_status()` inspects `response.status_code` against the 4xx/5xx ranges. `.json()` then decodes the response body bytes to a `str` (using the charset from headers or a UTF-8 guess) and parses it with the `json` module into a Python `dict`.

**How is it implemented?**
See [Implementation](#implementation) above — fetch-on-construct via a private `_fetch()` method, called from `__init__`.

**What if this fails?**
- Network/DNS failure → `requests.ConnectionError`.
- Timeout exceeded → `requests.Timeout`.
- Non-2xx response (e.g. Cloudflare block, rate limit) → `requests.HTTPError` from `raise_for_status()`.
- Malformed/unexpected JSON body → `requests.exceptions.JSONDecodeError` or `KeyError` if `image`/`fact` are missing.
All of these currently propagate out of `Bird()` uncaught — acceptable for a script, not for a production class, which would need explicit handling (retry/backoff, default values, or a typed exception surfaced to the caller).

**What trade-offs did you consider?**
- `requests` vs `urllib.request`: `urllib` needs no extra dependency (stdlib), but requires manually setting headers via `Request`, manually checking `HTTPError`, and manually decoding JSON. `requests` costs a dependency but is far less boilerplate and is the de facto standard for HTTP in Python backend code — chose `requests` since real-world backend code overwhelmingly uses it.
- Fetch-on-construct vs. explicit `.load()` method: constructor-fetch guarantees the object is always valid once created (simpler for callers), at the cost of `__init__` being able to raise and doing non-trivial work (a construction-time side effect, which is debatable style but matches "populate both fields" as stated in the requirement).
- No retry logic: kept the scope to what the exercise asked for; a production client would wrap `_fetch()` in a retry-with-backoff for transient errors (timeouts, 5xx) but not for 4xx (client errors won't fix themselves on retry).

**How do you debug it?**
- Reproduce the exact failure with `curl -v <url>` outside Python to isolate network/API issues from code issues — this is how the Cloudflare 403 was diagnosed (curl with default UA also got blocked; adding `-A "Mozilla/5.0..."` fixed it, which pointed straight at the header being the cause rather than the code).
- Print/log `response.status_code` and `response.text` before calling `.json()` when the shape is in doubt, since a JSON-decode error alone doesn't say *what* was actually returned (often an HTML error page).
- Use `response.request.headers` to confirm what was actually sent over the wire, in case a header was silently overridden by the library defaults.

**How do you evaluate it?**
- Correctness: run the script multiple times and confirm all three birds get distinct, well-formed `image` (valid URL) and `fact` (non-empty string) values.
- Resilience: manually trigger each failure mode (wrong URL → `ConnectionError`; very low timeout, e.g. `timeout=0.001` → `Timeout`; wrong header → `HTTPError`) and confirm the class fails loudly and identifiably rather than storing garbage.
- No automated test suite in this exercise since it depends on a live third-party API — in production this would be evaluated with a mocked HTTP layer (e.g. `responses` or `unittest.mock`) to make the tests deterministic and offline.

## Key Learnings

- Cloudflare-protected APIs commonly block on User-Agent alone — always check this first when a "should work" API call returns 403 with no other explanation, before assuming the endpoint itself is wrong.
- `raise_for_status()` is the difference between "the code crashes clearly at the API boundary" and "the code silently stores an HTML error page as if it were data" — cheap insurance, should be a default habit on every `requests` call.
- Fetch-on-construct is a reasonable pattern when a class's whole reason to exist is wrapping one external call, but it embeds a network dependency into object creation — worth naming explicitly as a design choice, not a default.
