import json
from pathlib import Path
from typing import Any

from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": {}, "attempts": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_run(self, run: RefreshJobRun) -> None:
        payload = self._read()
        payload.setdefault("runs", {})[run.run_id] = run.to_dict()
        payload.setdefault("attempts", {}).setdefault(run.run_id, [])
        self._write(payload)

    def get_run(self, run_id: str) -> RefreshJobRun | None:
        raw = self._read().get("runs", {}).get(run_id)
        return RefreshJobRun.from_dict(raw) if raw else None

    def latest_run(self) -> RefreshJobRun | None:
        runs = self._read().get("runs", {})
        if not runs:
            return None
        latest = max(runs.values(), key=lambda item: item.get("started_at", ""))
        return RefreshJobRun.from_dict(latest)

    def append_attempt(self, attempt: RefreshJobAttempt) -> None:
        payload = self._read()
        payload.setdefault("attempts", {}).setdefault(attempt.run_id, []).append(attempt.to_dict())
        self._write(payload)

    def list_attempts(self, run_id: str) -> list[RefreshJobAttempt]:
        raw = self._read().get("attempts", {}).get(run_id, [])
        return [RefreshJobAttempt.from_dict(item) for item in raw]

    def failed_attempts(self, run_id: str) -> list[RefreshJobAttempt]:
        return [item for item in self.list_attempts(run_id) if item.status == "failed"]
