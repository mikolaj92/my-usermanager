# my-usermanager

`my-usermanager` is a framework-neutral Python package for user management and authorization. It accepts an already-authenticated subject from a host application or authentication provider, then provides typed authorization and user-management primitives.

The core package includes immutable domain values, store protocols, in-memory contract implementations, and a safe `UserManager` facade. The facade enforces the package's default policy: administrators manage user access grants, while ordinary users can update only their own basic profile fields such as username, first name, last name, display name, and email.

## Scope

- Core package import: `my_usermanager`
- Distribution name: `my-usermanager`
- Python: `>=3.12`
- Tooling: `uv`, Hatchling, pytest, Ruff, basedpyright
- License: MIT
- Optional future adapter extra: `my-usermanager[fastapi]`

The core package must stay framework neutral. Importing `my_usermanager` must not import FastAPI or Pydantic as side effects.

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright src tests
```

## Version

Initial version: `0.1.0`
