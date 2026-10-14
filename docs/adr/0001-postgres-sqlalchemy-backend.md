# ADR 0001: Optional Postgres / SQLAlchemy adapter

Status: **Proposed**, deferred until a confirmed consumer requires Postgres.
No backend or consumer is confirmed. SQLite remains the default;
SQLAlchemy and its driver must stay optional and outside core imports.
Historical my-auth#1 does not establish current backend compatibility.

Current synchronous contracts: User/Role/Grant/Audit in `stores.py`,
Invitation in `invitations.py`. Memory and SQLite implement these;
SQLite reuses MemoryRoleStore's static catalogue. Do not duplicate stores.
Future modules: users, roles, grants, audit, invitations, database and schema.
Resolve username normalization and invitation transaction parity first.

Use synchronous Engine/Connection; async requires a separate decision.
The adapter closes connections and disposes engines it owns; the host owns
borrowed resources. Stores never commit or close host-owned transactions.
Owned transactions commit on success and roll back on failure.
UM owns UM migrations; hosts explicitly serialize execution; my-auth owns
credential migrations. No implicit DDL; unsupported schemas fail closed.

Passkey + UM atomicity remains unresolved: my-auth needs a compatible
credential backend using the same connection and outer transaction.
Reuse Memory/SQLite contract checks. Real Postgres checks must cover unique
identity/username, credential CAS, rollback of both stores and concurrent
last-admin mutations with independent connections and a shared guard.
SQLite cannot substitute for these checks. Approve this ADR before implementation.
