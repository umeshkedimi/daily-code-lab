"""Idempotency-key-based exactly-once-ish charge semantics -- measures
the concrete cost of ignoring at-least-once retries, and the honest
cost (unlike 2026-11-19's webhook verifier) of the TTL eviction that
bounds memory here."""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple


class Ledger:
    def __init__(self) -> None:
        self.charges: List[Tuple[str, int]] = []

    def execute_charge(self, account_id: str, amount: int) -> str:
        charge_id = f"ch_{len(self.charges) + 1}"
        self.charges.append((account_id, amount))
        return charge_id


class NaiveChargeService:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def charge(self, idempotency_key: str, account_id: str, amount: int) -> str:
        return self.ledger.execute_charge(account_id, amount)


class IdempotencyKeyConflict(Exception):
    pass


class IdempotentChargeService:
    def __init__(self, ledger: Ledger, clock: Callable[[], int], ttl: int) -> None:
        self.ledger = ledger
        self._clock = clock
        self.ttl = ttl
        self._store: Dict[str, Tuple[int, str, int, str]] = {}

    def charge(self, idempotency_key: str, account_id: str, amount: int) -> str:
        """Evict stale records, then either return the remembered
        charge_id for a matching retry, raise IdempotencyKeyConflict
        for a mismatched reuse, or execute a real charge and remember
        it.
        """
        now = self._clock()
        self._evict_stale(now)
        if idempotency_key in self._store:
            _, stored_account, stored_amount, charge_id = self._store[idempotency_key]
            if stored_account != account_id or stored_amount != amount:
                raise IdempotencyKeyConflict(idempotency_key)
            return charge_id
        charge_id = self.ledger.execute_charge(account_id, amount)
        self._store[idempotency_key] = (now, account_id, amount, charge_id)
        return charge_id

    def _evict_stale(self, now: int) -> None:
        stale = [k for k, (ts, *_rest) in self._store.items() if now - ts > self.ttl]
        for k in stale:
            del self._store[k]

    @property
    def store_size(self) -> int:
        return len(self._store)
