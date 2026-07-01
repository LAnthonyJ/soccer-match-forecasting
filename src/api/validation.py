from flask import request

from src.api.errors import ApiError


def get_json_body() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ApiError("validation_error", "JSON body must be an object", status_code=400)
    return payload


def require_fields(payload: dict, fields: list[str]) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise ApiError(
            "validation_error",
            "Missing required fields",
            status_code=400,
            details={"missing": missing},
        )
