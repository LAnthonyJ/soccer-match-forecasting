import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.billing.models import SubscriptionRecord


VALID_CARD_TIERS = {"basic", "vip", "admin"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return secrets.token_urlsafe(24).replace("-", "").replace("_", "")


@dataclass
class CardCodeRecord:
    code_hash: str
    code_value: str | None
    tier: str
    duration_days: int
    created_at: str
    batch_id: str | None = None
    platform: str | None = None
    used_by: str | None = None
    used_at: str | None = None
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CardCodeRecord":
        normalized = dict(payload)
        normalized.setdefault("code_value", None)
        normalized.setdefault("batch_id", None)
        normalized.setdefault("platform", None)
        normalized.setdefault("used_by", None)
        normalized.setdefault("used_at", None)
        normalized.setdefault("disabled", False)
        return cls(**normalized)


class CardCodeStore:
    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.codes_path = self.base_dir / "card_codes.json"

    def _read(self) -> dict[str, Any]:
        if not self.codes_path.exists():
            return {"codes": {}}
        return json.loads(self.codes_path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.codes_path.parent.mkdir(parents=True, exist_ok=True)
        self.codes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, record: CardCodeRecord) -> None:
        payload = self._read()
        payload.setdefault("codes", {})[record.code_hash] = record.to_dict()
        self._write(payload)

    def all_records(self) -> list[CardCodeRecord]:
        records = self._read().get("codes", {}).values()
        return [CardCodeRecord.from_dict(raw) for raw in records]

    def get_by_code(self, code: str) -> CardCodeRecord | None:
        raw = self._read().get("codes", {}).get(_hash_code(code))
        return CardCodeRecord.from_dict(raw) if raw else None


class CardCodeService:
    def __init__(self, store: CardCodeStore, subscription_service):
        self.store = store
        self.subscription_service = subscription_service

    def generate_codes(
        self,
        tier: str,
        duration_days: int,
        count: int,
        batch_id: str | None = None,
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        if tier not in VALID_CARD_TIERS:
            raise ValueError(f"Invalid tier: {tier}")
        if duration_days < 1:
            raise ValueError("duration_days must be >= 1")
        if count < 1:
            raise ValueError("count must be >= 1")

        now = _utc_now().isoformat()
        generated = []
        for _ in range(count):
            code = _generate_code()
            record = CardCodeRecord(
                code_hash=_hash_code(code),
                code_value=code,
                tier=tier,
                duration_days=duration_days,
                created_at=now,
                batch_id=(batch_id or "").strip() or None,
                platform=(platform or "").strip() or None,
            )
            self.store.save(record)
            generated.append({
                "code": code,
                "tier": tier,
                "duration_days": duration_days,
                "batch_id": record.batch_id,
                "platform": record.platform,
                "created_at": record.created_at,
            })
        return generated

    def list_codes(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for record in self.store.all_records():
            if batch_id and record.batch_id != batch_id:
                continue
            expires_at = None
            if record.used_by:
                existing = self.subscription_service.get_subscription(record.used_by)
                expires_at = existing.expires_at if existing else None
            rows.append({
                "code": record.code_value or "",
                "tier": record.tier,
                "duration_days": record.duration_days,
                "created_at": record.created_at,
                "batch_id": record.batch_id,
                "platform": record.platform,
                "used_by": record.used_by,
                "used_at": record.used_at,
                "disabled": record.disabled,
                "expires_at": expires_at,
            })
        rows.sort(key=lambda row: (row["created_at"], row["code"]))
        return rows

    def disable_code(self, code: str) -> dict[str, Any]:
        record = self.store.get_by_code(code)
        if record is None:
            raise ValueError("Invalid card code")
        record.disabled = True
        self.store.save(record)
        return {
            "code": record.code_value or code,
            "tier": record.tier,
            "disabled": True,
        }

    def get_code_status(self, code: str) -> dict[str, Any]:
        record = self.store.get_by_code(code)
        if record is None:
            raise ValueError("Invalid card code")
        expires_at = None
        if record.used_by:
            existing = self.subscription_service.get_subscription(record.used_by)
            expires_at = existing.expires_at if existing else None
        return {
            "code": record.code_value or code,
            "tier": record.tier,
            "duration_days": record.duration_days,
            "created_at": record.created_at,
            "batch_id": record.batch_id,
            "platform": record.platform,
            "used_by": record.used_by,
            "used_at": record.used_at,
            "disabled": record.disabled,
            "expires_at": expires_at,
        }

    def redeem_code(self, username: str, code: str) -> dict[str, Any]:
        cleaned_user = (username or "").strip()
        cleaned_code = (code or "").strip()
        if not cleaned_user:
            raise ValueError("username required")
        if not cleaned_code:
            raise ValueError("card_code required")

        record = self.store.get_by_code(cleaned_code)
        if record is None:
            raise ValueError("Invalid card code")
        if record.disabled:
            raise ValueError("Card code disabled")
        if record.used_by and record.used_by == cleaned_user:
            existing = self.subscription_service.get_subscription(cleaned_user)
            if existing and existing.expires_at:
                return {
                    "username": cleaned_user,
                    "tier": existing.tier,
                    "expires_at": existing.expires_at,
                    "reused": True,
                }
            raise ValueError("Card code already used")
        if record.used_by:
            raise ValueError("Card code already used")

        now = _utc_now()
        existing = self.subscription_service.get_subscription(cleaned_user)
        base_time = now
        if existing and existing.expires_at:
            try:
                expires = datetime.fromisoformat(existing.expires_at)
                if expires > now:
                    base_time = expires
            except ValueError:
                pass

        expires_at = base_time + timedelta(days=record.duration_days)
        sub = SubscriptionRecord(
            user_id=cleaned_user,
            tier=record.tier,
            status="active",
            starts_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            source_order_id=f"card:{record.code_hash[:12]}",
            updated_at=now.isoformat(),
        )
        self.subscription_service.store.save_subscription(sub)

        record.used_by = cleaned_user
        record.used_at = now.isoformat()
        self.store.save(record)
        return {
            "username": cleaned_user,
            "tier": record.tier,
            "expires_at": sub.expires_at,
            "reused": False,
        }
