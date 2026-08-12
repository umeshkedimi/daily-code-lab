# Problem

**Category:** `backend`

## Statement

Create a `Bird` class.

**Requirements:**
- Two fields: `image`, `fact`.
- Call an external REST API to populate both fields.
- Instantiate the class 3 times.
- Store the objects in a list.
- Print the results.

## Constraints

- Data must come from a real external API call, not hardcoded/mocked values.
- Each instance should make its own independent API call (not share one response across all three).

## Approach

1. Find a public REST API that returns a bird image URL and a bird fact in a single response — avoids juggling two separate calls/fields to stitch together. `some-random-api.com/animal/birb` returns exactly `{"image": ..., "fact": ...}`, which maps directly onto the two required fields.
2. Design `Bird.__init__` to call the API immediately on construction ("fetch on init") so that after `Bird()` returns, both fields are guaranteed populated — no separate `.load()` step to forget.
3. Use the `requests` library for the HTTP call: handles JSON decoding, timeouts, and status-code checking with less boilerplate than `urllib`.
4. Build a list via `[Bird() for _ in range(3)]`, then iterate and print `image`/`fact` for each.
5. Discovered mid-implementation: the API's Cloudflare edge blocks requests carrying a generic/library User-Agent (`python-requests/x.x`) with a 403. Fixed by sending an explicit browser-like `User-Agent` header.
