# my-usermanager

`my-usermanager` is planned as a framework-neutral Python package for user management and authorization. It accepts an already-authenticated subject from a host application or authentication provider, then provides authorization and user-management primitives.

Wave 0 contains only the repository bootstrap: packaging metadata, an importable package, tests, documentation placeholders, and CI configuration.

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
