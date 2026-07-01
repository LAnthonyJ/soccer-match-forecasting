"""
Access control: tier resolution and payload filtering.

User tiers: free -> basic -> vip -> admin
Each tier sees a progressively richer prediction payload.
Backend filtering is mandatory — frontend hiding is not sufficient.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Simulated subscription store (replace with DB in production)
_subscriptions: dict[str, "UserSubscription"] = {}


@dataclass
class UserSubscription:
    user_id: str
    tier: str  # free / basic / vip / admin
    expires_at: datetime | None = None

    def is_active(self) -> bool:
        if self.expires_at is None:
            return True
        return datetime.now(timezone.utc) < self.expires_at

    def effective_tier(self) -> str:
        if not self.is_active():
            return "free"
        if self.tier not in TIER_HIERARCHY:
            return "free"
        return self.tier


TIER_HIERARCHY = {"free": 0, "basic": 1, "vip": 2, "admin": 3}


def resolve_user_tier(user_id: str) -> str:
    """Resolve a user's effective tier from in-memory store. Defaults to 'free'."""
    sub = _subscriptions.get(user_id)
    if sub is None:
        return "free"
    return sub.effective_tier()


def set_subscription(sub: UserSubscription) -> None:
    _subscriptions[sub.user_id] = sub


def clear_subscriptions() -> None:
    _subscriptions.clear()


def resolve_user_tier_from_service(user_id: str, service=None) -> str:
    """Resolve tier from durable subscription service or fall back to in-memory."""
    if service is not None:
        return service.resolve_tier(user_id)
    return resolve_user_tier(user_id)


def can_access_tier(user_id: str, required_tier: str) -> bool:
    return _check_tier(resolve_user_tier(user_id), required_tier)


def can_access_tier_from_service(user_id: str, required_tier: str, service=None) -> bool:
    """Check tier using durable subscription service, falling back to in-memory."""
    resolved = resolve_user_tier_from_service(user_id, service)
    return _check_tier(resolved, required_tier)


def _check_tier(user_tier: str, required_tier: str) -> bool:
    user_level = TIER_HIERARCHY.get(user_tier, 0)
    required_level = TIER_HIERARCHY.get(required_tier, 0)
    return user_level >= required_level


# -- VIP-only fields (must be stripped for non-VIP users) -----------------------

VIP_ONLY_FIELDS = {
    "score_heatmap", "market_edge", "market_probs",
    "lineup_adjustment", "missing_key_players",
    "prematch_snapshot_id", "model_pipeline",
    "over25", "btts",
}


def filter_prediction_payload(payload: dict[str, Any], user_tier: str) -> dict[str, Any]:
    """
    Strip VIP-only fields from a prediction payload for non-VIP users.

    The payload is the dict returned by PredictionSnapshot.to_public_dict(tier="vip").
    This function removes fields that the user's tier should not see.

    admin: all fields pass through (plus debug)
    vip: all VIP fields pass through
    basic: VIP fields stripped, only basic contract fields remain
    free: same as basic, but can be further restricted
    """
    tier_level = TIER_HIERARCHY.get(user_tier, 0)

    filtered = dict(payload)

    if tier_level < 2:  # below vip
        for field in VIP_ONLY_FIELDS:
            filtered.pop(field, None)

    if tier_level >= 3:  # admin
        filtered["_debug_tier"] = user_tier

    return filtered


# -- Admin-only debug fields ----------------------------------------------------

ADMIN_DEBUG_FIELDS = {
    "fallback_used", "warnings", "_debug_tier",
}


def add_debug_info(payload: dict[str, Any], fallback: list[str], warnings: list[str]) -> dict[str, Any]:
    """Add debug metadata for admin users."""
    payload["fallback_used"] = fallback
    payload["warnings"] = warnings
    return payload
