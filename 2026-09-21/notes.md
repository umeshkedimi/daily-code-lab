## Implementation

- **`parse_url`** — one strict parser whose output (`Target`: scheme, host, port, request-target) is the only thing later stages use. Rejects: non-`http(s)` schemes (`file`, `gopher`, `ftp`, `dict`, `data`, `javascript`), any `@` in the authority (userinfo), backslashes, whitespace/control characters, ports outside the allowlist, empty hosts, and hostnames outside `[a-z0-9_.-]` after IDNA normalization. Non-ASCII paths are percent-encoded so `http.client` doesn't choke on them.
- **`blocked_reason(ip)`** — order matters: (1) unwrap IPv4-mapped IPv6 and judge the inner address; (2) **deny-list wins**; (3) `extra_allowed_networks` opt-ins; (4) fall back to `is_global`. Deny-list: cloud metadata (`169.254.169.254`, `fd00:ec2::254`, Azure `168.63.129.16`, Alibaba `100.100.100.200`), `192.0.0.0/24`, multicast, and the IPv6 forms that embed IPv4 (`::/96`, NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`, 6to4 `2002::/16`, Teredo `2001::/32`).
- **`resolve_and_validate`** — resolves via an injectable resolver and requires **every** returned address to pass, not just the one that will be dialed.
- **`_open_connection` (pinning)** — builds an `http.client` connection for the *hostname* (so `Host` and TLS SNI/verification use the name) but replaces its `_create_connection` hook with a closure that dials a validated IP. Tries validated IPs in order; each attempt's timeout is capped by the remaining total budget.
- **`safe_fetch`** — loops over at most `max_redirects + 1` hops; each hop runs parse → resolve/validate → pinned connect from scratch. Redirects are followed by hand (`urljoin` for relative `Location`s). A 3xx without `Location` is returned as a normal response.
- **`_read_capped` + watchdog** — rejects an oversized declared `Content-Length` before reading, then reads with `read1` and enforces the cap while streaming (a server can lie or omit the header). A `threading.Timer` shuts the socket down at the total deadline so *any* blocked read (TLS, status line, headers, body) returns; a flag distinguishes that from a clean EOF, and a `Content-Length` shortfall is an explicit error.
- **Baselines** — `substring_blocklist_fetch` (judges URL text, then `urlopen`) and `check_then_fetch` (the same good check as the real thing, on the first URL only, then `urlopen`, which re-resolves and auto-follows redirects).
- **`labnet.py`** — `LabServer`/`RawServer` (local HTTP and raw-TCP servers recording every hit) and `FakeNetwork` (scripted DNS where the Nth lookup can return the Nth answer, IP→local-server routing, and a context manager that patches `socket` process-wide so `urllib` is subject to it too). Names not scripted fall through to the real OS resolver, so obfuscated IP forms get the OS's genuine interpretation.

Not handled (by design): the resolver call itself is not bounded by `total_timeout` (`getaddrinfo` has no timeout), no async variant, no proxy support, no response-content validation, and no protection outside the process (see follow-ups).

## Complexity

- Time: at most `max_redirects + 1` hops; each hop is one resolution, ≤ (number of validated IPs) connect attempts, one request, and at most `max_body_bytes` read. Total wall-clock is bounded by `total_timeout` (except the resolver — see above).
- Space: O(`max_body_bytes`) — the body is buffered up to the cap and never beyond.
- Validation itself is O(deny-list size × addresses) — a few dozen comparisons.

## Follow-up Questions

**Why?**
SSRF turns your server into a proxy that sits *inside* the trust boundary. The payoff for an attacker is usually cloud-metadata credentials (short-lived IAM keys), internal admin/actuator endpoints, or localhost-only services that have no auth because they were "internal". It's a recurring top-tier web risk because the vulnerable feature ("fetch this URL for me") looks harmless. LLM agents with a generic URL-fetch tool add a new route to it: the URL can originate from prompt-injected page content, not from the user.

**How exactly?**
Layered checks (parse strictly → resolve → judge resolved IPs → pin the connection → re-check every redirect hop), plus resource limits. See Approach in `problem.md`.

**Which algorithm?**
No algorithm as such — it's a validation pipeline. The core technique is *allowlist-by-resolved-address with pinning*: decide on what the request will actually connect to, and make sure nothing can change that decision afterwards.

**Which library?**
Standard library only: `ipaddress` (classification), `urllib.parse` (parsing), `http.client` (HTTP/1.1 with the connection hook that makes pinning possible), `socket`/`ssl`, `threading` (watchdog). Deliberately not `requests`/`httpx`: pinning an IP while preserving Host/SNI needs control of connection establishment that those libraries don't expose cleanly, and `requests` additionally honors proxy environment variables and follows redirects itself — both places a validated request can be silently rerouted.

**What happens internally?**
`http.client.HTTPConnection.connect()` calls `self._create_connection((self.host, self.port), timeout, source_address)`, which by default resolves the name and connects. Swapping that callable for one that dials a fixed IP means the socket goes to the validated address, while `self.host` remains the name — so `Host:` is the name, and for HTTPS `wrap_socket(..., server_hostname=self.host)` verifies the certificate against the name. Verified with a TLS lab server: the pinned connection succeeds for a cert covering `public.test` and fails (`FetchError`) when the URL names a host the cert doesn't cover, even though the IP is identical.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- *Validation bug*: the layers overlap on purpose — e.g. even if a hostname trick slipped past the parser, the resolved-IP check still applies; even if resolution were fooled at check time, pinning means the connection can't go elsewhere.
- *Private hook changes*: `_create_connection` is a private `http.client` attribute. If a future Python stops honoring it, requests would bypass the pinned connector. The test `test_fetch_connects_to_validated_ip_and_keeps_hostname_in_host_header` is the canary: it asserts the connector was actually used.
- *Slow/hostile server*: covered by the size cap, redirect cap, total-time watchdog and truncation check (all tested).
- *Slow/hostile DNS*: **not covered** — `getaddrinfo` blocks with no timeout parameter, so a stalled resolver can exceed `total_timeout`. Fix would be running resolution in a worker with a deadline.
- *Defense in depth outside the app*: none here. If this function has a bug, only network-level controls help.

**What trade-offs did you consider?**
- *Deny-list + `is_global` vs. allow-list of public ranges.* `is_global` is the safer default but is not sufficient alone (below); a deny-list catches its holes. An explicit allow-list of the public IPv4 space would be more auditable but must track IANA changes.
- *Reject vs. try to sanitize.* Userinfo, backslashes, control characters are rejected rather than normalized; this fails a few odd-but-legal URLs to avoid guessing which parser the target uses.
- *Require all DNS answers to pass vs. any/first.* All — stricter, and it stops a hostile name from mixing a public record with an internal one; the cost is rejecting misconfigured-but-innocent DNS.
- *Hand-rolled client (stdlib) vs. `requests`.* More code and a private-attribute dependency, in exchange for control over exactly where the connection goes.
- *Watchdog thread vs. per-read timeouts only.* A daemon `Timer` per hop is cheap and covers every blocking phase; per-recv timeouts alone don't bound total time.
- *Allowing an internal target.* `extra_allowed_networks` is explicit and narrow, and the deny-list still wins, so "allow 10.0.0.0/8" can't accidentally re-open metadata.

**How do you debug it?**
- Errors name the reason: `'attacker.test' resolves to 127.0.0.1: non-global address …` vs `port 22 not allowed` vs `total time limit exceeded`; the exception *type* separates policy blocks (`BlockedURLError`) from ordinary failures (`FetchError`) — the demo relies on this so "blocked" never quietly means "DNS failed".
- `FetchResult.chain` lists every hop; `FetchResult.connected_ip` shows which validated address was actually dialed.
- In tests, the lab servers' `.hits` and `FakeNetwork.connected_ips` are ground truth for "was anything contacted".

**How do you evaluate it?**
- **Attack matrix (demo, 12 attacks vs 3 fetchers):** secrets leaked — substring blocklist **8/12**, check-then-fetch **2/12** (DNS rebinding and redirect), `safe_fetch` **0/12**; and `safe_fetch` never opened a connection to the internal service in any of them. Rebinding against `safe_fetch` returns the harmless public page, because the connection is pinned to the validated IP.
- **110 tests, ~5 s**, run 12 consecutive times with no failures. Categories: address classification (32 forbidden + 5 allowed + opt-in/deny-precedence), 24 hostile-URL parses, loopback in 12 spellings (plus 3 strict cases) (invariant: no connection, internal service never hit), redirect handling per hop, size/time/truncation limits, IP failover, HTTPS pinning, plus **negative controls** — the baselines are asserted to actually leak, so a passing `safe_fetch` test can't be vacuous — and a replay of the entire demo attack list as a regression test.
- **Not evaluated:** Linux/glibc resolver behavior, a real (non-simulated) rebinding DNS server, IPv6-only networks, and behavior behind an HTTP proxy.

## Key Learnings

- **`ipaddress.is_global` is not a complete SSRF gate.** Checked on Python 3.9.6: it returns `True` for Azure's platform address `168.63.129.16`, for multicast, for `192.0.0.8`, and for the IPv6 encodings `::127.0.0.1`, `64:ff9b::7f00:1` and `2002:7f00:1::` — the last three all embed loopback. Hence the explicit deny-list.
- **Judge what will be connected to, not what was typed.** The OS resolver (macOS here) turns `2130706433`, `0x7f.1`, `127.1` and `0` into loopback/unspecified, so text filters lose; OS behavior also differs — `0177.0.0.1` resolved to `177.0.0.1` on this machine, while glibc's `inet_aton` reads a leading `0` as octal (from documented behavior; not tested here). The same URL can mean different hosts on different systems, which is one more reason to validate the resolved address.
- **A correct check is not enough without pinning.** `check_then_fetch` uses the identical parser and IP validation as the real fetcher and still leaks twice — it validates one lookup and connects using another (DNS rebinding), and lets the HTTP library follow redirects unchecked.
- **My own fetcher had a slowloris hole, found by a test that passed for the wrong reason.** The first version of the slow-drip test "passed" (the right exception was raised) but took ~10 s against a 1 s limit — the timing assertion caught it. Cause: `resp.read(8192)` blocks until 8192 bytes or EOF, so a server dripping a byte every 0.2 s never trips per-recv timeouts and never reaches the loop's deadline check. Measured directly: one `read(8192)` blocked **2.8 s** on a 15-byte drip; `read1(8192)` returned in **0.00 s**. Header-dripping needs a different fix (the loop can't run while `http.client` is inside header parsing), hence the watchdog. Asserting on *time*, not just on the exception type, is what made this visible.
- **`http.client` silently accepts a body shorter than `Content-Length`.** Measured: server declared 100 bytes, sent 10; the read returned 10 bytes with no error and `resp.length == 90`. Nothing in the fetcher would have noticed. Now an explicit `truncated` error — and necessary anyway, because the watchdog's own socket shutdown looks exactly like that EOF.
- **Assert the security property, not the exception.** Every attack test checks `internal.hits == []` and which IPs were dialed. An exception can come from a DNS typo or a closed port while the defense is absent; the demo table likewise reports only `BlockedURLError` as "blocked" and everything else as "error (no leak)".
- **A defense is only shown to matter by an attack that beats its absence** — hence the baselines that are required to leak (negative controls).
- Housekeeping: the suite first took 47 s because `serve_forever`'s default 0.5 s poll made each of two server shutdowns per test cost ~0.5 s; a 20 ms poll brought it to ~5 s.
- **Do not rely on the app layer alone.** Egress filtering / network segmentation, IMDSv2 (session tokens plus hop limit) or disabling instance metadata where unused, and least-privilege credentials mean a missed bypass isn't game over.
