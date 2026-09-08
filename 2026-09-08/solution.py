"""Redis-backed distributed lock (mutex) safe across concurrent callers and
multiple application instances.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import redis

# Release: only delete the key if it still holds *this* holder's token.
# Without this check, a holder whose lock already expired (e.g. it stalled
# past the TTL) could delete a lock some other instance has since acquired.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class RedisLock:
    """A mutual-exclusion lock backed by a single Redis key.

    Whichever caller's SET NX succeeds owns the lock. State lives in Redis,
    not in the process, so any number of application instances sharing the
    same Redis deployment coordinate on one true lock owner.
    """

    def __init__(self, redis_client: "redis.Redis", resource: str, ttl_ms: int = 5000):
        self.redis = redis_client
        self.key = f"lock:{resource}"
        self.ttl_ms = ttl_ms
        self.token: str | None = None
        self._release_script = self.redis.register_script(_RELEASE_LUA)

    def acquire(self, blocking: bool = True, timeout: float = 10.0, retry_delay: float = 0.02) -> bool:
        """Try to become the lock holder. Returns True iff this call now owns it."""
        token = uuid.uuid4().hex
        deadline = time.monotonic() + timeout
        while True:
            # SET ... NX PX: atomic "create if absent, with expiry" -- the
            # single Redis operation that decides who wins the race.
            if self.redis.set(self.key, token, nx=True, px=self.ttl_ms):
                self.token = token
                return True
            if not blocking or time.monotonic() >= deadline:
                return False
            time.sleep(retry_delay)

    def release(self) -> bool:
        """Release the lock, but only if this instance still owns it."""
        if self.token is None:
            return False
        released = bool(self._release_script(keys=[self.key], args=[self.token]))
        self.token = None
        return released

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"could not acquire lock for {self.key!r}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def _make_client() -> "redis.Redis":
    import os

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def _racy_increment(r: "redis.Redis", key: str) -> None:
    """Deliberately racy read-sleep-write, no lock -- the classic lost-update bug."""
    value = int(r.get(key) or 0)
    time.sleep(0.001)  # widen the race window so contention is virtually guaranteed
    r.set(key, value + 1)


def _locked_increment(key: str) -> None:
    """Same read-sleep-write, but the critical section is now mutually exclusive."""
    r = _make_client()
    with RedisLock(r, resource=key, ttl_ms=2000):
        value = int(r.get(key) or 0)
        time.sleep(0.001)
        r.set(key, value + 1)


def demo():
    r = _make_client()
    r.flushdb()
    n = 50

    print(f"1) WITHOUT a lock: {n} concurrent increments on one counter (lost-update bug)")
    r.set("counter_racy", 0)
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda _: _racy_increment(_make_client(), "counter_racy"), range(n)))
    print(f"   final value: {r.get('counter_racy')} (expected {n} -- lower means updates were lost)")

    print(f"\n2) WITH RedisLock: {n} concurrent increments on one counter")
    r.set("counter_locked", 0)
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(_locked_increment, ["counter_locked"] * n))
    print(f"   final value: {r.get('counter_locked')} (expected exactly {n})")

    print("\n3) Crash recovery: a holder that never releases doesn't deadlock the lock forever")
    crashed_holder = RedisLock(r, resource="job-x", ttl_ms=1000)
    crashed_holder.acquire()
    print(f"   holder A acquired 'job-x' (never calls release() -- simulates a crash)")
    other = RedisLock(r, resource="job-x", ttl_ms=1000)
    print(f"   holder B tries immediately: acquired={other.acquire(blocking=False)}")
    time.sleep(1.1)  # past the 1s TTL
    print(f"   holder B tries after TTL elapses: acquired={other.acquire(blocking=False)}")

    print("\n4) Safe release: a stale holder can't release someone else's active lock")
    r.flushdb()
    stale = RedisLock(r, resource="job-y", ttl_ms=300)
    stale.acquire()
    time.sleep(0.4)  # let stale's lock expire without it knowing
    fresh = RedisLock(r, resource="job-y", ttl_ms=5000)
    fresh.acquire()
    print(f"   holder C (fresh) now owns 'job-y'")
    released = stale.release()  # stale still has its old token, but the key holds C's token now
    print(f"   holder A (stale, unaware it expired) calls release(): released={released}")
    print(f"   'job-y' still held by C afterwards: {r.get('lock:job-y') == fresh.token}")


if __name__ == "__main__":
    demo()
