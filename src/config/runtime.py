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
    sportmonks_api_key_present: bool
    beta_auth_secret: str
    beta_session_ttl_hours: int
    payment_provider: str
    payment_webhook_secret: str
    payment_return_url: str
    enable_dev_payment_webhook: bool
    enable_ai_analysis: bool
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str

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
        sportmonks_api_key_present=bool(os.environ.get("SPORTMONKS_API_KEY")),
        beta_auth_secret=os.environ.get("BETA_AUTH_SECRET", ""),
        beta_session_ttl_hours=_parse_int("BETA_SESSION_TTL_HOURS", 720),
        payment_provider=os.environ.get("PAYMENT_PROVIDER", "mock").strip().lower(),
        payment_webhook_secret=os.environ.get("PAYMENT_WEBHOOK_SECRET", ""),
        payment_return_url=os.environ.get("PAYMENT_RETURN_URL", "http://localhost:5000/h5#/account").strip(),
        enable_dev_payment_webhook=_parse_bool("ENABLE_DEV_PAYMENT_WEBHOOK", False),
        enable_ai_analysis=_parse_bool("ENABLE_AI_ANALYSIS", False),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
    )
    if cfg.is_prod and cfg.flask_debug:
        raise ValueError("FLASK_DEBUG must be disabled when APP_ENV=prod")
    if cfg.is_prod and not cfg.beta_auth_secret:
        raise ValueError("BETA_AUTH_SECRET is required when APP_ENV=prod")
    if cfg.is_prod and not cfg.payment_webhook_secret:
        raise ValueError("PAYMENT_WEBHOOK_SECRET is required when APP_ENV=prod")
    if cfg.is_prod and cfg.enable_dev_payment_webhook:
        raise ValueError("ENABLE_DEV_PAYMENT_WEBHOOK must be false when APP_ENV=prod")
    return cfg
