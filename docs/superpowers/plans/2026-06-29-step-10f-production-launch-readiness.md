# Step 10F Production Launch Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the H5 paid prediction product deployable, smoke-testable, observable, and rollback-ready for a first paid beta launch.

**Architecture:** Keep the existing Flask API and static H5 frontend as the deployable unit. Add explicit runtime configuration, production entrypoints, browser/API smoke tests, deployment docs, and launch gates without changing prediction model behavior.

**Tech Stack:** Python Flask, unittest, JSON file stores, static H5 JavaScript, PowerShell-friendly CLI commands, optional `gunicorn` for Linux production hosting.

---

## Scope

Step 10F is the production-readiness layer after Step 10E. It should not add new paid features, new model logic, or a real payment provider. It should make the current product safer to run and easier to verify.

In scope:

- Runtime environment configuration.
- Production server entrypoint.
- Release smoke test CLI.
- H5/API contract smoke tests.
- Cache, billing, jobs, and prediction directory readiness checks.
- Deployment guide.
- Launch checklist.
- Rollback checklist.
- Minimal production documentation.

Out of scope:

- Real WeChat/Alipay/Stripe integration.
- Real authentication/session login.
- CDN setup.
- Cloud vendor automation.
- Database migration away from JSON stores.
- Native mini-program implementation.
- Model accuracy changes.

## File Structure

Create:

- `src/config/runtime.py`: Central runtime configuration loader for environment variables, data paths, allowed origins, and deployment mode.
- `src/server.py`: Production import target that exposes `app` and applies runtime config.
- `src/smoke_test_launch.py`: End-to-end launch smoke test using Flask test client by default and optional live base URL.
- `tests/test_runtime_config.py`: Unit tests for environment config defaults and overrides.
- `tests/test_launch_smoke.py`: Tests for smoke test result aggregation and failure handling.
- `tests/test_server_entrypoint.py`: Tests that the production entrypoint exposes the Flask app and expected config.
- `docs/deployment_guide.md`: Local and production deployment guide.
- `docs/launch_checklist.md`: Human launch checklist and rollback procedure.
- `.env.example`: Required and optional environment variables.
- `requirements.txt`: Minimal runtime and test dependencies for deployment.

Modify:

- `src/api/app.py`: Read runtime config for static H5 enablement, app environment, and response safety defaults.
- `src/monitoring/health.py`: Add launch-readiness checks for data directories and configured environment.
- `PROJECT_PROGRESS.md`: Add Step 10F status and launch command summary.
- `.gitignore`: Ignore local `.env`, deployment logs, and transient smoke output.

Do not modify:

- Prediction model files.
- Billing business rules.
- Subscription data model.
- H5 product behavior, except for environment-safe API base handling if needed.

## Runtime Environment Contract

Environment variables:

- `APP_ENV`: `dev`, `test`, or `prod`. Default `dev`.
- `APP_BASE_URL`: Public base URL used by docs and optional smoke tests. Default `http://localhost:5000`.
- `DATA_DIR`: JSON storage directory. Default `data`.
- `ENABLE_H5`: `true` or `false`. Default `true`.
- `SPORTMONKS_API_TOKEN`: Optional in local dev, expected in production when live upstream calls are enabled.
- `PORT`: Flask port. Default `5000`.
- `FLASK_DEBUG`: `1` enables Flask debug. Default `0`.
- `LAUNCH_SMOKE_FIXTURE_ID`: Optional fixture id used by smoke test prediction checks.

Rules:

- `APP_ENV=prod` must not enable Flask debug.
- `ENABLE_H5=false` should make `/h5` return `404`.
- Missing `SPORTMONKS_API_TOKEN` should be a health warning, not a hard failure, because cached predictions can still serve.
- Data directories should be created or checked explicitly before launch.

## Task 1: Runtime Configuration

**Files:**

- Create: `src/config/runtime.py`
- Create: `tests/test_runtime_config.py`
- Modify: `src/api/app.py`

- [ ] **Step 1: Write failing tests for runtime config defaults**

Create `tests/test_runtime_config.py`:

```python
import os
import unittest
from unittest.mock import patch

from src.config.runtime import RuntimeConfig, load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_are_dev_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.app_env, "dev")
        self.assertEqual(cfg.port, 5000)
        self.assertTrue(cfg.enable_h5)
        self.assertFalse(cfg.flask_debug)
        self.assertEqual(str(cfg.data_dir).replace("\\", "/"), "data")

    def test_env_overrides_are_parsed(self):
        env = {
            "APP_ENV": "prod",
            "APP_BASE_URL": "https://example.com",
            "DATA_DIR": "runtime-data",
            "ENABLE_H5": "false",
            "PORT": "8080",
            "FLASK_DEBUG": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.app_env, "prod")
        self.assertEqual(cfg.app_base_url, "https://example.com")
        self.assertEqual(str(cfg.data_dir), "runtime-data")
        self.assertFalse(cfg.enable_h5)
        self.assertEqual(cfg.port, 8080)

    def test_prod_rejects_debug(self):
        with patch.dict(os.environ, {"APP_ENV": "prod", "FLASK_DEBUG": "1"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("FLASK_DEBUG", str(ctx.exception))

    def test_invalid_boolean_rejected(self):
        with patch.dict(os.environ, {"ENABLE_H5": "maybe"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("ENABLE_H5", str(ctx.exception))
```

- [ ] **Step 2: Run config tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_runtime_config
```

Expected:

```text
ModuleNotFoundError: No module named 'src.config'
```

- [ ] **Step 3: Implement runtime config**

Create `src/config/runtime.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import os


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class RuntimeConfig:
    app_env: str
    app_base_url: str
    data_dir: Path
    enable_h5: bool
    port: int
    flask_debug: bool
    sportmonks_api_token_present: bool

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


def load_runtime_config() -> RuntimeConfig:
    app_env = os.environ.get("APP_ENV", "dev").strip().lower()
    if app_env not in {"dev", "test", "prod"}:
        raise ValueError("APP_ENV must be dev, test, or prod")

    cfg = RuntimeConfig(
        app_env=app_env,
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:5000").strip(),
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        enable_h5=_parse_bool("ENABLE_H5", True),
        port=_parse_int("PORT", 5000),
        flask_debug=_parse_bool("FLASK_DEBUG", False),
        sportmonks_api_token_present=bool(os.environ.get("SPORTMONKS_API_TOKEN")),
    )
    if cfg.is_prod and cfg.flask_debug:
        raise ValueError("FLASK_DEBUG must be disabled when APP_ENV=prod")
    return cfg
```

- [ ] **Step 4: Add package init**

Create `src/config/__init__.py`:

```python
from src.config.runtime import RuntimeConfig, load_runtime_config

__all__ = ["RuntimeConfig", "load_runtime_config"]
```

- [ ] **Step 5: Wire H5 enable flag in Flask**

Modify `src/api/app.py` imports:

```python
from src.config.runtime import load_runtime_config
```

After `app = Flask(__name__)`, add:

```python
_runtime_config = load_runtime_config()
```

Modify `h5_index()`:

```python
@app.route("/h5")
def h5_index():
    if not _runtime_config.enable_h5:
        raise ApiError("not_found", "H5 frontend is disabled", status_code=404)
    return send_from_directory(_H5_DIR, "index.html")
```

Modify `h5_asset(filename)`:

```python
@app.route("/h5/<path:filename>")
def h5_asset(filename):
    if not _runtime_config.enable_h5:
        raise ApiError("not_found", "H5 frontend is disabled", status_code=404)
    return send_from_directory(_H5_DIR, filename)
```

Modify the `__main__` block:

```python
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=_runtime_config.port, debug=_runtime_config.flask_debug)
```

- [ ] **Step 6: Add H5 disabled test**

Add to `tests/test_h5_static.py`:

```python
    def test_h5_can_be_disabled_by_runtime_config(self):
        from src.api import app as api_app
        old = api_app._runtime_config
        api_app._runtime_config = type("Cfg", (), {"enable_h5": False})()
        try:
            with app.test_client() as c:
                r = c.get("/h5")
                try:
                    self.assertEqual(r.status_code, 404)
                    payload = r.get_json()
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["error"]["code"], "not_found")
                finally:
                    r.close()
        finally:
            api_app._runtime_config = old
```

- [ ] **Step 7: Run tests**

Run:

```powershell
python -m unittest tests.test_runtime_config tests.test_h5_static
```

Expected:

```text
OK
```

## Task 2: Production Server Entrypoint

**Files:**

- Create: `src/server.py`
- Create: `tests/test_server_entrypoint.py`

- [ ] **Step 1: Write failing entrypoint test**

Create `tests/test_server_entrypoint.py`:

```python
import unittest


class ServerEntrypointTests(unittest.TestCase):
    def test_server_exposes_flask_app(self):
        from src.server import app

        self.assertEqual(app.name, "src.api.app")

    def test_server_has_runtime_config(self):
        from src.server import runtime_config

        self.assertIn(runtime_config.app_env, {"dev", "test", "prod"})
        self.assertGreater(runtime_config.port, 0)
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m unittest tests.test_server_entrypoint
```

Expected:

```text
ModuleNotFoundError: No module named 'src.server'
```

- [ ] **Step 3: Create server entrypoint**

Create `src/server.py`:

```python
from src.api.app import app
from src.config.runtime import load_runtime_config

runtime_config = load_runtime_config()

__all__ = ["app", "runtime_config"]
```

- [ ] **Step 4: Run entrypoint test**

Run:

```powershell
python -m unittest tests.test_server_entrypoint
```

Expected:

```text
OK
```

## Task 3: Requirements And Environment Example

**Files:**

- Create: `requirements.txt`
- Create: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Create minimal requirements**

Create `requirements.txt`:

```text
Flask>=3.0,<4.0
gunicorn>=22.0,<23.0
requests>=2.31,<3.0
```

If the current code imports additional mandatory third-party packages during full test discovery, add them only after seeing the exact import error.

- [ ] **Step 2: Create environment template**

Create `.env.example`:

```text
APP_ENV=dev
APP_BASE_URL=http://localhost:5000
DATA_DIR=data
ENABLE_H5=true
PORT=5000
FLASK_DEBUG=0
SPORTMONKS_API_TOKEN=
LAUNCH_SMOKE_FIXTURE_ID=
```

- [ ] **Step 3: Update gitignore**

Append to `.gitignore`:

```text
.env
*.log
data/smoke/
```

- [ ] **Step 4: Verify files are present**

Run:

```powershell
Test-Path requirements.txt
Test-Path .env.example
Select-String -Path .gitignore -Pattern "^\\.env$"
```

Expected:

```text
True
True
```

The `Select-String` command should print the `.env` line.

## Task 4: Launch Smoke Test CLI

**Files:**

- Create: `src/smoke_test_launch.py`
- Create: `tests/test_launch_smoke.py`

- [ ] **Step 1: Write smoke result tests**

Create `tests/test_launch_smoke.py`:

```python
import unittest

from src.smoke_test_launch import SmokeCheck, SmokeReport


class LaunchSmokeTests(unittest.TestCase):
    def test_report_success_when_all_checks_pass(self):
        report = SmokeReport([
            SmokeCheck("health", True, "200"),
            SmokeCheck("h5", True, "200"),
        ])
        self.assertTrue(report.ok)
        self.assertEqual(report.failed_count, 0)

    def test_report_failure_when_any_check_fails(self):
        report = SmokeReport([
            SmokeCheck("health", True, "200"),
            SmokeCheck("prediction", False, "503"),
        ])
        self.assertFalse(report.ok)
        self.assertEqual(report.failed_count, 1)

    def test_report_to_dict_is_stable(self):
        report = SmokeReport([SmokeCheck("health", True, "200")])
        payload = report.to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"][0]["name"], "health")
        self.assertEqual(payload["checks"][0]["detail"], "200")
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m unittest tests.test_launch_smoke
```

Expected:

```text
ModuleNotFoundError or ImportError for src.smoke_test_launch
```

- [ ] **Step 3: Implement smoke test structures and checks**

Create `src/smoke_test_launch.py`:

```python
from dataclasses import dataclass
import json
import os
import sys
from typing import Iterable, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


class SmokeReport:
    def __init__(self, checks: Iterable[SmokeCheck]):
        self.checks = list(checks)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_count(self) -> int:
        return sum(1 for check in self.checks if not check.passed)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "failed_count": self.failed_count,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check_response(name: str, status_code: int, content_type: str = "") -> SmokeCheck:
    return SmokeCheck(name, 200 <= status_code < 300, f"{status_code} {content_type}".strip())


def run_flask_smoke(fixture_id: Optional[str] = None) -> SmokeReport:
    from src.api.app import app

    checks: List[SmokeCheck] = []
    with app.test_client() as client:
        response = client.get("/health")
        try:
            checks.append(_check_response("health", response.status_code, response.content_type))
        finally:
            response.close()

        response = client.get("/h5")
        try:
            checks.append(_check_response("h5", response.status_code, response.content_type))
        finally:
            response.close()

        response = client.get("/api/matches/today?limit=5")
        try:
            payload = response.get_json(silent=True) or {}
            checks.append(SmokeCheck(
                "matches_today",
                response.status_code == 200 and payload.get("ok") is True,
                f"{response.status_code} count={payload.get('data', {}).get('count')}",
            ))
        finally:
            response.close()

        if fixture_id:
            response = client.get(f"/api/predictions/{fixture_id}")
            try:
                payload = response.get_json(silent=True) or {}
                checks.append(SmokeCheck(
                    "prediction",
                    response.status_code == 200 and payload.get("ok") is True,
                    f"{response.status_code} code={payload.get('error', {}).get('code')}",
                ))
            finally:
                response.close()

    return SmokeReport(checks)


def _live_get(base_url: str, path: str) -> SmokeCheck:
    url = base_url.rstrip("/") + path
    try:
        req = Request(url, headers={"User-Agent": "launch-smoke/1.0"})
        with urlopen(req, timeout=10) as response:
            return _check_response(path, response.status, response.headers.get("content-type", ""))
    except HTTPError as exc:
        return SmokeCheck(path, False, f"{exc.code} {exc.reason}")
    except URLError as exc:
        return SmokeCheck(path, False, str(exc.reason))


def run_live_smoke(base_url: str, fixture_id: Optional[str] = None) -> SmokeReport:
    paths = ["/health", "/h5", "/api/matches/today?limit=5"]
    if fixture_id:
        paths.append(f"/api/predictions/{fixture_id}")
    return SmokeReport(_live_get(base_url, path) for path in paths)


def main(argv: Optional[list] = None) -> int:
    argv = list(argv or sys.argv[1:])
    fixture_id = os.environ.get("LAUNCH_SMOKE_FIXTURE_ID") or None
    base_url = None
    if "--base-url" in argv:
        index = argv.index("--base-url")
        try:
            base_url = argv[index + 1]
        except IndexError:
            print("--base-url requires a value", file=sys.stderr)
            return 2
    report = run_live_smoke(base_url, fixture_id) if base_url else run_flask_smoke(fixture_id)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run smoke tests**

Run:

```powershell
python -m unittest tests.test_launch_smoke
```

Expected:

```text
OK
```

- [ ] **Step 5: Run local smoke CLI**

Run:

```powershell
python src/smoke_test_launch.py
```

Expected:

```json
{
  "ok": true,
  "failed_count": 0,
  "checks": [...]
}
```

If `/health` is unhealthy because local model/API state is missing, record the exact failed check and continue to Task 5 health readiness instead of weakening the smoke test.

## Task 5: Launch Readiness Health Checks

**Files:**

- Modify: `src/monitoring/health.py`
- Modify: `tests/test_health_and_refresh.py`

- [ ] **Step 1: Inspect current health structure**

Run:

```powershell
Get-Content src/monitoring/health.py
```

Confirm existing `check_all()` returns:

```python
{"healthy": bool, "checks": {...}}
```

- [ ] **Step 2: Add failing tests for runtime readiness**

Add to `tests/test_health_and_refresh.py`:

```python
    def test_runtime_health_reports_environment(self):
        from src.monitoring.health import check_runtime

        result = check_runtime()
        self.assertIn("status", result)
        self.assertIn("app_env", result)
        self.assertIn(result["status"], {"ok", "warning", "failed"})

    def test_storage_health_reports_required_directories(self):
        from src.monitoring.health import check_storage_paths

        result = check_storage_paths()
        self.assertIn("status", result)
        self.assertIn("paths", result)
        self.assertIsInstance(result["paths"], dict)
```

- [ ] **Step 3: Run targeted tests and verify failure**

Run:

```powershell
python -m unittest tests.test_health_and_refresh
```

Expected:

```text
ImportError: cannot import name 'check_runtime'
```

- [ ] **Step 4: Implement health checks**

In `src/monitoring/health.py`, add:

```python
from src.config.runtime import load_runtime_config
```

Add functions:

```python
def check_runtime():
    try:
        cfg = load_runtime_config()
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}
    status = "ok"
    warnings = []
    if cfg.is_prod and not cfg.sportmonks_api_token_present:
        status = "warning"
        warnings.append("SPORTMONKS_API_TOKEN missing")
    return {
        "status": status,
        "app_env": cfg.app_env,
        "base_url": cfg.app_base_url,
        "enable_h5": cfg.enable_h5,
        "warnings": warnings,
    }


def check_storage_paths():
    cfg = load_runtime_config()
    required = {
        "data": cfg.data_dir,
        "billing": cfg.data_dir / "billing",
        "jobs": cfg.data_dir / "jobs",
        "prediction_snapshots": cfg.data_dir / "prediction_snapshots",
    }
    paths = {}
    failed = False
    for name, path in required.items():
        exists = path.exists()
        writable = False
        if exists and path.is_dir():
            try:
                probe = path / ".health_write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                writable = True
            except OSError:
                writable = False
        paths[name] = {"path": str(path), "exists": exists, "writable": writable}
        if not exists or not writable:
            failed = True
    return {"status": "failed" if failed else "ok", "paths": paths}
```

Modify `check_all()` to include:

```python
    checks["runtime"] = check_runtime()
    checks["storage"] = check_storage_paths()
```

If `check_all()` currently computes `healthy` from statuses, ensure `warning` does not make the whole service unhealthy:

```python
healthy = all(item.get("status") != "failed" for item in checks.values())
```

- [ ] **Step 5: Run health tests**

Run:

```powershell
python -m unittest tests.test_health_and_refresh
```

Expected:

```text
OK
```

## Task 6: Deployment Documentation

**Files:**

- Create: `docs/deployment_guide.md`
- Create: `docs/launch_checklist.md`
- Modify: `PROJECT_PROGRESS.md`

- [ ] **Step 1: Create deployment guide**

Create `docs/deployment_guide.md`:

```markdown
# Deployment Guide

## Runtime

The deployable unit is the Flask API plus static H5 frontend.

Production import target:

```text
src.server:app
```

## Local Start

```powershell
python -m pip install -r requirements.txt
python src/api/app.py
```

Open:

```text
http://localhost:5000/h5
```

## Linux Production Start

```bash
python -m pip install -r requirements.txt
APP_ENV=prod FLASK_DEBUG=0 gunicorn "src.server:app" --bind "0.0.0.0:${PORT:-5000}" --workers 2 --timeout 60
```

## Required Environment

```text
APP_ENV=prod
APP_BASE_URL=https://your-domain.example
DATA_DIR=data
ENABLE_H5=true
PORT=5000
FLASK_DEBUG=0
SPORTMONKS_API_TOKEN=<provider-token>
```

## Smoke Test

Local Flask test-client smoke:

```powershell
python src/smoke_test_launch.py
```

Live URL smoke:

```powershell
python src/smoke_test_launch.py --base-url https://your-domain.example
```

## Rollback

Rollback means serving the previous app version with the same `DATA_DIR`.

Before rollback:

- Preserve `data/billing`.
- Preserve `data/prediction_snapshots`.
- Preserve `data/jobs`.
- Do not delete paid subscription records.

After rollback:

```powershell
python src/smoke_test_launch.py --base-url https://your-domain.example
```
```

- [ ] **Step 2: Create launch checklist**

Create `docs/launch_checklist.md`:

```markdown
# Launch Checklist

## Pre-Launch

- [ ] `python -m unittest discover -s tests` passes.
- [ ] `python src/smoke_test_launch.py` passes.
- [ ] `/health` returns HTTP 200 in target environment.
- [ ] `/h5` opens on mobile viewport.
- [ ] `/api/matches/today?limit=5` returns standard envelope.
- [ ] Basic prediction page hides VIP-only fields.
- [ ] VIP user can access `/api/predictions/<id>/vip`.
- [ ] Non-VIP user gets `vip_required` for VIP endpoint.
- [ ] Billing checkout creates a pending order.
- [ ] Mock webhook is disabled or protected before public launch.
- [ ] `DATA_DIR` is backed up.
- [ ] `SPORTMONKS_API_TOKEN` is configured when live upstream refresh is required.

## Launch

- [ ] Deploy `src.server:app`.
- [ ] Run live smoke test.
- [ ] Trigger admin refresh for the next 48 hours.
- [ ] Check latest refresh job status.
- [ ] Open H5 match list.
- [ ] Open one prediction detail page.
- [ ] Verify account/subscription page.

## Rollback

- [ ] Stop new deployment.
- [ ] Restore previous app version.
- [ ] Reuse existing `DATA_DIR`.
- [ ] Run live smoke test.
- [ ] Verify paid subscription lookup still works.
```

- [ ] **Step 3: Update progress document**

Append to `PROJECT_PROGRESS.md`:

```markdown
## Step 10F - Production Launch Readiness

Planned scope:

- Runtime environment config.
- Production server entrypoint.
- Launch smoke test CLI.
- Runtime and storage health checks.
- Deployment guide and launch checklist.

Primary verification:

```powershell
python -m unittest discover -s tests
python src/smoke_test_launch.py
```
```

## Task 7: Full Verification

**Files:**

- No new files.

- [ ] **Step 1: Run targeted Step 10F tests**

Run:

```powershell
python -m unittest tests.test_runtime_config tests.test_server_entrypoint tests.test_launch_smoke tests.test_health_and_refresh tests.test_h5_static
```

Expected:

```text
OK
```

- [ ] **Step 2: Run full regression suite**

Run:

```powershell
python -m unittest discover -s tests
```

Expected:

```text
OK
```

- [ ] **Step 3: Run launch smoke**

Run:

```powershell
python src/smoke_test_launch.py
```

Expected:

```json
{
  "ok": true,
  "failed_count": 0
}
```

- [ ] **Step 4: Start local server manually**

Run:

```powershell
python src/api/app.py
```

Open:

```text
http://localhost:5000/h5
```

Manual checks:

- H5 shell loads.
- Match list either shows fixtures or upstream warning state.
- Account page can save a user id.
- Subscribe page can create a pending order.
- `/health` returns a clear status.

## Implementation Order

1. Runtime config.
2. H5 enable/disable wiring.
3. Server entrypoint.
4. Requirements and environment template.
5. Launch smoke test CLI.
6. Runtime/storage health checks.
7. Deployment guide and launch checklist.
8. Full test suite.
9. Manual H5 smoke test.
10. Update project progress.

## Acceptance Criteria

- `src.server:app` is a stable production import target.
- `APP_ENV=prod FLASK_DEBUG=1` is rejected.
- `/h5` can be disabled with `ENABLE_H5=false`.
- Launch smoke CLI can validate `/health`, `/h5`, and `/api/matches/today`.
- Health output includes runtime and storage readiness.
- Deployment guide documents local start, production start, smoke test, and rollback.
- Full tests pass.

## Follow-Up Step 10G Candidates

- Real payment provider integration.
- Real user authentication/session handling.
- Browser automation test with Playwright or in-app browser.
- Cloud deployment automation.
- JSON store to managed database migration.
- CDN/static asset caching policy.

