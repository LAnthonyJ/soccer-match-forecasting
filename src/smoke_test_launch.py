from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.path.insert(0, str(Path(__file__).parent.parent))


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
        response = client.get("/health/live")
        try:
            checks.append(_check_response("health_live", response.status_code, response.content_type))
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
    paths = ["/health/live", "/h5", "/api/matches/today?limit=5"]
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
