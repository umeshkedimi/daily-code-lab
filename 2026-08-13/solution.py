"""Redis-backed distributed rate limiter: 5 requests/user/minute.

Algorithm: sliding-window log, implemented as a Redis sorted set (ZSET)
per user, with the check-and-increment done atomically in a single Lua
script (EVALSHA) so it's race-free under concurrent callers and safe to
share across multiple application instances.
"""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import redis

# ZSET member = request timestamp (ms), score = same timestamp.
# Atomic: trim anything outside the window, count what's left, and only
# admit the new request if under the limit -- all in one round trip so no
# two concurrent callers can both observe "room for one more".
_LUA_SCRIPT = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]

local time_parts = redis.call('TIME')
local now_ms = tonumber(time_parts[1]) * 1000 + math.floor(tonumber(time_parts[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)

local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
    return 1
else
    return 0
end
"""


class RateLimiter:
    """Sliding-window rate limiter backed by Redis.

    State lives entirely in Redis, so any number of application instances
    sharing the same Redis deployment enforce one consistent limit per user.
    """

    def __init__(self, redis_client: "redis.Redis", max_requests: int = 5, window_seconds: int = 60):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_ms = window_seconds * 1000
        self._script = self.redis.register_script(_LUA_SCRIPT)

    def is_allowed(self, user_id: str) -> bool:
        """Return True if this request should be allowed, False to reject it."""
        key = f"ratelimit:{user_id}"
        member = uuid.uuid4().hex  # unique per call so concurrent hits in the same ms don't collide in the ZSET
        result = self._script(keys=[key], args=[self.window_ms, self.max_requests, member])
        return bool(result)


def _make_client() -> "redis.Redis":
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def demo():
    r = _make_client()
    r.flushdb()

    print("1) Sequential burst for one user (limit=5/min):")
    limiter = RateLimiter(r, max_requests=5, window_seconds=60)
    for i in range(1, 8):
        allowed = limiter.is_allowed("user-1")
        print(f"   request {i}: {'ALLOWED' if allowed else 'REJECTED'}")

    print("\n2) Concurrent burst, 30 threads hitting the same user at once:")
    # Each thread gets its own RateLimiter/connection, simulating separate
    # application instances that all happen to share this Redis deployment.
    def make_request(_):
        return RateLimiter(_make_client(), max_requests=5, window_seconds=60).is_allowed("user-2")

    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(make_request, range(30)))
    allowed_count = sum(results)
    print(f"   allowed: {allowed_count} / 30 (expected exactly 5 regardless of race conditions)")

    print("\n3) Independent limits per user:")
    for user in ("user-3", "user-4"):
        allowed = RateLimiter(r, max_requests=5, window_seconds=60).is_allowed(user)
        print(f"   {user} first request: {'ALLOWED' if allowed else 'REJECTED'}")

    print("\n4) Window expiry (short 2s window so the demo doesn't wait a full minute):")
    short_limiter = RateLimiter(r, max_requests=2, window_seconds=2)
    print(f"   request 1: {'ALLOWED' if short_limiter.is_allowed('user-5') else 'REJECTED'}")
    print(f"   request 2: {'ALLOWED' if short_limiter.is_allowed('user-5') else 'REJECTED'}")
    print(f"   request 3 (still in window): {'ALLOWED' if short_limiter.is_allowed('user-5') else 'REJECTED'}")
    time.sleep(2.1)
    print(f"   request 4 (after window elapses): {'ALLOWED' if short_limiter.is_allowed('user-5') else 'REJECTED'}")


if __name__ == "__main__":
    demo()
