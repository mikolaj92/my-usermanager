# ADR 0001: Optional Postgres / SQLAlchemy backend

Status: **Proposed (proponowane)**. No Postgres backend or extra is available.
SQLite remains the default durable adapter. Implementation is deferred until a
host requires Postgres and the acceptance gates below are met.

## Evidence and revision boundary

Audit date: 2026-10-14. This follow-up starts at
`961b7e372f21272a8ff97c9ad9de2bf6d7215034` on `lokay/125`, with a clean tree.
`git ls-remote origin refs/heads/main` confirms that remote main still matches
local `origin/main`: `1844d41e0caad5a458a16bc16d3dcc54444b35fc`.
The starting branch adds this ADR, README corrections, production typing and
formatting fixes across eleven files. These changes remain in place.
The ADR exists in the checkout, but not in main; this updates that same document.
Inventory paths were rechecked against the starting branch revision.

The issue's my-usermanager v0.6.5 / my-auth v0.5.4 research is historical,
not the current baseline. Current metadata is my-usermanager 0.6.10; nested
sources pin app-factory v0.7.9 and my-auth v0.5.7. `uv.lock` resolves my-auth to
`bd0775f1993299b27d2bbd785dd252821dbff06e`. The public annotated tag resolves to
that same commit. The installed sources inspected below match that lock.

[my-auth#1](https://github.com/mikolaj92/my-auth/issues/1) was observed closed
on 2026-07-02 in the prior audit. The issue body has no pinned consumer
revision; its claims remain unverified historical evidence. Its body reports
app-local SQLite stores and a pyemitype
SQLAlchemy ORM credential store. These are historical issue claims, not an
audit of those consumers or proof of a library Postgres backend. Closure does
not establish completion of every suggested adapter. In my-auth v0.5.7,
`passkey_models.py` defines `CredentialStore`, `passkey_memory.py` implements
`MemoryCredentialStore`, and `passkey_sqlite.py` implements
`SQLiteCredentialStore`. The inspected package contains no SQLAlchemy adapter.
[fastapi-users](https://github.com/fastapi-users/fastapi-users) is inspiration,
not evidence of an unmet contract or a security defect here.

## Inventory: contracts, implementations, and consumers

Paths in this table are relative to `src/my_usermanager/`.

| Contract | Verified surface and behavior | Available implementation |
| --- | --- | --- |
| User | `stores.py:UserStore`: create, get, case-insensitive username lookup, update, deterministic filtered/paginated list, count active users. Duplicate IDs/usernames and missing updates have typed errors. | `memory.py:MemoryUserStore`; `adapters/sqlite_stores.py:SQLiteUserStore` |
| Role | `stores.py:RoleStore`: get and ordered list only; no mutation API. `permissions.py:BUILTIN_ROLES` is the static built-in catalogue. | `memory.py:MemoryRoleStore`, reused by `SQLiteAuthTransaction`; no SQLite role table |
| Grant | `stores.py:GrantStore`: add/remove role or permission grants, scope-aware ordered listing, typed duplicate/missing errors. SQLite additionally requires an existing user through FK/store validation; Memory does not. | `memory.py:MemoryGrantStore`; `adapters/sqlite_stores.py:SQLiteGrantStore` |
| Audit | `stores.py:AuditStore`: append only, unique event ID, append-order listing with filters and pagination. SQLite uses row order, not timestamp order. | `memory.py:MemoryAuditStore`; `adapters/sqlite_stores.py:SQLiteAuditStore` |
| Invitation | `invitations.py:InvitationStore`: create, get, pending lookup by user, update; unavailable operations raise `InvitationError`. Metadata includes capability ID, expiry, status and approved grants, never the raw activation token. | `invitations.py:MemoryInvitationStore`; `adapters/sqlite_invitations.py:SQLiteInvitationStore` |

`count_active` actually counts `User.is_active` / SQL `status = 'active'`,
not every non-disabled user. Pending and deleted users must not count.
The production docstrings are corrected with this decision; signatures stay
unchanged.

Identity linking is a separate `ExternalIdentityUserStore` contract in
`subjects.py`, implemented by SQLiteUserStore. MemoryUserStore persists the
identities in User values but does not expose those linking methods.
`sqlite_schema.py` version 5 stores users, identities, grants and audit, with
unique `(provider, subject)` and username `lower(username)` indexes, and
cascading user foreign keys for identities/grants. Memory uses Python
`casefold()` for usernames and search; SQLite uses `lower()`. Do not assume
these are equivalent for Unicode. Postgres must pin and test normalization
semantics before claiming parity, rather than inherit server collation.

Invitation parity is also incomplete: SQLite uniquely constrains capability
IDs, one pending invitation per user and the user FK. SQLite create maps
integrity failures to `InvitationError`; update does not map integrity failures.
The contract does not itself specify these extra constraints. Memory checks duplicate
invitation IDs and existing pending users on create, but not all these rules
on update. SQLiteInvitationStore mutations commit directly; its schema
initializer's external mode does **not** make the store transaction-bound.
Do not compose that existing store into an outer atomic unit and claim rollback.

Additional verified adapter boundaries:

- `adapters/sqlite_stores.py` separates operation commits from external
  savepoint mutations. Reads do not initialize schemas.
- `adapters/sqlite_schema.py` owns explicit inspection, bootstrap and supported
  one-shot UM migrations, including orphan-grant rejection.
- `adapters/my_auth_sqlite.py:SQLiteAuthDatabase` coordinates initialization
  and `BEGIN IMMEDIATE` transactions. `SQLiteAuthTransaction` exposes users,
  grants, the static role catalogue and an external credential-store factory;
  it does not expose all five stores automatically. In path-mode `stores()`,
  this follow-up closes the first owned connection if opening the second fails;
  borrowed connections remain caller-owned.
- The shared owner can migrate supported legacy my-auth schemas through
  my-auth's migration API. README's statement that all auth legacy schemas
  are refused is inaccurate and is corrected in this PR.
- `adapters/sqlite_registration.py` writes user, host-provided authentication
  records and the explicitly selected initial role on one connection.
- `adapters/sqlite_sessions.py` provides optional hashed-token metadata,
  expiry and revocation with operation/external transaction modes.
- `adapters/sqlite_oidc_flows.py` provides durable, one-time OIDC flow state
  with operation/external modes. Neither sessions nor OIDC imply a Postgres
  requirement in this decision.
- `admin.py:GrantAdminService` and `manager.py:UserManager` accept an `atomic`
  context factory for last-active-admin checks and mutations. A bare store
  contract does not provide that concurrency guarantee.
- `adapters/my_auth_fastapi.py` normalizes sync/async completion callbacks;
  that HTTP boundary does not turn synchronous stores into async stores.
- `adapters/local_identity.py:install_local_identity` constructs SQLite and
  my-auth SQLite services. It is not a generic Postgres installer.

**Confirmed Postgres consumer: none.** Repository examples use demo host
stores/callbacks, not a Postgres deployment. The canonical installer is a
verified SQLite composition, not evidence that an external host needs
Postgres. The consumers named by historical my-auth#1 have not been audited
at current revisions. This justifies deferring implementation, not omitting
this decision. Host provisioning, domain policy, cookies and CSRF remain
consumer responsibilities.

## Proposed design

### Dependency and sync boundary

Use SQLAlchemy **Core**, its SQL expression and connection layer, in an
explicit optional adapter. Do not add ORM models, SQLAlchemy imports, driver
imports or new dependencies to core. A future `postgres` extra will declare
SQLAlchemy and a compatible synchronous Postgres driver. This PR adds neither.

All five present contracts are synchronous. Implement a synchronous Engine
and Connection first. AsyncEngine/AsyncSession and an async store API require
a separate consumer need and decision. HTTP hosts must arrange blocking I/O
outside their event loop; an async callback wrapper alone is not sufficient.

### Resource, schema, and transaction ownership

| Resource | Proposed owner and lifecycle |
| --- | --- |
| Engine created by adapter from a host-provided DSN | Database adapter owns the pool. Explicit close/shutdown disposes that engine. Do not log the DSN or credentials. |
| Engine supplied by host | Host owns configuration, pool and disposal. Closing the adapter must not dispose the borrowed engine. |
| Connection checked out by adapter | Adapter returns/closes it on every success or exception path. A transaction-bound store must not outlive it. |
| Connection/transaction supplied by host | Host owns close, commit and rollback. Stores must not end that outer transaction. Reject missing external transactions. |
| UM schema and migration definitions | `schema.py` owns versioned UM definitions and explicit migrations. No DDL in imports, constructors or CRUD. Unsupported versions fail closed. |
| Migration execution | Host explicitly schedules startup/deployment migration with suitable privileges and serialization. No automatic destructive migration. my-auth owns its own schema; UM does not migrate credentials independently. |
| Adapter-owned transaction | `database.py` opens one connection and transaction, binds all participating stores, commits on success and rolls back on failure. |
| Host-owned transaction | Stores use the same supplied Connection; optional operation savepoints may isolate failures but never commit the host's work. |

Standalone mutations can own a short transaction. A multi-store unit must
use transaction-bound factories, not five independently connected stores.
Define nested/savepoint behavior explicitly and test continued transaction
usability after a mapped constraint failure. Map database constraints to the
existing domain errors; do not expose driver exceptions as contract outcomes.

For last-admin protection, lock one stable database-wide guard row before
reading the active-admin set and mutating it, across both user transitions
and grant revokes. Locking only the target user permits write skew between
two different administrators. All supported mutation entry points must use
the same boundary. No claim of last-admin safety before the real concurrent
tests pass. Host administrative policy remains injected.

### Passkey + UM atomicity: conditional, not supported yet

SQLite's `SQLiteAuthDatabase.complete_registration` passes one connection
with external transaction mode to my-auth and UM. That verifies SQLite
composition only. A Postgres UM store alone cannot provide atomic passkeys.

Before declaring Postgres passkey compatibility, my-auth must provide a
credential backend accepting the **same SQLAlchemy Connection and outer
transaction**, without an independent commit or connection. Merely using the
same DSN, Engine or database is insufficient. Its registration, identity and
grant writes must commit together. A failure in either library must roll back
both. Passkey verification/ceremony stays in my-auth; no distributed
transaction or compensating deletion is proposed.

CAS (compare-and-set) is present in my-auth's
`CredentialStore.compare_and_set_credential_after_login`, not in UserStore.
There is no public UM user version/CAS API to implement silently. A future
credential adapter must preserve expected sign-count, zero-counter and
non-decreasing-counter behavior. Its acceptance and tests belong to my-auth,
with cross-library composition verified here once that adapter exists.

### Small future modules

All paths below are planned, not files delivered by this PR:

```text
src/my_usermanager/adapters/postgres/
  database.py     engine ownership, connection scope, transaction factories
  schema.py       UM tables, constraints, inspection and explicit migrations
  users.py        UserStore plus identity linking and lifecycle extensions
  roles.py        composition of the existing static catalogue; no role table
  grants.py       scoped grant mutations and ordered queries
  audit.py        append-only storage with explicit stable append sequence
  invitations.py metadata persistence bound to the chosen transaction owner
```

Persist the audit append sequence explicitly rather than use Postgres physical
row order. Pin concurrent ordering semantics in contract documentation before
implementation. Resolve invitation constraint/error parity before shared
factories assert rules absent from Memory. Neither resolution changes the
public API in this PR. Do not add sessions, OIDC stores, persistent role
administration or a replacement local-identity installer without a host need.

## Acceptance gates and future test matrix

Existing `tests/contracts/test_{user,role,grant,audit}_store_contract.py`
instantiate Memory stores directly. They are not yet shared factory suites.
`tests/test_sqlite_adapters.py` separately exercises SQLite and the reused
MemoryRoleStore. Available implementation is not the same as shared coverage.

| Contract | Memory now | SQLite now | Postgres now | Future shared factory |
| --- | --- | --- | --- | --- |
| User | Implemented; contract suite | Implemented; separate adapter tests | Missing | Fresh store/connection; seed identities as appropriate |
| Role | Static catalogue; contract suite | Reuses Memory catalogue; separate tests | Missing composition | Same static catalogue, no database prerequisite |
| Grant | Implemented; contract suite | Implemented; separate adapter tests | Missing | Seed parent user for all backends; separate FK-specific checks |
| Audit | Implemented; contract suite | Implemented; separate adapter tests | Missing | Empty store, stable append order and duplicate checks |
| Invitation | Implemented; lifecycle tests | Implemented; separate invitation tests | Missing | Resolve known parity differences; seed parent user |

In a later implementation PR, reuse the four existing suites through factories
for Memory/SQLite/Postgres. Add
`tests/contracts/test_invitation_store_contract.py` and
`tests/test_postgres_adapters.py` only with real assertions, not empty files.
No new test files or functions are introduced by this design PR.

Required future real-Postgres tests:

1. Unique user IDs, explicitly normalized usernames and external identity
   pairs; concurrent collision attempts yield the defined domain errors.
   Assert the persisted winner, no orphan rows and no partial losing write.
2. Credential CAS with stale expected counts, zero counters and concurrent
   updates; assert final counter and both operation outcomes. This depends
   on the my-auth credential backend, not a new UM CAS API.
3. User + identity + grant + audit + invitation rollback in one shared
   transaction. Inject a failure after earlier writes; inspect every store
   from a new connection. Test savepoint errors and outer rollback too.
4. Parallel last-admin grant revokes, disable/soft-delete transitions, and
   mixed grant/user mutations. Assert at least one active administrator,
   expected rejection and no partial grant/status changes.
5. Shared credential + UM registration: inject failures on either side and
   verify neither persists. Add a successful commit control. This gate stays
   blocked until a compatible my-auth backend is verified at a pinned revision.
6. Migration initialization, supported upgrades, unsupported schemas,
   engine disposal ownership and cleanup on failure.
7. Isolated installation/import without the Postgres extra: build/install
   the base wheel in a clean environment without SQLAlchemy or the driver;
   import core and Memory/SQLite adapters and assert neither optional package
   was imported. Run independently of the dev environment's extras.

Concurrency tests must use independent connections and controlled barriers or
other deterministic synchronization, not sleep-only timing. Inspect final
state from a fresh connection after all workers finish. Check both returned
values and errors. PostgreSQL CI must run a real server with a required DSN;
missing DSN fails its required integration job, not a skip. SQLite cannot
substitute for Postgres locking, isolation, unique constraints or CAS tests.
No Postgres tests have been run or passed in this PR.

## Delivery order and validation boundary

1. Accept this ADR with a named consumer and required host capabilities.
2. Specify normalization, invitation parity, migration versioning and
   transaction-bound factories; confirm my-auth dependency separately.
3. Deliver `database.py` / `schema.py` and resource/migration integration tests.
4. Deliver users and static role composition, then grants and audit, reusing
   contract suites and adding real concurrent last-admin checks.
5. Deliver invitations with external transaction support and rollback checks.
6. Integrate verified my-auth credential transactions and CAS tests before
   advertising full passkey compatibility. Otherwise document UM-only support.

The local required gate is `CONTRIBUTING.md`: `uv run pytest`,
`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run basedpyright src tests`. Keep metadata, nested source pins and lock
consistent if that gate exposes incompatibilities; do not revert necessary
compatibility fixes as unrelated cleanup.

There is no `.github/workflows` in the audited tree. Public GitHub API evidence
on the baseline SHA returned zero check runs and zero commit statuses; its
combined `pending` value is not a passing check. Branch rules returned an
empty list; branch protection inspection returned HTTP 401. No open PR was
found for `mikolaj92:lokay/125`. Therefore required external checks cannot be
fully established without authorized repository evidence. No token was read
or written. Local results cannot certify required CI on the final PR SHA.
The follow-up's unauthenticated requests for check runs at starting SHA
`961b7e3` and open PRs both returned HTTP 403 (public API rate limit).
The earlier observations above are not renewed evidence for this head.
Recheck the actual PR head, required protections and external checks after
publication; final-SHA CI confirmation remains an explicit delivery gate.

Follow-up validation: 333 tests passed; Ruff lint and formatting passed
(110 files). A manual fault-injection check confirmed owned-connection cleanup
when the second connection fails, and preservation of a borrowed connection
on store close. No test files or functions changed.
The prior commit corrected five formatting inconsistencies and nine production
typing errors; those fixes remain in place. The full type gate still reports
**50 errors and 21 warnings**, all in existing test files:
`test_fastapi_htmx_adapter.py`, `test_imports.py`,
`test_invitation_delivery.py`, `test_issuer_swap.py`, and
`test_oidc_relying_party.py`. Production analysis has no remaining diagnostics.
Those tests were not made wrong by this change. The execution constraint allows
existing test edits only when the change makes a test wrong, so this PR does
not rewrite their fixtures, annotations or test functions to hide the failure.
The full gate is not green; completing it requires permission for those test
repairs. No diagnostic settings or dependency pins were weakened.
