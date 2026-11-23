# Problem

**Category:** `system-design`

## Statement

Implement idempotency-key-based exactly-once-ish API semantics for a charge/payment-style endpoint, and measure the actual, concrete consequence of skipping it: under at-least-once delivery (a client that retries a request it never got a confirmed response for — the single most common reason idempotency keys exist in real payment APIs), a naive handler executes the underlying side effect once per retry; a correct one executes it exactly once, no matter how many times the identical request arrives.

This is also where the TTL-bounded-memory pattern from 2026-11-19's webhook verifier shows up again — but with a twist worth measuring honestly: there, evicting an old entry was *free*, because a separate freshness check would have rejected a replay of it anyway. Here, there is no such second check. Evicting an idempotency record to bound memory has a real, measured cost: a retry that arrives after its key has aged out is no longer recognized as a retry at all, and executes as a brand-new charge.

**Requirements:**

- `Ledger()` — `execute_charge(account_id: str, amount: int) -> str` appends `(account_id, amount)` to `.charges` and returns a fresh `charge_id` (`f"ch_{len(self.charges) + 1}"`). The thing being protected — every call is a real, irreversible side effect.
- `NaiveChargeService(ledger: Ledger)` — `charge(idempotency_key: str, account_id: str, amount: int) -> str` calls `ledger.execute_charge(account_id, amount)` directly, every time, ignoring `idempotency_key` entirely. Included specifically as the baseline that shows what "ignoring the key" actually costs.
- `IdempotencyKeyConflict(Exception)` — raised when the same `idempotency_key` is reused with a different `account_id` or `amount` than the request it was first associated with (two unrelated requests colliding on the same key is a client bug or an attack, not a legitimate retry, and must never be silently treated as one).
- `IdempotentChargeService(ledger: Ledger, clock: Callable[[], int], ttl: int)` — `charge(idempotency_key: str, account_id: str, amount: int) -> str`:
  1. Evict any remembered key whose recorded timestamp is now more than `ttl` away from the current clock time.
  2. If `idempotency_key` is still remembered: return the previously-recorded `charge_id` if `account_id` and `amount` match exactly; raise `IdempotencyKeyConflict` if either differs.
  3. Otherwise: execute the charge for real, remember `(now, account_id, amount, charge_id)` under `idempotency_key`, and return the new `charge_id`.
  4. `.store_size` — the number of currently-remembered keys, to measure that eviction actually bounds memory under continuous traffic.

## Constraints

- Standard library only.
- Unlike 2026-11-19's webhook verifier, eviction here is **not** backed by an independent check that would reject a late replay anyway — there is no timestamp embedded in the charge request itself, only in the idempotency record. A retry that outlives its own record's `ttl` is therefore genuinely indistinguishable from a brand-new request, and the exercise is required to measure that cost directly rather than assume it away.

## Approach

1. **The headline measured gap**: the identical `(idempotency_key, account_id, amount)` is submitted 5 times, simulating a client that retried because it never received a confirmed response. `NaiveChargeService` executes 5 real charges (`len(ledger.charges) == 5`, 5 distinct `charge_id`s); `IdempotentChargeService` executes exactly 1 (`len(ledger.charges) == 1`, all 5 returned `charge_id`s identical). Not "fewer" — exactly one, confirmed by running both before writing either number into a test.
2. **Key reuse with a different payload is a conflict, not a retry — detected, not guessed**: the same `idempotency_key` used once with `amount=500` and again with `amount=999` raises `IdempotencyKeyConflict` on the second call. `NaiveChargeService`, by construction, has no mechanism to even notice this — it just executes both as two unrelated real charges.
3. **Bounded memory under continuous traffic, confirmed directly**: 50 distinct keys, each submitted once and spaced `ttl + 1` apart, keep `store_size` at exactly 1 throughout — the same measured pattern as 2026-11-19, confirmed again here rather than assumed to still hold.
4. **The honest cost of that eviction, measured, not glossed over**: a single key is charged once, the clock is advanced well past `ttl`, and the identical request is resubmitted. The ledger now shows **2** real charges, not 1 — the late retry is executed as if it were new, because its own idempotency record no longer exists to recognize it. This is the specific, falsifiable difference from 2026-11-19, where eviction never created a security gap; here, it creates a real one, bounded by how long `ttl` is set to.

## Stretch

- A durable store (instead of an in-memory dict) so idempotency records survive a process restart — the in-memory version here can't protect against a retry that arrives after the service itself restarted.
- Concurrent/in-flight request handling: two near-simultaneous requests with the same key, where the second arrives before the first has finished executing the charge — not modeled here, since this exercise is sequential and deterministic by design.
- A longer `ttl` recommendation tied to the client's own maximum retry window, rather than an arbitrary fixed value — the real engineering trade-off this exercise's Approach 4 result is actually arguing for.
