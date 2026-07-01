from datetime import datetime, timezone
from typing import Any

from flask import jsonify

from src.api.errors import ApiError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_success_payload(
    data: Any,
    request_id: str,
    meta: dict | None = None,
) -> dict:
    payload_meta = {"request_id": request_id, "generated_at": _now_iso()}
    if meta:
        payload_meta.update(meta)
    return {"ok": True, "data": data, "meta": payload_meta}


def build_error_payload(error: ApiError, request_id: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
        "meta": {"request_id": request_id, "generated_at": _now_iso()},
    }


def api_success(data: Any, request_id: str, meta: dict | None = None, status_code: int = 200):
    resp = jsonify(build_success_payload(data, request_id, meta))
    return resp, status_code


def api_error_response(error: ApiError, request_id: str):
    return jsonify(build_error_payload(error, request_id)), error.status_code
