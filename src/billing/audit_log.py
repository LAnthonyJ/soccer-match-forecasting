"""Append-only JSON audit log for billing events."""
import json
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: dict) -> dict:
        record = {
            "event_type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        records = self.list_all()
        records.append(record)
        self.path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return record

    def list_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))
