"""Repository pattern (swappable persistence behind one interface) plus a
Unit of Work on top (atomic commit/rollback across *multiple* repositories
in one business operation) -- demonstrated with a funds-transfer use case
that touches two repositories (accounts, transaction log) at once.

The domain function `transfer_funds` is written exactly once, against the
abstract `UnitOfWork`/`Repository` interfaces, and never imports or
mentions either backend. It's run against two structurally different
backends -- an in-memory dict store (rollback via snapshot/restore) and a
real SQLite database (rollback via the database's own transaction log) --
through the identical test suite, which is the actual proof that the
abstraction doesn't leak backend-specific behavior.

`transfer_funds_unsafe` is the deliberate counter-example: the same logic
with no Unit of Work wrapping it, included specifically to measure the
failure mode the Unit of Work exists to prevent -- a crash partway through
leaves the accounts mutated but the transaction log entry missing, an
inconsistent state that the UoW-wrapped version cannot produce (see
notes.md for the measured comparison).
"""

from __future__ import annotations

import copy
import secrets
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, Generic, List, Optional, Protocol, TypeVar

T = TypeVar("T")


# --- domain models and errors ------------------------------------------------------


@dataclass
class Account:
    id: str
    owner: str
    balance: int


@dataclass
class TransactionRecord:
    id: str
    from_id: str
    to_id: str
    amount: int
    timestamp: float


class AccountNotFound(ValueError):
    pass


class InsufficientFunds(ValueError):
    pass


class DuplicateEntityError(ValueError):
    """Raised by every backend's `add()` on an id collision -- a shared
    type specifically so callers coded against the abstract `Repository`
    interface don't have to know or care which backend is underneath to
    catch this. Without it, InMemoryRepository would naturally raise a
    different exception than SQLite's UNIQUE constraint violation, and the
    abstraction would leak on the error path even though it holds on the
    happy path."""


class EntityNotFoundError(KeyError):
    """Raised by every backend's `update()` when the entity doesn't exist
    -- same reasoning as `DuplicateEntityError`, for the opposite case."""


# --- repository interface + backends ------------------------------------------------


class Repository(Protocol[T]):
    def get(self, entity_id: str) -> Optional[T]: ...
    def add(self, entity: T) -> None: ...
    def update(self, entity: T) -> None: ...
    def list(self) -> List[T]: ...


class InMemoryRepository(Generic[T]):
    """Backed by a plain dict. The dict is passed in rather than created
    here so a UnitOfWork can hold the *same* dict across many repository
    instances -- that shared dict is what makes "reconnecting" to the
    store in a later `with` block see earlier commits."""

    def __init__(self, store: Dict[str, T]):
        self._store = store

    def get(self, entity_id: str) -> Optional[T]:
        return self._store.get(entity_id)

    def add(self, entity) -> None:
        if entity.id in self._store:
            raise DuplicateEntityError(f"entity with id {entity.id!r} already exists")
        self._store[entity.id] = entity

    def update(self, entity) -> None:
        if entity.id not in self._store:
            raise EntityNotFoundError(f"cannot update nonexistent entity {entity.id!r}")
        self._store[entity.id] = entity

    def list(self) -> List[T]:
        return list(self._store.values())


class SqliteAccountRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection

    def get(self, entity_id: str) -> Optional[Account]:
        row = self._conn.execute(
            "SELECT id, owner, balance FROM accounts WHERE id = ?", (entity_id,)
        ).fetchone()
        return Account(id=row[0], owner=row[1], balance=row[2]) if row else None

    def add(self, entity: Account) -> None:
        try:
            self._conn.execute(
                "INSERT INTO accounts (id, owner, balance) VALUES (?, ?, ?)",
                (entity.id, entity.owner, entity.balance),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateEntityError(f"entity with id {entity.id!r} already exists") from e

    def update(self, entity: Account) -> None:
        cursor = self._conn.execute(
            "UPDATE accounts SET owner = ?, balance = ? WHERE id = ?",
            (entity.owner, entity.balance, entity.id),
        )
        if cursor.rowcount == 0:
            raise EntityNotFoundError(f"cannot update nonexistent entity {entity.id!r}")

    def list(self) -> List[Account]:
        rows = self._conn.execute("SELECT id, owner, balance FROM accounts").fetchall()
        return [Account(id=r[0], owner=r[1], balance=r[2]) for r in rows]


class SqliteTransactionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection

    def get(self, entity_id: str) -> Optional[TransactionRecord]:
        row = self._conn.execute(
            "SELECT id, from_id, to_id, amount, timestamp FROM transaction_log WHERE id = ?", (entity_id,)
        ).fetchone()
        return TransactionRecord(*row) if row else None

    def add(self, entity: TransactionRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO transaction_log (id, from_id, to_id, amount, timestamp) VALUES (?, ?, ?, ?, ?)",
                (entity.id, entity.from_id, entity.to_id, entity.amount, entity.timestamp),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateEntityError(f"entity with id {entity.id!r} already exists") from e

    def update(self, entity: TransactionRecord) -> None:
        cursor = self._conn.execute(
            "UPDATE transaction_log SET from_id = ?, to_id = ?, amount = ?, timestamp = ? WHERE id = ?",
            (entity.from_id, entity.to_id, entity.amount, entity.timestamp, entity.id),
        )
        if cursor.rowcount == 0:
            raise EntityNotFoundError(f"cannot update nonexistent entity {entity.id!r}")

    def list(self) -> List[TransactionRecord]:
        rows = self._conn.execute("SELECT id, from_id, to_id, amount, timestamp FROM transaction_log").fetchall()
        return [TransactionRecord(*r) for r in rows]


def create_sqlite_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, balance INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS transaction_log ("
        "id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, "
        "amount INTEGER NOT NULL, timestamp REAL NOT NULL)"
    )
    connection.commit()


# --- unit of work --------------------------------------------------------------------


class UnitOfWork(ABC):
    """Groups writes to multiple repositories into one atomic operation.
    `commit()` must be called explicitly inside the `with` block; exiting
    the block WITHOUT having committed -- whether because an exception
    propagated or the caller simply forgot -- rolls everything back. That
    "rollback is the default" asymmetry is deliberate: a forgotten
    `commit()` should silently do nothing, never silently keep whatever
    was written so far.
    """

    accounts: Repository
    transactions: Repository

    def __enter__(self) -> "UnitOfWork":
        self._committed = False
        self._begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._committed:
            self.rollback()
        return False  # never suppress an exception raised inside the `with` block

    @abstractmethod
    def _begin(self) -> None: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...


class InMemoryUnitOfWork(UnitOfWork):
    """Rollback via snapshot/restore: a deep copy of both backing stores is
    taken at `__enter__` (deep, not shallow -- entities are mutated in
    place, so a shallow dict copy would still share the same mutated
    objects and roll back nothing) and restored wholesale on rollback."""

    def __init__(self, accounts_store: Dict[str, Account], transactions_store: Dict[str, TransactionRecord]):
        self._accounts_store = accounts_store
        self._transactions_store = transactions_store
        self.accounts: InMemoryRepository[Account] = InMemoryRepository(accounts_store)
        self.transactions: InMemoryRepository[TransactionRecord] = InMemoryRepository(transactions_store)
        self._snapshot = None

    def _begin(self) -> None:
        self._snapshot = (copy.deepcopy(self._accounts_store), copy.deepcopy(self._transactions_store))

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        if self._snapshot is None:
            return
        accounts_snapshot, transactions_snapshot = self._snapshot
        self._accounts_store.clear()
        self._accounts_store.update(accounts_snapshot)
        self._transactions_store.clear()
        self._transactions_store.update(transactions_snapshot)


class SqliteUnitOfWork(UnitOfWork):
    """Rollback via the database's own transaction log -- `isolation_level
    =None` on the connection (set by the caller) puts sqlite3 in full
    manual-transaction mode, so no statement is implicitly committed
    behind this class's back between `BEGIN` and `commit()`/`rollback()`.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._conn = connection
        self.accounts = SqliteAccountRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)

    def _begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.commit()
        self._committed = True

    def rollback(self) -> None:
        self._conn.rollback()


def make_sqlite_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    create_sqlite_schema(conn)
    return conn


# --- domain logic: written once, against the abstract interfaces only -------------


def transfer_funds(
    uow: UnitOfWork,
    from_id: str,
    to_id: str,
    amount: int,
    *,
    transaction_id: Optional[str] = None,
    clock: Callable[[], float] = time.time,
) -> None:
    if amount <= 0:
        raise ValueError("amount must be positive")
    with uow:
        src = uow.accounts.get(from_id)
        if src is None:
            raise AccountNotFound(from_id)
        dst = uow.accounts.get(to_id)
        if dst is None:
            raise AccountNotFound(to_id)
        if src.balance < amount:
            raise InsufficientFunds(f"{from_id} has {src.balance}, needs {amount}")

        src.balance -= amount
        dst.balance += amount
        uow.accounts.update(src)
        uow.accounts.update(dst)
        uow.transactions.add(
            TransactionRecord(
                id=transaction_id or secrets.token_hex(8),
                from_id=from_id,
                to_id=to_id,
                amount=amount,
                timestamp=clock(),
            )
        )
        uow.commit()


def transfer_funds_unsafe(
    accounts: Repository,
    transactions: Repository,
    from_id: str,
    to_id: str,
    amount: int,
    *,
    transaction_id: Optional[str] = None,
    clock: Callable[[], float] = time.time,
) -> None:
    """Identical business logic to `transfer_funds`, with no Unit of Work.
    Included only as the counter-example that measures what a Unit of
    Work actually buys: if `transactions.add` fails here, the two
    `accounts.update` calls just above it have already taken effect, with
    nothing left to undo them."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    src = accounts.get(from_id)
    if src is None:
        raise AccountNotFound(from_id)
    dst = accounts.get(to_id)
    if dst is None:
        raise AccountNotFound(to_id)
    if src.balance < amount:
        raise InsufficientFunds(f"{from_id} has {src.balance}, needs {amount}")

    src.balance -= amount
    dst.balance += amount
    accounts.update(src)
    accounts.update(dst)
    transactions.add(
        TransactionRecord(
            id=transaction_id or secrets.token_hex(8),
            from_id=from_id,
            to_id=to_id,
            amount=amount,
            timestamp=clock(),
        )
    )


class FailingRepository:
    """Wraps a real repository and raises on one named method, after
    forwarding every other call through unchanged -- used to inject a
    failure partway through a multi-repository write and check what
    happens to whatever was already written before it."""

    def __init__(self, inner: Repository, *, fail_on: str = "add"):
        self._inner = inner
        self._fail_on = fail_on

    def get(self, entity_id: str):
        return self._inner.get(entity_id)

    def add(self, entity) -> None:
        if self._fail_on == "add":
            raise RuntimeError("simulated failure in add()")
        self._inner.add(entity)

    def update(self, entity) -> None:
        if self._fail_on == "update":
            raise RuntimeError("simulated failure in update()")
        self._inner.update(entity)

    def list(self):
        return self._inner.list()


def demo() -> None:
    print("1) Same domain logic, two backends, via the identical interface")
    for name, uow_factory in [
        ("in-memory", _fresh_in_memory_factory()),
        ("sqlite", _fresh_sqlite_factory()),
    ]:
        with uow_factory() as uow:
            uow.accounts.add(Account("alice", "Alice", 100))
            uow.accounts.add(Account("bob", "Bob", 50))
            uow.commit()
        transfer_funds(uow_factory(), "alice", "bob", 30, transaction_id="t1", clock=lambda: 1000.0)
        with uow_factory() as uow:
            a, b = uow.accounts.get("alice"), uow.accounts.get("bob")
            print(f"   {name:>9}: alice={a.balance}, bob={b.balance}, transactions={len(uow.transactions.list())}")

    print("\n2) Unit of Work vs. no Unit of Work, identical injected mid-transaction failure")
    for name, uow_factory, accounts_repo_factory, transactions_repo_factory in [
        _fresh_in_memory_handle(),
        _fresh_sqlite_handle(),
    ]:
        uow = uow_factory()
        uow.accounts.add(Account("alice", "Alice", 100))
        uow.accounts.add(Account("bob", "Bob", 50))
        uow.commit()

        safe_uow = uow_factory()
        safe_uow.transactions = FailingRepository(safe_uow.transactions, fail_on="add")
        try:
            transfer_funds(safe_uow, "alice", "bob", 30)
        except RuntimeError:
            pass
        with uow_factory() as check:
            a, b = check.accounts.get("alice"), check.accounts.get("bob")
            print(f"   {name:>9} + UoW:    alice={a.balance}, bob={b.balance}  (rolled back, as if nothing happened)")

        unsafe_accounts = accounts_repo_factory()
        unsafe_transactions = FailingRepository(transactions_repo_factory(), fail_on="add")
        try:
            transfer_funds_unsafe(unsafe_accounts, unsafe_transactions, "alice", "bob", 30)
        except RuntimeError:
            pass
        with uow_factory() as check:
            a, b = check.accounts.get("alice"), check.accounts.get("bob")
            print(f"   {name:>9} no UoW:   alice={a.balance}, bob={b.balance}  (mutated despite the failure)")


def _fresh_in_memory_factory():
    accounts_store: Dict[str, Account] = {}
    transactions_store: Dict[str, TransactionRecord] = {}
    return lambda: InMemoryUnitOfWork(accounts_store, transactions_store)


def _fresh_sqlite_factory():
    conn = make_sqlite_connection()
    return lambda: SqliteUnitOfWork(conn)


def _fresh_in_memory_handle():
    accounts_store: Dict[str, Account] = {}
    transactions_store: Dict[str, TransactionRecord] = {}
    return (
        "in-memory",
        lambda: InMemoryUnitOfWork(accounts_store, transactions_store),
        lambda: InMemoryRepository(accounts_store),
        lambda: InMemoryRepository(transactions_store),
    )


def _fresh_sqlite_handle():
    conn = make_sqlite_connection()
    return (
        "sqlite",
        lambda: SqliteUnitOfWork(conn),
        lambda: SqliteAccountRepository(conn),
        lambda: SqliteTransactionRepository(conn),
    )


if __name__ == "__main__":
    demo()
