from dataclasses import asdict, dataclass
from typing import Any


RUN_STATUSES = {"running", "success", "partial_failure", "failed"}
ATTEMPT_STATUSES = {"refreshed", "skipped", "failed"}


@dataclass
class RefreshJobRun:
    run_id: str
    status: str
    started_at: str
    finished_at: str | None
    window_hours: int
    total: int
    refreshed: int
    skipped: int
    failed: int
    force: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefreshJobRun":
        return cls(**payload)


@dataclass
class RefreshJobAttempt:
    run_id: str
    fixture_id: int
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    snapshot_path: str | None
    error: str | None
    skipped_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefreshJobAttempt":
        return cls(**payload)
