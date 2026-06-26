# Contributing

Thanks for your interest in contributing to `my-usermanager`.

## Development Setup

```sh
uv sync
```

## Quality Gate

Run the full local gate before proposing changes:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright src tests
```

## Project Rules

- Keep the core package framework neutral.
- Do not import FastAPI or Pydantic from `my_usermanager` core imports.
- Add tests before implementation changes.
- Keep optional framework code behind extras and adapter modules.
