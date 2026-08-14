# Approach plan

<!-- lokay-approach source=deterministic repo=mikolaj92/my-usermanager issue=56 -->

Repository: `mikolaj92/my-usermanager`  
Issue: #56 — README demo: mini-m4 sciezki i importy, ktorych pakiet nie eksportuje

## Goal

1. Replace machine-local `my-auth` checkout paths in demo commands
   (`/Users/mini-m4-main/Developer/hermes-repos/my-auth` in root README,
   `/Users/mini-m4-1/Developer/my-auth` in `examples/fastapi_htmx/README.md`)
   with the published `my-auth[fastapi-htmx]` extra.
2. Stop documenting `create_usermanager_ui_router` and
   `usermanager_ui_static_files` as public imports. Those names exist in
   adapter internals; `__all__` exports `install_usermanager_ui`.

## Files likely touched

- `README.md`
- `examples/fastapi_htmx/README.md`
- `tests/test_fastapi_htmx_example.py`

## Test plan

- `uv run pytest tests/test_fastapi_htmx_example.py tests/test_fastapi_htmx_adapter.py -k "readme or public_api"`

## Non-goals

- Do not re-export internal router/static helpers.
- Do not change adapter runtime behavior.
- This is not #53 (BOM pin).

## Notes

- Public adapter API already matches the installer contract; this is a docs
  and demo-command fix plus a regression assertion.
