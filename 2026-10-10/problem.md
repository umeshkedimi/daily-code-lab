# Problem

**Category:** `backend` / `system-design` / `python`

## Statement

Implement the **Repository pattern** (persistence hidden behind one interface: `get`/`add`/`update`/`list`) with two structurally different backends — an in-memory dict store and a real SQLite database — and a **Unit of Work** on top that groups writes to *multiple* repositories into one atomic operation.

The worked example is a funds transfer that touches two repositories at once (an `Account` repository and a `TransactionRecord` log), written exactly once against the abstract interfaces, and never importing or mentioning either backend by name.

**Requirements:**
- The domain logic (`transfer_funds`) must be backend-agnostic: the identical function, unmodified, must work against both backends.
- The Unit of Work must roll back **every** repository it touched, not just the one where a failure happened — a crash while writing the second repository must undo writes already made to the first.
- `commit()` must be explicit; exiting the `with` block without calling it must roll back by default, whether an exception was the reason or the caller simply forgot.
- The same test suite (not a parallel, backend-specific copy of it) must run against both backends, so an inconsistency between them shows up as a test failure rather than being missed by only ever checking one.
- Error semantics must not leak the backend: colliding on an existing id, or updating something that doesn't exist, must raise the *same* exception type regardless of which backend is underneath.

## Constraints

- Standard library only (`sqlite3`, `copy`, `secrets`).
- A deliberate counter-example (`transfer_funds_unsafe`) — the identical business logic with no Unit of Work — is included specifically to measure, not just assert, what the Unit of Work buys: the same injected failure must be shown leaving the unsafe version in an inconsistent state (accounts mutated, log entry missing) while the UoW-wrapped version leaves no trace at all.
- Checked with hand-picked adversarial cases (insufficient funds, unknown accounts, a transaction-log write that fails partway through) and a randomized property test (money is conserved, and no balance ever goes negative, across 100 random transfer attempts) — and a differential test running an identical scripted operation sequence against both backends and asserting the final states are byte-for-byte equal.

## Approach

1. **`Repository` as a `Protocol`** (`get`/`add`/`update`/`list`), with two independent implementations: `InMemoryRepository` (a plain dict, injected rather than owned, so a `UnitOfWork` can hold the same dict across many repository instances and "reconnecting" in a later `with` block sees earlier commits) and per-entity SQLite repositories (`SqliteAccountRepository`, `SqliteTransactionRepository`) backed by real SQL.
2. **`UnitOfWork` as an abstract base class** exposing `accounts`/`transactions` plus `commit()`/`rollback()`, used as a context manager. `__exit__` rolls back unless `commit()` was explicitly called inside the block — rollback is the default outcome of *not* committing, not an error path that has to be triggered.
3. **Two different rollback mechanisms for the two backends, both satisfying the identical contract:** `InMemoryUnitOfWork` takes a *deep* copy of both backing stores at `__enter__` and restores it wholesale on rollback (deep, because entities are mutated in place — a shallow dict copy would still share the same mutated objects). `SqliteUnitOfWork` uses the database's own transaction log (`BEGIN`/`COMMIT`/`ROLLBACK` on a connection running in manual, non-implicit-transaction mode).
4. **One domain function, two call sites in the test suite**: `transfer_funds(uow, ...)` is parametrized across both backends via a single pytest fixture, so every test in the "happy path + edge cases" section runs twice — against structurally different implementations — without being written twice.
5. **Measuring the point of the Unit of Work, not just asserting it**: the same injected failure (a repository wrapper that raises on `add`) is run once through `transfer_funds` (UoW-wrapped) and once through `transfer_funds_unsafe` (not), and the resulting account balances are compared — directly showing the inconsistent intermediate state the Unit of Work exists to prevent.
