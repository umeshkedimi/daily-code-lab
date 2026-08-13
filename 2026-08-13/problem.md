# Problem

**Category:** `backend` / `system-design`

## Statement

Build a **Redis-backed distributed API rate limiter** in Python that allows each user a maximum of **5 requests per minute**.

**Requirements:**
- Implement the core logic and expose a small method/API to check whether a request should be **allowed or rejected**.
- Consider concurrent requests and multiple application instances.

## Constraints

- Limit is per user: `user-A` making 5 requests must not affect `user-B`'s quota.
- Correct under concurrency: many simultaneous requests for the same user must not let more than 5 through, even as a race.
- Correct across multiple application instances: if two separate app servers both call the limiter for the same user, they must agree on one shared count, not keep two independent local counts.

## Approach

1. **State must live outside the application process.** With N app instances behind a load balancer, an in-memory counter per instance would let each instance independently allow 5 requests — N×5 total for one user. Redis is the natural fit: single shared store, all instances read/write the same key per user.

2. **Algorithm: sliding-window log**, not fixed-window counters. A naive fixed window (`INCR user:minute-bucket`, reset every 60s) lets a user burst up to 2×limit right at a window boundary (5 requests at 0:59, 5 more at 1:00). A sliding-window log tracks the actual timestamp of each request and only counts ones inside the trailing 60-second window, so the limit holds at any point in time, not just at bucket edges.

3. **Data structure: a Redis sorted set (ZSET) per user**, `ratelimit:<user_id>`, where each member is a unique request ID and its score is the request's timestamp in milliseconds. Checking the limit means: drop members older than `now - window`, count what's left, and admit the new request only if under the limit.

4. **Atomicity is the crux of the concurrency requirement.** "Count, then decide, then add" is a classic check-then-act race — two concurrent requests can both read count=4 and both add themselves, blowing past the limit of 5. Redis executes Lua scripts as a single atomic operation (no other command runs on that Redis instance mid-script), so the entire trim-count-decide-add sequence is wrapped in one `EVAL`/`EVALSHA` call. This is also what makes multiple application instances safe "for free" — they're not coordinating with each other, they're all just issuing the same atomic script against the one shared Redis.

5. **Clock source: Redis's own `TIME` command inside the script**, not each application server's local clock. Multiple app instances may have clock skew; if each computed `now` locally and sent it as an argument, the effective window boundary would drift between instances. Using Redis's clock makes it the single source of truth.
