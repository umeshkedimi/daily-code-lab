import random
from dataclasses import dataclass
from typing import Callable, Dict

import pytest

from solution import (
    Account,
    AccountNotFound,
    DuplicateEntityError,
    EntityNotFoundError,
    FailingRepository,
    InMemoryRepository,
    InMemoryUnitOfWork,
    InsufficientFunds,
    Repository,
    SqliteAccountRepository,
    SqliteTransactionRepository,
    SqliteUnitOfWork,
    TransactionRecord,
    UnitOfWork,
    make_sqlite_connection,
    transfer_funds,
    transfer_funds_unsafe,
)


@dataclass
class BackendHandle:
    name: str
    uow_factory: Callable[[], UnitOfWork]
    accounts_repo_factory: Callable[[], Repository]
    transactions_repo_factory: Callable[[], Repository]


@pytest.fixture(params=["in_memory", "sqlite"])
def backend(request):
    if request.param == "in_memory":
        accounts_store: Dict[str, Account] = {}
        transactions_store: Dict[str, TransactionRecord] = {}
        yield BackendHandle(
            name="in_memory",
            uow_factory=lambda: InMemoryUnitOfWork(accounts_store, transactions_store),
            accounts_repo_factory=lambda: InMemoryRepository(accounts_store),
            transactions_repo_factory=lambda: InMemoryRepository(transactions_store),
        )
    else:
        conn = make_sqlite_connection()
        yield BackendHandle(
            name="sqlite",
            uow_factory=lambda: SqliteUnitOfWork(conn),
            accounts_repo_factory=lambda: SqliteAccountRepository(conn),
            transactions_repo_factory=lambda: SqliteTransactionRepository(conn),
        )
        conn.close()


def seed(backend: BackendHandle, accounts):
    with backend.uow_factory() as uow:
        for a in accounts:
            uow.accounts.add(a)
        uow.commit()


# --- the same test suite, run against both backends via the `backend` fixture -----


def test_transfer_moves_balance_between_accounts(backend):
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 50)])
    transfer_funds(backend.uow_factory(), "a", "b", 30)
    with backend.uow_factory() as uow:
        assert uow.accounts.get("a").balance == 70
        assert uow.accounts.get("b").balance == 80


def test_transfer_records_a_transaction(backend):
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 50)])
    transfer_funds(backend.uow_factory(), "a", "b", 30, clock=lambda: 42.0)
    with backend.uow_factory() as uow:
        records = uow.transactions.list()
        assert len(records) == 1
        assert (records[0].from_id, records[0].to_id, records[0].amount, records[0].timestamp) == ("a", "b", 30, 42.0)


@pytest.mark.parametrize("amount,expect_ok", [(100, True), (101, False)])
def test_transfer_boundary_at_exact_balance(backend, amount, expect_ok):
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 0)])
    if expect_ok:
        transfer_funds(backend.uow_factory(), "a", "b", amount)
        with backend.uow_factory() as uow:
            assert uow.accounts.get("a").balance == 0
            assert uow.accounts.get("b").balance == 100
    else:
        with pytest.raises(InsufficientFunds):
            transfer_funds(backend.uow_factory(), "a", "b", amount)


def test_transfer_fails_on_insufficient_funds_and_leaves_balances_unchanged(backend):
    seed(backend, [Account("a", "alice", 10), Account("b", "bob", 50)])
    with pytest.raises(InsufficientFunds):
        transfer_funds(backend.uow_factory(), "a", "b", 30)
    with backend.uow_factory() as uow:
        assert uow.accounts.get("a").balance == 10
        assert uow.accounts.get("b").balance == 50
        assert uow.transactions.list() == []


@pytest.mark.parametrize("missing", ["from", "to"])
def test_transfer_fails_on_unknown_account_and_leaves_state_unchanged(backend, missing):
    seed(backend, [Account("a", "alice", 100)])
    from_id, to_id = ("ghost", "a") if missing == "from" else ("a", "ghost")
    with pytest.raises(AccountNotFound):
        transfer_funds(backend.uow_factory(), from_id, to_id, 10)
    with backend.uow_factory() as uow:
        assert uow.accounts.get("a").balance == 100
        assert uow.transactions.list() == []


def test_transfer_rejects_non_positive_amount(backend):
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 0)])
    with pytest.raises(ValueError):
        transfer_funds(backend.uow_factory(), "a", "b", 0)
    with pytest.raises(ValueError):
        transfer_funds(backend.uow_factory(), "a", "b", -5)


def test_exiting_the_with_block_without_calling_commit_rolls_back(backend):
    # Direct UnitOfWork usage, bypassing transfer_funds entirely -- this is
    # the UoW's own contract, not the domain logic's: rollback is the
    # default outcome of exiting the block, whether an exception happened
    # or the caller simply forgot to commit.
    seed(backend, [Account("a", "alice", 100)])
    with backend.uow_factory() as uow:
        acct = uow.accounts.get("a")
        acct.balance = 999
        uow.accounts.update(acct)
        # deliberately never call uow.commit()
    with backend.uow_factory() as uow2:
        assert uow2.accounts.get("a").balance == 100


def test_duplicate_add_raises_the_same_exception_type_on_every_backend(backend):
    seed(backend, [Account("a", "alice", 100)])
    with backend.uow_factory() as uow:
        with pytest.raises(DuplicateEntityError):
            uow.accounts.add(Account("a", "alice-again", 0))
        # deliberately not calling commit() -- exiting the block rolls back automatically


def test_update_of_nonexistent_entity_raises_the_same_exception_type_on_every_backend(backend):
    with backend.uow_factory() as uow:
        with pytest.raises(EntityNotFoundError):
            uow.accounts.update(Account("ghost", "nobody", 0))


# --- the actual point: Unit of Work vs. no Unit of Work, same failure -------------


def test_transfer_rolls_back_both_repositories_on_mid_transaction_failure(backend):
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 50)])
    uow = backend.uow_factory()
    uow.transactions = FailingRepository(uow.transactions, fail_on="add")

    with pytest.raises(RuntimeError):
        transfer_funds(uow, "a", "b", 30)

    with backend.uow_factory() as check:
        assert check.accounts.get("a").balance == 100  # untouched
        assert check.accounts.get("b").balance == 50  # untouched
        assert check.transactions.list() == []  # never landed


def test_unsafe_transfer_leaves_inconsistent_state_on_the_identical_failure(backend):
    # Same scenario, same injected failure, no Unit of Work: the point of
    # this test is that the assertions below are the OPPOSITE of the
    # UoW-wrapped test above, for the exact same failure.
    seed(backend, [Account("a", "alice", 100), Account("b", "bob", 50)])
    accounts_repo = backend.accounts_repo_factory()
    transactions_repo = FailingRepository(backend.transactions_repo_factory(), fail_on="add")

    with pytest.raises(RuntimeError):
        transfer_funds_unsafe(accounts_repo, transactions_repo, "a", "b", 30)

    fresh_accounts = backend.accounts_repo_factory()
    fresh_transactions = backend.transactions_repo_factory()
    assert fresh_accounts.get("a").balance == 70  # mutated, despite the failure
    assert fresh_accounts.get("b").balance == 80  # mutated, despite the failure
    assert fresh_transactions.list() == []  # the log entry still never landed


# --- cross-backend differential test: identical script, identical result ---------


def _run_script(uow_factory):
    with uow_factory() as uow:
        uow.accounts.add(Account("a", "alice", 100))
        uow.accounts.add(Account("b", "bob", 50))
        uow.accounts.add(Account("c", "carol", 0))
        uow.commit()

    transfer_funds(uow_factory(), "a", "b", 30, transaction_id="t1", clock=lambda: 1000.0)
    transfer_funds(uow_factory(), "b", "c", 10, transaction_id="t2", clock=lambda: 1001.0)
    try:
        transfer_funds(uow_factory(), "a", "c", 10_000, transaction_id="t3", clock=lambda: 1002.0)
    except InsufficientFunds:
        pass

    with uow_factory() as uow:
        accounts = sorted(uow.accounts.list(), key=lambda a: a.id)
        transactions = sorted(uow.transactions.list(), key=lambda t: t.id)
        return accounts, transactions


def test_in_memory_and_sqlite_agree_on_an_identical_scripted_sequence():
    mem_accounts: Dict[str, Account] = {}
    mem_transactions: Dict[str, TransactionRecord] = {}
    mem_result = _run_script(lambda: InMemoryUnitOfWork(mem_accounts, mem_transactions))

    conn = make_sqlite_connection()
    sqlite_result = _run_script(lambda: SqliteUnitOfWork(conn))
    conn.close()

    assert mem_result == sqlite_result


# --- randomized property test: total money is conserved across any sequence -------


@pytest.mark.parametrize("seed_value", range(10))
def test_random_transfer_sequences_conserve_total_money(backend, seed_value):
    rng = random.Random(seed_value)
    accounts = [Account(f"acct-{i}", f"user{i}", rng.randint(0, 1000)) for i in range(6)]
    seed(backend, accounts)
    total_before = sum(a.balance for a in accounts)
    ids = [a.id for a in accounts]

    for _ in range(100):
        from_id, to_id = rng.sample(ids, 2)
        amount = rng.randint(1, 200)
        try:
            transfer_funds(backend.uow_factory(), from_id, to_id, amount)
        except (InsufficientFunds, AccountNotFound):
            pass

    with backend.uow_factory() as uow:
        total_after = sum(a.balance for a in uow.accounts.list())
        for a in uow.accounts.list():
            assert a.balance >= 0  # no transfer should ever be able to push a balance negative
    assert total_after == total_before
