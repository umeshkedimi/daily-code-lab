import pytest

from solution import (
    IdempotencyKeyConflict,
    IdempotentChargeService,
    Ledger,
    NaiveChargeService,
)


class FakeClock:
    def __init__(self, start: int = 0) -> None:
        self.now = start

    def advance(self, dt: int) -> None:
        self.now += dt

    def __call__(self) -> int:
        return self.now


# --- Ledger / NaiveChargeService basics -----------------------------------


def test_ledger_assigns_sequential_charge_ids():
    ledger = Ledger()
    assert ledger.execute_charge("acct-1", 100) == "ch_1"
    assert ledger.execute_charge("acct-1", 200) == "ch_2"
    assert ledger.charges == [("acct-1", 100), ("acct-1", 200)]


def test_naive_service_ignores_idempotency_key_entirely():
    ledger = Ledger()
    naive = NaiveChargeService(ledger)
    naive.charge("same-key", "acct-1", 500)
    naive.charge("same-key", "acct-1", 500)
    assert len(ledger.charges) == 2


# --- the headline measured gap: 5 retries, naive vs idempotent -----------


def test_headline_naive_executes_five_real_charges_for_five_retries():
    ledger = Ledger()
    naive = NaiveChargeService(ledger)
    results = [naive.charge("key-1", "acct-1", 500) for _ in range(5)]
    assert len(ledger.charges) == 5
    assert len(set(results)) == 5  # five distinct charge_ids


def test_headline_idempotent_executes_exactly_one_real_charge_for_five_retries():
    ledger = Ledger()
    clock = FakeClock(0)
    idem = IdempotentChargeService(ledger, clock, ttl=300)
    results = [idem.charge("key-1", "acct-1", 500) for _ in range(5)]
    assert len(ledger.charges) == 1
    assert len(set(results)) == 1  # identical charge_id every time


# --- conflict detection: same key, different payload ----------------------


def test_idempotent_service_raises_on_key_reuse_with_different_amount():
    ledger = Ledger()
    idem = IdempotentChargeService(ledger, FakeClock(0), ttl=300)
    idem.charge("key-2", "acct-1", 500)
    with pytest.raises(IdempotencyKeyConflict):
        idem.charge("key-2", "acct-1", 999)
    assert len(ledger.charges) == 1  # the conflicting attempt never executed


def test_idempotent_service_raises_on_key_reuse_with_different_account():
    ledger = Ledger()
    idem = IdempotentChargeService(ledger, FakeClock(0), ttl=300)
    idem.charge("key-3", "acct-1", 500)
    with pytest.raises(IdempotencyKeyConflict):
        idem.charge("key-3", "acct-2", 500)
    assert len(ledger.charges) == 1


def test_naive_service_has_no_conflict_detection_at_all():
    ledger = Ledger()
    naive = NaiveChargeService(ledger)
    naive.charge("key-4", "acct-1", 500)
    naive.charge("key-4", "acct-1", 999)  # same key, different amount: no error
    assert len(ledger.charges) == 2


# --- bounded memory under continuous traffic ------------------------------


def test_store_size_does_not_grow_unboundedly_under_continuous_traffic():
    clock = FakeClock(0)
    idem = IdempotentChargeService(Ledger(), clock, ttl=100)

    max_store = 0
    for i in range(50):
        clock.now = i * 101  # each key ages out just before the next arrives
        idem.charge(f"key-{i}", "acct-1", 100)
        max_store = max(max_store, idem.store_size)

    assert max_store == 1


# --- the honest cost of eviction: a late retry duplicates the charge -----


def test_late_replay_after_ttl_expiry_executes_as_a_genuine_duplicate_charge():
    clock = FakeClock(0)
    ledger = Ledger()
    idem = IdempotentChargeService(ledger, clock, ttl=100)

    idem.charge("key-5", "acct-1", 500)
    assert len(ledger.charges) == 1

    clock.now = 500  # well past ttl=100
    idem.charge("key-5", "acct-1", 500)  # a genuine retry, arriving too late
    assert len(ledger.charges) == 2  # the idempotency protection is gone


def test_replay_within_ttl_still_correctly_deduplicates():
    clock = FakeClock(0)
    ledger = Ledger()
    idem = IdempotentChargeService(ledger, clock, ttl=100)

    idem.charge("key-6", "acct-1", 500)
    clock.now = 50  # still within ttl=100
    idem.charge("key-6", "acct-1", 500)
    assert len(ledger.charges) == 1
