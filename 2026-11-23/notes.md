## Implementation

- `charge` checks membership and raises `IdempotencyKeyConflict` *before* ever touching `self.ledger` -- a conflicting reuse of a key must never execute the side effect, only a genuinely new key or a genuinely matching retry may. Mutation testing confirmed this ordering matters: recording/executing before the membership check turned every dedup and conflict test into a failure, because the real charge had already happened by the time the check ran.
- The conflict check compares **both** `account_id` and `amount` against the stored record, not just one -- an early version of this reasoning (caught by mutation, not by inspection) would have let a key reused with the same amount but a different account silently return the *original* account's `charge_id`, which is a far worse bug than simply failing to detect a conflict: it would make the response actively lie about which account was charged.
- `ttl` is inclusive, matching 2026-11-19's webhook freshness-window convention exactly: `now - ts > self.ttl` evicts, so a record exactly `ttl` old is still valid. This boundary was initially untested -- a `>` vs `>=` mutation passed every test on the first mutation pass, which is what prompted adding the exact-boundary test rather than assuming the convention carried over correctly just because it was copied from the right place.
- `_evict_stale` runs unconditionally at the start of every `charge` call, before checking the current key -- so eviction isn't a separate maintenance task that could be forgotten or scheduled wrong, it's a normal part of handling any single request.
- Not built: a durable store surviving process restart, concurrent/in-flight request handling, dynamic `ttl` tied to a client's actual retry window -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `Ledger.execute_charge` | O(1) | O(1) per charge |
| `NaiveChargeService.charge` | O(1) | O(1) |
| `IdempotentChargeService.charge` | O(number of currently-remembered keys), for eviction | O(number of currently-remembered keys) |

## Measured Results

- **The headline gap, exact counts**: 5 identical retries produce **5** real charges (5 distinct `charge_id`s) through `NaiveChargeService`, and exactly **1** real charge (1 `charge_id`, returned 5 times) through `IdempotentChargeService` -- confirmed by running both against the same inputs before writing either number into a test.
- **Conflict detection is a real, falsifiable capability gap, not just a nicety**: reusing a key with a different `amount` or a different `account_id` raises `IdempotencyKeyConflict` and executes zero charges for the conflicting attempt, through `IdempotentChargeService`. `NaiveChargeService` has no mechanism to even notice the same scenario -- it silently executes two unrelated real charges.
- **Bounded memory under continuous traffic, confirmed directly**: 50 distinct keys, each spaced `ttl + 1` apart, keep `store_size` at exactly 1 throughout.
- **The honest, measured cost 2026-11-19 didn't have**: a single key, charged once, replayed after the clock advances well past `ttl`, produces **2** real charges, not 1 -- the late retry executes as a brand-new request, because nothing besides the (now-evicted) idempotency record itself would have recognized it. A replay at or before the exact `ttl` boundary still correctly deduplicates (1 charge).
- **11 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
"Use idempotency keys" is common advice for payment-style APIs, but the actual, specific failure it prevents -- a client that legitimately retries after a dropped response, not a buggy client hammering the same button -- is worth measuring directly rather than taking on faith, especially since the fix (a dict keyed by idempotency key) looks almost too simple to be the whole story.

**How exactly?**
The naive service treats `idempotency_key` as inert metadata -- it's accepted as a parameter and never read. The idempotent service inverts that: the key is the *only* thing that decides whether `ledger.execute_charge` ever runs. Everything else (the account, the amount) is only used to detect a conflict once a key is already known, never to decide whether to execute.

**What happens internally?**
`_evict_stale` is called on every single request, not on a timer or a background thread -- there's no separate eviction process to go stale or crash independently of request handling. The cost of that is `charge` becomes O(current store size) instead of O(1), which is the same trade `_evict_stale` makes in 2026-11-19's `WebhookVerifier`; the difference here is entirely in what eviction *means* afterward, not in how it's implemented.

**What if this fails?**
- **A client's retry arrives after its idempotency record has already been evicted**: `test_late_replay_after_ttl_expiry_executes_as_a_genuine_duplicate_charge` is the measured version of exactly this -- the system has no way to distinguish it from a new request, and it isn't supposed to pretend otherwise.
- **Two unrelated requests collide on the same key** (a client-side UUID bug, or a deliberate replay with a tampered amount): caught by `IdempotencyKeyConflict`, never silently merged into either request's result.
- **Someone assumes the TTL boundary is exclusive because that's a common convention elsewhere**: `test_replay_at_exact_ttl_boundary_still_deduplicates` pins the actual, inclusive behavior directly, rather than leaving it to be rediscovered by whoever next changes `ttl`'s meaning.

**What trade-offs did you consider?**
- **A fixed `ttl` chosen by the service (as modeled here) vs. a `ttl` tied to the client's actual maximum retry window**: a fixed value is simpler and is what's actually implemented, but `problem.md`'s Stretch section notes the real engineering question is matching `ttl` to how long a client might plausibly still retry -- too short, and Approach 4's cost becomes common; too long, and the store grows larger than it needs to.
- **In-memory dict (chosen) vs. a durable store**: the in-memory version is sufficient to measure the actual claims this exercise cares about (retry deduplication, conflict detection, bounded memory, the eviction cost) without needing a database dependency, but it can't survive a process restart -- a retry arriving right after a restart would be treated as new regardless of `ttl`, a second, distinct failure mode from the one this exercise measures.

**How do you evaluate it?**
- **11 tests, 3/3 consecutive clean runs.**
- **Mutation check, 4 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - Conflict check drops the `account_id` comparison, checks only `amount`: 1/11 failed (`test_idempotent_service_raises_on_key_reuse_with_different_account`), exactly the test built to catch it.
  - Eviction boundary changed from `>` to `>=` (evicts one tick earlier than intended): 0/11 failed on first attempt -- no existing test exercised the exact `now - ts == ttl` boundary; `test_replay_at_exact_ttl_boundary_still_deduplicates` was added afterward specifically to close this, and with it present, 1/11 failed, exactly the new one.
  - `charge` executes the real charge *before* checking key membership, instead of after: 5/11 failed -- every test that depends on deduplication, conflict detection, or the late-replay distinction, since the side effect now always runs regardless of what the membership check would have said.

## Key Learnings

- **Copying a convention from a prior day (TTL inclusivity, from 2026-11-19) doesn't carry its test coverage along with it.** The boundary behavior was implemented correctly by habit, but nothing in this day's own test suite actually exercised the exact boundary until a mutation revealed the gap -- a reminder that "I did this correctly last time" and "I tested that this is correct this time" are different claims, and only the second one survives a mutation that targets exactly the untested edge.
- **The two system-design days built around TTL eviction (2026-11-19, this one) look structurally identical but differ in the one fact that actually matters: whether a second, independent check protects what eviction removes.** Webhook verification has one (the timestamp freshness check); idempotency-key-based deduplication doesn't (there's no separate signal that says "this is a retry" once the record itself is gone) -- which is exactly why this exercise's Approach 4 needed to be a measured result instead of an inherited assumption from the prior day.
- Consistent with every prior mutation-testing day: a mutation surviving on the first attempt points at a specific untested property, and the fix is a small, targeted test aimed at that exact boundary -- not a larger pile of scenario tests shaped like the ones that already exist.
