# FastAPI/Jinja/HTMX Browser + Design QA Evidence

## DoneClaim

- **Task**: Complete the remaining Browser/design QA evidence fix for the live composed FastAPI/Jinja/HTMX example.
- **Status**: **PASS**. The stale non-secure fallback favicon entry is gone, browser evidence is internally consistent, and visual evidence now includes an accepted screenshot diff/baseline artifact with pixel/OCR findings for all 19 PNGs.
- **Workdir**: `/Users/mini-m4-1/Developer/my-usermanager`
- **Editable dependency used read-only**: `/Users/mini-m4-1/Developer/my-auth`
- **Product source edits in this evidence-fix run**: none. Existing source fixes were only observed:
  - `examples/fastapi_htmx/app.py` contains `/favicon.ico` returning inline SVG as `image/svg+xml`.
  - `src/my_usermanager/adapters/fastapi_htmx/templates/base.html` uses canonical `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js`.
- **No commits/staging/push/amend/branch/tag/publish/PR**: none performed.
- **Ledger**: `.omo/start-work/ledger.jsonl` was not edited by this worker.

## Evidence files written/refreshed

- `.omo/evidence/fastapi-htmx-browser-qa.md`
- `.omo/evidence/fastapi-htmx/browser-qa-results.json`
- `.omo/evidence/fastapi-htmx/browser-qa-summary.json`
- `.omo/evidence/fastapi-htmx/fallback-nonsecure-results.json`
- `.omo/evidence/fastapi-htmx/consistency-results.json`
- `.omo/evidence/fastapi-htmx/screenshot-metadata.json`
- `.omo/evidence/fastapi-htmx/screenshot-contact-sheet.png`
- `.omo/evidence/fastapi-htmx/screenshot-contact-sheet-annotated.png`
- `.omo/evidence/fastapi-htmx/screenshot-visual-review.json`
- `.omo/evidence/fastapi-htmx/screenshot-visual-review.md`
- `.omo/evidence/fastapi-htmx/screenshot-ocr.json`
- `.omo/evidence/fastapi-htmx/*.ocr.txt` sidecars for the 19 PNGs
- `.omo/evidence/fastapi-htmx/healthcheck.txt`
- `.omo/evidence/fastapi-htmx/server.log`
- `.omo/evidence/fastapi-htmx/server.pid`
- `.omo/evidence/fastapi-htmx/shutdown-check.txt`
- `.omo/evidence/fastapi-htmx/final-git-state.txt`
- 19 refreshed PNG screenshots:
  - `account-1280.png`, `account-375.png`, `account-768.png`
  - `admin-users-1280.png`, `admin-users-375.png`, `admin-users-768.png`
  - `admin-users-after-disable.png`, `admin-users-after-enable.png`
  - `auth-login-1280.png`, `auth-login-375.png`, `auth-login-768.png`
  - `auth-login-nonsecure-context.png`, `auth-login-unsupported-webauthn.png`
  - `auth-register-1280.png`, `auth-register-375.png`, `auth-register-768.png`
  - `auth-register-nonsecure-context.png`, `auth-register-registration-closed-route.png`, `auth-register-unsupported-webauthn.png`

## Commands and results

### Initial directory and git state

```bash
rtk ls "/Users/mini-m4-1/Developer/my-usermanager" && rtk ls "/Users/mini-m4-1/Developer/my-auth"
```

Result: both directories exist. `my-usermanager` contains `.github/`, `.omo/`, `examples/`, `src/`, `tests/`, `pyproject.toml`, and `uv.lock`; `my-auth` contains `src/`, `tests/`, `pyproject.toml`, and `uv.lock`.

```bash
GIT_MASTER=1 git status --short && GIT_MASTER=1 git diff --stat && GIT_MASTER=1 git diff --staged --stat && GIT_MASTER=1 git branch --show-current && GIT_MASTER=1 git rev-parse HEAD
```

Result: worktree was already dirty from parent/plan implementation changes; `git diff --staged --stat` printed no staged diff. Branch was `fastapi-htmx-adapter`; HEAD was `c61c1e851df43a5f184257aef510bf801a07e14c`.

### Live server and health

Started from `/Users/mini-m4-1/Developer/my-usermanager` with the required command, wrapped only for PID/log/health capture:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync --with-editable /Users/mini-m4-1/Developer/my-auth --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" --with "uvicorn[standard]>=0.32" uvicorn examples.fastapi_htmx.app:app --host 127.0.0.1 --port 8765 > ".omo/evidence/fastapi-htmx/server.log" 2>&1 &
```

Health check output in `.omo/evidence/fastapi-htmx/healthcheck.txt`:

```text
ok
health_status=ok
server_pid=64644
```

Port receipt while running:

```text
python3.1 64647 ... TCP 127.0.0.1:8765 (LISTEN)
```

### Browser/Playwright evidence regeneration

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync --with playwright --with pillow python - <<'PY'
# Playwright script captured 12 route screenshots at 375/768/1280, HTMX row swaps,
# registration-denied API edge via BrowserContext APIRequestContext, favicon status,
# unsupported-WebAuthn states, and non-secure host fallback states.
PY
```

Result summary from `.omo/evidence/fastapi-htmx/browser-qa-summary.json`:

```json
{
  "console_errors": [],
  "failed_requests": [],
  "page_errors": [],
  "non_ok_responses": [],
  "blocking_non_ok_responses": [],
  "htmx_redirect_responses": [],
  "htmx_non_ok_responses": [],
  "overflow_failures": [],
  "favicon_status": 200,
  "favicon_content_type": "image/svg+xml",
  "htmx_disable_fragment_only": true,
  "htmx_enable_fragment_only": true,
  "htmx_url_unchanged": true,
  "registration_denied_403": true,
  "registration_denied_no_cookie": true,
  "required_route_screenshots": 12,
  "screenshot_count": 19
}
```

Non-secure fallback reliability recorded in `browser-qa-results.json`: Chromium was launched with `--host-resolver-rules=MAP notsecure.test 127.0.0.1`, preserving URL host `http://notsecure.test:8765` while routing to the local server. Both non-secure pages verified `isSecureContext=false`, `PublicKeyCredential=false`, `navigator.credentials=false`, visible unsupported text, and `data-state="error"`.

`.omo/evidence/fastapi-htmx/fallback-nonsecure-results.json` now has empty `console` arrays for both entries and no `/favicon.ico` 404 entry.

### Evidence consistency check

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python - <<'PY'
# Validated browser-qa-results.json, browser-qa-summary.json, and fallback-nonsecure-results.json.
PY
```

Result from `.omo/evidence/fastapi-htmx/consistency-results.json`:

```json
{
  "status": "PASS",
  "errors": [],
  "summary": {
    "console_errors": 0,
    "failed_requests": 0,
    "favicon_content_type": "image/svg+xml",
    "favicon_status": 200,
    "htmx_redirect_responses": 0,
    "non_ok_responses": 0,
    "overflow_failures": 0,
    "page_errors": 0,
    "screenshot_count": 19
  }
}
```

### Visual review artifact

Direct multimodal/model image inspection was unavailable (`look_at`/image input could not analyze the PNG). I therefore produced the accepted alternative: a screenshot diff/baseline report with pixel-level findings, an annotated contact sheet, and OCR over all 19 PNGs.

```bash
tesseract --version
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync --with pillow python - <<'PY'
# Generated screenshot-contact-sheet-annotated.png, screenshot-visual-review.{json,md},
# screenshot-ocr.json, per-PNG OCR sidecars, per-image bounding boxes/margins,
# edge-touch checks, dominant palettes, and selected screenshot diffs.
PY
```

Result from `.omo/evidence/fastapi-htmx/screenshot-visual-review.md`:

- Verdict from pixel/OCR artifact: **PASS**.
- 19/19 screenshots covered.
- All screenshots load as PNG/RGB and are not blank/single-color.
- Edge-touch risk count: `0`.
- Pixel content bounding boxes stay inside image dimensions.
- Dominant palettes are neutral warm backgrounds/cards with dark text and muted borders, consistent with the calm admin-console design direction.
- HTMX active/disabled diff is localized (`diff_ratio=0.012391`, similarity `98.761`) and confirms a row-state visual change, not a full-page replacement.
- Secure vs non-secure auth diff is localized to the status/form region (`auth-login` similarity `95.608`; `auth-register` similarity `96.044`).
- Non-secure and unsupported fallback screenshots contain unsupported-WebAuthn text in OCR/DOM evidence.

Review notes: Tesseract did not confidently OCR the words `Disabled`/`Active` in the two HTMX after-state screenshots, but DOM/browser evidence captured exact row text (`Disabled` after disable and `Active` after enable), and the pixel diff localized the visual row-state change.

### Shutdown and port check

```bash
server_pid=$(cat ".omo/evidence/fastapi-htmx/server.pid"); child_pids=$(pgrep -P "$server_pid" || true); if [ -n "$child_pids" ]; then kill $child_pids; fi; kill "$server_pid" 2>/dev/null || true; sleep 1; lsof -nP -iTCP:8765 -sTCP:LISTEN
```

`.omo/evidence/fastapi-htmx/shutdown-check.txt`:

```text
shutdown_check=stopped
server_pid=64644
child_pids=64647
port_8765=closed
```

Follow-up `lsof -nP -iTCP:8765 -sTCP:LISTEN` produced no output.

## Browser/design observables

- `/favicon.ico`: `200`, `image/svg+xml`, server log shows `GET /favicon.ico HTTP/1.1" 200 OK`; no favicon console error.
- HTMX canonical URL: network evidence includes `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js`; `htmx_redirect_responses=[]`.
- Console/page/network: no console errors, page errors, failed requests, non-OK responses, blocking non-OK responses, or HTMX non-OK responses in required JSON evidence.
- HTMX disable/enable: target user row changed Active -> Disabled -> Active; responses start with `<tr>` and do not contain a full document; URL remained `http://127.0.0.1:8765/admin/users`.
- Registration denied edge: Playwright `BrowserContext` API request returned JSON 403 `{"detail":"passkey registration is not allowed"}` and did not set `passkey_challenge` cookie; it did not create browser console noise.
- Unsupported/non-secure fallback: captured via `http://notsecure.test:8765`; JSON shows `isSecureContext=false`, `hasPublicKeyCredential=false`, `hasNavigatorCredentials=false`, `statusState="error"`, and visible unsupported text.
- Keyboard/focus/labels/live regions: `tab_probe`, `labelledControls`, focus style data, headings, links, and live regions are captured in `browser-qa-results.json` for sampled routes/states.
- Horizontal overflow: none at 375/768/1280 for login, register, account, and admin users pages.

## Adversarial class outcomes

- **dirty_worktree**: PASS-with-note. Worktree was already dirty before this delegated run; this worker made evidence-only changes and staged nothing.
- **stale_state**: PASS. `fallback-nonsecure-results.json`, `browser-qa-results.json`, and `browser-qa-summary.json` were regenerated in one server/browser run; stale favicon 404 contradiction is removed.
- **misleading_success_output**: PASS. Consistency script fails on any console/page/request/non-OK/redirect/overflow mismatch; output recorded `status=PASS` with `errors=[]`.
- **generated_stale_artifacts**: PASS. Screenshots, contact sheet, OCR, metadata, and visual review were regenerated from the same screenshot set (`screenshot_count=19`).
- **security_policy_ownership**: PASS. Registration-denied 403 was observed through APIRequestContext without granting registration or setting `passkey_challenge`; host-owned callbacks remain in source.
- **design_scope**: PASS. No product UI source edits were made; visual evidence is evidence-only.
- **hung_long_commands**: PASS. Server PID/child PID recorded and shut down; port 8765 closed.
- **dirty_generated_cleanup**: PASS. No cache cleanup/product generated edits were made; evidence is limited to allowed `.omo/evidence` paths.
- **browser_flakiness**: PASS. Non-secure fallback used real non-secure host + host resolver, not broken capability mocking.
- **accessibility_regression**: PASS. Sampled labels, focus, headings, main landmarks, status/live regions, and tab probes captured; no browser evidence failure.
- **visual_evidence_integrity**: PASS-with-note. Direct model image input was unavailable, but accepted screenshot diff/baseline evidence was produced with pixel/OCR findings, annotated contact sheet, non-blank checks, edge-touch checks, and localized diff observations.

## Risks/notes

- Direct multimodal image-model inspection was unavailable in this environment. The PASS relies on the user-accepted alternative: screenshot diff/baseline report with meaningful visual findings.
- Initial worktree contained parent/source changes unrelated to this evidence-only run. No product source was edited here.
- No commits, staging, pushes, amends, branch changes, tags, publishing, or PRs were performed.
