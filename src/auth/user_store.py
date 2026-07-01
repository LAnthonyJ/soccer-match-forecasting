import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class UserRecord:
    user_id: str
    display_name: str = ""
    api_key_hash: str = ""
    created_at: str = ""
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["created_at"]:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserRecord":
        return cls(**payload)


class UserStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_user(self, user: UserRecord) -> None:
        payload = self._read()
        payload.setdefault("users", {})[user.user_id] = user.to_dict()
        self._write(payload)

    def get_user(self, user_id: str) -> UserRecord | None:
        raw = self._read().get("users", {}).get(user_id)
        return UserRecord.from_dict(raw) if raw else None
