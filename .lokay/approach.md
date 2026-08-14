# Approach plan

<!-- lokay-approach source=deterministic repo=mikolaj92/my-usermanager issue=53 -->

Repository: `mikolaj92/my-usermanager`  
Issue: #53 — v0.5.4 gniazduje app-factory v0.6.4, BOM jest v0.6.5

## Goal

BOM chrome is app-factory **v0.6.5**. my-usermanager **v0.5.4** still pins
v0.6.4, so the kit tests against older chrome than the five hosts.

Fail-closed:

- Release **0.5.5**.
- `[tool.uv.sources]` app-factory → tag **v0.6.5**.
- my-auth stays **v0.4.2**. Extra remains `my-auth>=0.4,<0.5`. Do not pin
  my-auth 0.5.x.
- Relock. Tests pass.

## Files likely touched

- `pyproject.toml` — version `0.5.5`; app-factory source tag `v0.6.5`
- `uv.lock` — relock after the source pin
- `CHANGELOG.md` — 0.5.5 pin note
- `src/my_usermanager/__init__.py` — `__version__`
- `tests/test_imports.py`
- `tests/test_my_auth_adapter.py`
- `tests/test_my_auth_fastapi.py`

Same shape as #50 / #51. Deterministic localize matched HTMX auth/evidence
paths via token noise (`app`, `auth`, `source`); those are not the pin.

## Test plan

- `uv lock` after the source change
- Targeted pytest for version assertions
- Broader `pytest` if the lock/install stays usable

## Non-goals

- Do not bump my-auth past v0.4.2
- Do not change the `myauth` extra range `>=0.4,<0.5`
- Do not edit FastAPI/HTMX templates or `omo/evidence`

## Notes

- Trust intentional issue; this plan is evidence for later review, not a human gate.
- Coding agent may refine details but should stay on the stated goal and non-goals.
- Collector boundary: if implementation introduces unbounded collection, ship only a bounded collector patch that starts durably in the background after merge. The coding agent and mill must not populate data or wait for collection to finish.
