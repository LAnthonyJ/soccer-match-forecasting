"""
System health checks for production monitoring.

Checks: model availability, cache writability, Sportmonks reachability,
latest prediction freshness, runtime config, storage paths.
"""
import os
import time
from datetime import datetime, timezone

from src.config.runtime import load_runtime_config

_CACHE_DIR = load_runtime_config().data_dir / "prediction_snapshots"


def check_model_available() -> dict:
    try:
        from src.models.score_models import load_score_models
        models = load_score_models()
        dc = models.get("dixon_coles")
        return {"ok": dc is not None, "detail": "DC model loaded" if dc else "DC model missing"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_cache_writable(probe_cache_dir: bool = True) -> dict:
    import tempfile

    cache_error = None
    if probe_cache_dir:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=str(_CACHE_DIR), prefix=".health_", suffix=".tmp", delete=True,
            ) as handle:
                handle.write("ok")
            return {"ok": True, "detail": "cache writable"}
        except Exception as exc:
            cache_error = str(exc)

    try:
        with tempfile.NamedTemporaryFile(mode="w", prefix="health_", suffix=".tmp", delete=True) as handle:
            handle.write("ok")
        detail = "cache writable (system temp fallback)" if probe_cache_dir else "cache writable (system temp)"
        return {"ok": True, "detail": detail}
    except Exception as exc:
        if probe_cache_dir:
            return {"ok": False, "detail": f"cache_dir: {cache_error}; system_temp: {exc}"}
        return {"ok": False, "detail": f"system_temp: {exc}"}


def check_sportmonks_available() -> dict:
    try:
        if not os.environ.get("SPORTMONKS_API_KEY", ""):
            return {"ok": False, "detail": "SPORTMONKS_API_KEY not set"}
        _ = _get("/leagues", {"per_page": 1}, cache_ttl_min=5)
        return {"ok": True, "detail": "API reachable"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_latest_prediction_age(max_age_hours: int = 24) -> dict:
    json_files = sorted(_CACHE_DIR.glob("**/*.json"))
    if not json_files:
        return {"ok": True, "detail": "no cached predictions yet"}
    latest = json_files[-1]
    age_hours = (time.time() - latest.stat().st_mtime) / 3600
    ok = age_hours < max_age_hours
    return {
        "ok": ok,
        "detail": f"latest cache: {latest.name} ({age_hours:.1f}h old, max {max_age_hours}h)",
    }


def check_refresh_jobs() -> dict:
    try:
        from src.jobs.job_store import RefreshJobStore

        path = load_runtime_config().data_dir / "jobs" / "refresh_runs.json"
        store = RefreshJobStore(path)
        latest = store.latest_run()
        if latest is None:
            return {"ok": True, "detail": "no refresh runs yet"}
        if latest.status in ("failed", "partial_failure"):
            return {"ok": False, "detail": f"latest refresh run {latest.run_id}: {latest.status}"}
        return {"ok": True, "detail": f"latest refresh run {latest.run_id}: {latest.status}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_runtime():
    try:
        cfg = load_runtime_config()
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}
    status = "ok"
    warnings = []
    if cfg.is_prod and not cfg.sportmonks_api_key_present:
        status = "warning"
        warnings.append("SPORTMONKS_API_KEY missing")
    return {
        "status": status,
        "app_env": cfg.app_env,
        "base_url": cfg.app_base_url,
        "enable_h5": cfg.enable_h5,
        "warnings": warnings,
    }


def check_storage_paths(probe_writable: bool = True):
    import tempfile

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
        if not probe_writable:
            exists = path.is_dir()
            paths[name] = {"path": str(path), "exists": exists, "writable": exists}
            if not exists:
                failed = True
            continue

        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            paths[name] = {"path": str(path), "exists": False, "writable": False}
            failed = True
            continue

        exists = path.is_dir()
        writable = exists
        if exists:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", dir=str(path), prefix=".health_", suffix=".tmp", delete=True,
                ) as handle:
                    handle.write("ok")
                writable = True
            except OSError:
                writable = False

        paths[name] = {"path": str(path), "exists": exists, "writable": writable}
        if not exists or not writable:
            failed = True

    return {"status": "failed" if failed else "ok", "paths": paths}


def _failed(item: dict) -> bool:
    if "status" in item:
        return item["status"] == "failed"
    return not item.get("ok", False)


def check_all() -> dict:
    results = {
        "model": check_model_available(),
        "cache": check_cache_writable(),
        "api": check_sportmonks_available(),
        "prediction_age": check_latest_prediction_age(),
        "refresh_jobs": check_refresh_jobs(),
        "runtime": check_runtime(),
        "storage": check_storage_paths(),
    }
    healthy = not any(_failed(value) for value in results.values())
    return {
        "healthy": healthy,
        "status": "ok" if healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": results,
    }


def check_all_live() -> dict:
    """Fast liveness check - skips model loading, API reachability, and business-dir probes."""
    results = {
        "cache": check_cache_writable(probe_cache_dir=False),
        "prediction_age": check_latest_prediction_age(),
        "refresh_jobs": check_refresh_jobs(),
        "runtime": check_runtime(),
        "storage": check_storage_paths(probe_writable=False),
    }
    blocking = ("cache", "refresh_jobs", "runtime", "storage")
    healthy = not any(_failed(results[name]) for name in blocking)
    return {
        "healthy": healthy,
        "status": "ok" if healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": results,
    }


_last_cache_updated = 0.0
_cached_leagues = None


def _get(endpoint: str, params: dict | None = None, cache_ttl_min: int = 5) -> dict:
    """Lightweight GET with short-lived in-memory cache for health checks."""
    global _last_cache_updated, _cached_leagues
    from src.data.sportmonks_client import _get as client_get

    return client_get(endpoint, params, cache_ttl_min=cache_ttl_min)
