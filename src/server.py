from src.api.app import app
from src.config.runtime import load_runtime_config

runtime_config = load_runtime_config()

__all__ = ["app", "runtime_config"]
