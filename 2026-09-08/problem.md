# Problem

**Category:** `backend` / `system-design`

## Statement

Build a **Redis-backed distributed lock** (mutex) that lets only one caller at a time execute a critical section, safe when that critical section is invoked concurrently and from multiple application instances.

**Requirements:**
- `acquire()` / `release()` API (plus a context-manager form for correct usage by default).
- Mutual exclusion must hold under real concurrency, not just look correct on paper.
- A holder that crashes without releasing must not deadlock the lock forever.
- A holder must not be able to release a lock it no longer actually owns.

## Constraints

- Only one caller may be inside the critical section at any instant, regardless of how many processes/threads/instances are racing to acquire it.
- The lock must self-heal from a crashed holder within a bounded time (a TTL), without any external cleanup process.
- Releasing must be scoped to the actual current owner — a stale holder whose lock already expired must not be able to delete a different, currently-valid holder's lock.

## Approach

1. **Acquisition primitive: `SET key value NX PX ttl`.** This one Redis command atomically does "create this key only if it doesn't already exist, with an expiry" — that atomicity is what decides a single winner when multiple callers race to acquire the same lock at once. Whoever's `SET NX` returns success owns the lock; everyone else's returns a no-op.

2. **Ownership token, not just presence.** The value stored isn't a placeholder like `"1"` — it's a random UUID unique to this specific `acquire()` call. This is what makes safe release possible: release only needs to delete the key if *my* token is still the value stored there.

3. **Release must be atomic too — a Lua script, not two separate commands.** "Check if my token is stored, then delete" is a check-then-act pair: done as two separate round trips (`GET` then `DEL`), there's a window where the lock could expire and a different holder could acquire it *between* the check and the delete, and the stale holder's `DEL` would wrongly remove the new holder's active lock. Wrapping both in one Lua script makes the check-and-delete indivisible.

4. **TTL as the crash-recovery mechanism.** A process can die (crash, OOM-kill, network partition) while holding a lock and never call `release()`. Without a TTL, that lock is held forever — a permanent deadlock. With a TTL, an abandoned lock self-expires and the resource becomes acquirable again, bounded by the TTL length. This is a deliberate trade-off, not a free win — see below.

5. **Proved mutual exclusion with a real race, not just code review.** The most convincing evidence for "this actually prevents concurrent access" is a shared counter incremented by many threads inside the critical section: without the lock, concurrent read-modify-write loses updates (the classic lost-update bug); with the lock, the final count is exactly right. `solution.py`'s demo runs both versions side by side.
