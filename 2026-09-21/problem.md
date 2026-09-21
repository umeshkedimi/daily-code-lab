# Problem

**Category:** `security` / `backend`

## Statement

Build an **SSRF-safe URL fetcher**: a function that GETs a caller-supplied URL and cannot be turned against internal resources.

**SSRF (server-side request forgery)** is when an attacker supplies a URL and *your server* fetches it from inside your network — reaching things the attacker cannot, such as cloud instance-metadata credentials at `169.254.169.254`, internal admin panels, or localhost-only services. Any feature that fetches a user-supplied URL has this exposure: webhooks, link previews, image proxies, HTML/PDF renderers, and LLM-agent "fetch this page" tools (where the URL can arrive via prompt injection).

**Requirements:**
- Fetch public `http(s)` URLs normally.
- Refuse requests that would reach loopback, private, link-local, metadata, or otherwise non-public addresses — however the address is spelled or reached.
- Stay safe against the standard bypass classes: obfuscated IP encodings, attacker-controlled DNS, DNS rebinding, redirects, URL-parser confusion, non-HTTP schemes.
- Be a safe *client* too: bounded response size, redirect count, and total time.

## Constraints

- Judge the **resolved IP address**, never the hostname text — text-based checks are defeated by encodings and by DNS names that point at internal IPs.
- The IP that was validated must be the IP that is connected to (no second resolution between check and use).
- Every redirect hop is untrusted input and gets the full check again.
- Legitimate internal targets must be expressible as an explicit, narrow opt-in — and must not be able to re-open cloud-metadata addresses.
- Tests must assert the security *property* (the internal service received no request), not merely that an exception occurred.

## Approach

Defense in layers, where each layer exists because a specific bypass beats the ones beneath it:

1. **Strict single-parser URL handling.** Allowlist scheme (`http`/`https`) and port; reject userinfo (`good.com@evil.com`), backslashes, whitespace/control characters (header injection), and odd hostname characters; normalize IDNA. Parser differentials — the validator and the requester disagreeing about what host a URL names — are a whole bug class, so there is exactly one parser and its output is what gets used.
2. **Resolve ourselves; judge every resolved address.** The OS resolver expands `2130706433`, `0x7f.1`, `127.1`, and `0` to loopback/unspecified, and attacker DNS can point any name anywhere. All answers must pass — a name mixing public and internal records is rejected outright. `ipaddress.is_global` is a starting point, not the answer (see notes: it is `True` for Azure's platform address, multicast, and several IPv6 encodings of loopback), so a deny-list applies first and always wins.
3. **Pin the connection to the validated IP.** Connect to the address that passed validation while keeping the original hostname for the `Host` header and TLS verification. This closes DNS rebinding (the attacker's second DNS answer never gets a chance to matter) without weakening certificate checks.
4. **Manual redirects, re-validated per hop.** Auto-following is how "public URL → 302 → `http://169.254.169.254/`" works. Bounded hop count; loops terminate.
5. **Bound the client's own resource use.** Response-size cap (checked against the declared `Content-Length` *and* enforced while streaming, since a server can lie or omit it), redirect cap, and a total-time budget enforced by a watchdog — per-socket timeouts alone don't bound total time against a peer that dribbles bytes.

To make each layer's necessity concrete, two deliberately flawed baselines are built and attacked alongside it: a **substring blocklist** (the most common real-world attempt) and a **check-then-fetch** (a correct check on the first URL only, then a normal fetch that re-resolves and follows redirects). The demo attacks all three against a local "internal service" and prints who leaks.

**Scope and safety of the exercise:** every attack targets servers this process starts on `127.0.0.1`; a fake DNS/routing layer stands in for "the internet" so rebinding and redirect attacks are reproducible offline. Nothing external is contacted.
