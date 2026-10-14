# ADR 0001: Optional Postgres / SQLAlchemy adapter

**Proposed; deferred:** no confirmed consumer. SQLite stays default; SQLAlchemy stays optional outside core imports. Historical my-auth#1 does not prove compatibility.

Existing synchronous User/Role/Grant/Audit (`stores.py`) and Invitation (`invitations.py`) contracts have Memory/SQLite implementations; SQLite reuses Memory's static roles. Future modules: users, roles, grants, audit, invitations, database, schema. Resolve username normalization and invitation transaction parity before implementation.

Use sync Engine/Connection; async needs another decision. Adapter closes/disposes owned resources, never borrowed resources; stores never commit host transactions. Owned transactions commit on success, roll back on failure. UM owns UM migrations, my-auth owns credential migrations; hosts serialize explicit migration execution. No implicit DDL; unsupported schemas fail closed.

Passkey + UM atomicity requires a compatible my-auth credential backend sharing the connection and outer transaction; it remains unresolved. Reuse Memory/SQLite contract checks. Real Postgres must verify unique identity/username, credential CAS, rollback of both stores and concurrent last-admin mutations using independent connections and a shared guard. SQLite cannot substitute. Approve this ADR before implementation.
