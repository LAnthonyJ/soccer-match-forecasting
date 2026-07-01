import uuid
from dataclasses import dataclass

from flask import request

from src.api.errors import ApiError
from src.auth.access_control import TIER_HIERARCHY, resolve_user_tier_from_service


@dataclass
class ApiRequestContext:
    request_id: str
    user_id: str
    tier: str

    @property
    def is_authenticated(self) -> bool:
        return self.user_id != "anonymous"

    @property
    def is_admin(self) -> bool:
        return self.tier == "admin"

    def require_user(self) -> None:
        if not self.is_authenticated:
            raise ApiError("unauthorized", "Authentication required", status_code=401)

    def require_tier(self, required_tier: str) -> None:
        user_level = TIER_HIERARCHY.get(self.tier, 0)
        required_level = TIER_HIERARCHY.get(required_tier, 0)
        if user_level < required_level:
            code = "vip_required" if required_tier == "vip" else "forbidden"
            raise ApiError(code, f"{required_tier.upper()} access required", status_code=403)


def _request_id() -> str:
    return request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    prefix = "Bearer "
    return header[len(prefix):].strip() if header.startswith(prefix) else None


def _resolve_user_from_token(app_env: str) -> str | None:
    from src.auth.session_tokens import verify_session_token
    from src.config.runtime import load_runtime_config

    token = _bearer_token(request.headers.get("Authorization"))
    if not token:
        return None
    cfg = load_runtime_config()
    secret = cfg.beta_auth_secret or "dev-secret"
    try:
        payload = verify_session_token(token, secret)
        return payload["user_id"]
    except ValueError:
        if app_env == "dev":
            return None
        raise ApiError("unauthorized", "Invalid session token", status_code=401)


def _user_id(app_env: str) -> str:
    token_user = _resolve_user_from_token(app_env)
    if token_user:
        return token_user

    user_id = (request.headers.get("X-User-Id", "") or "").strip()
    if user_id and app_env == "prod":
        raise ApiError("unauthorized", "Session token required in production", status_code=401)
    return user_id or "anonymous"


def build_request_context(billing_service) -> ApiRequestContext:
    from src.config.runtime import load_runtime_config

    cfg = load_runtime_config()
    app_env = cfg.app_env

    user_id = _user_id(app_env)
    if app_env == "dev":
        tier_hint = request.headers.get("X-User-Tier", "")
        if tier_hint in TIER_HIERARCHY:
            return ApiRequestContext(_request_id(), user_id, tier_hint)
    tier = resolve_user_tier_from_service(user_id, billing_service)
    return ApiRequestContext(_request_id(), user_id, tier)
