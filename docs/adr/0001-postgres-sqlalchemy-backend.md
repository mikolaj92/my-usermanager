# ADR 0001: Optional Postgres / SQLAlchemy adapter

Status: **Proposed**. SQLite stays the default durable adapter. No Postgres
backend, extra or confirmed consumer exists in this repository. Defer
implementation until a host requires it; keep SQLAlchemy Core and its driver
optional, outside core imports and dependencies.

## Verified evidence

Inventory checked at `4c39adf6beaf85a5facb3590b77c885c56c90755`;
remote main and PR #164 base are `1844d41e0caad5a458a16bc16d3dcc54444b35fc`
(main confirmed with `git ls-remote`). Metadata is UM 0.6.10;
nested sources are app-factory v0.7.9 / my-auth v0.5.7. The lock resolves
my-auth to `bd0775f1993299b27d2bbd785dd252821dbff06e`.
Historical my-auth#1 and v0.6.5/v0.5.4 research do not prove current
Postgres support or a consumer requirement. Consumer claims remain unverified.

Paths below are relative to `src/my_usermanager/`.

| Contract | Current surface | Available implementations |
| --- | --- | --- |
| User (`stores.py`) | Create/get/update, case-insensitive username lookup, ordered filtered pages, active count | `memory.py:MemoryUserStore`, `adapters/sqlite_stores.py:SQLiteUserStore` |
| Role (`stores.py`) | Get and ordered list; no mutation API | `memory.py:MemoryRoleStore`, also reused by SQLite; static `permissions.py:BUILTIN_ROLES`, no role table |
| Grant (`stores.py`) | Add/remove scoped role or permission grants, ordered user grants | MemoryGrantStore / SQLiteGrantStore in the same files |
| Audit (`stores.py`) | Append unique events, filtered append-order pages | MemoryAuditStore / SQLiteAuditStore in the same files |
| Invitation (`invitations.py`) | Create/get/pending lookup/update; metadata excludes raw tokens | MemoryInvitationStore / `adapters/sqlite_invitations.py:SQLiteInvitationStore` |

All five contracts are synchronous. Active count excludes pending/deleted users.
SQLiteUserStore also implements identity linking from `subjects.py`; Memory
has no equivalent linking API. SQLite schema v5 enforces unique identity pairs
and lowercased usernames; Memory uses casefold, so Unicode parity is unresolved.
SQLite grants require an existing user; Memory does not. SQLite invitation
constraints and error mapping differ from Memory, and invitation mutations
commit directly, even though schema initialization supports external mode.
SQLite audit order uses row order; Postgres needs an explicit append sequence.

`adapters/my_auth_sqlite.py` coordinates explicit schema initialization,
supported migrations and shared SQLite registration transactions. It exposes
users, static roles and grants, not all five stores. Session and OIDC SQLite
adapters are separate. `install_local_identity` is SQLite composition, not a
Postgres installer. Store contracts alone do not guarantee last-admin safety;
`UserManager` and `GrantAdminService` accept an atomic context factory.

## Proposed ownership and boundaries

Future small modules: `users`, `roles`, `grants`, `audit`, `invitations`,
plus `database` for resources/transactions and `schema` for UM migrations.
Reuse the static role catalogue; create no persistent role API or scaffolding.

The adapter disposes engines it creates and closes its checked-out connections
on success or failure. The host disposes borrowed engines and closes borrowed
connections. Transaction-bound stores must not outlive their connection or
commit/close a host-owned transaction. Adapter-owned units commit on success
and roll back on failure; external units require an open shared transaction.
Specify savepoints and map constraint errors before implementation.

UM owns versioned UM migration definitions; the host explicitly schedules
serialized migration execution. No DDL in imports, constructors or CRUD;
unsupported schemas fail closed. my-auth owns credential migrations.
Use synchronous SQLAlchemy Engine/Connection to match current contracts;
hosts move blocking I/O outside event loops. Async API needs a separate decision.

Postgres passkey + UM atomicity is unresolved, not supported. It requires a
verified my-auth credential backend on the same Connection and outer
transaction, without independent commits. Sharing a DSN or engine is insufficient.
Failure on either side must roll back both. Credential CAS belongs to my-auth,
not UserStore; passkey ceremony stays in my-auth.

## Future implementation gates

Name a consumer; resolve normalization, invitation parity, migration versions
and external transaction factories before claiming compatibility. Reuse existing
contract checks for available Memory/SQLite and future Postgres implementations.
Run real-Postgres integration checks for unique identity/username, credential
CAS, savepoint/outer rollback and concurrent last-admin grant/user mutations.
Use independent connections, synchronized workers, operation/error assertions
and fresh-connection final-state checks. Serialize all last-admin mutations on
a shared guard, not only individual users. Inject credential and UM failures
and verify rollback of both; pin the compatible my-auth backend first.
SQLite results cannot substitute for Postgres concurrency evidence.

Delivery requires frozen installation, the full CONTRIBUTING gate, isolated
core import without optional dependencies, and required PR checks on the final
SHA. No Postgres checks are claimed here. Required GitHub checks remain
unverified: public branch rules are empty, protection requires authentication,
gh has no authorization and origin push is disabled. Missing CI access is a
delivery blocker, not a passing result. Removing the three changed count_active
docstrings conflicts with Ruff D102; strict typing also reports existing test
errors. Neither check is weakened, and unaffected tests remain unchanged.
