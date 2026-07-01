def apply_no_store(headers) -> None:
    headers["Cache-Control"] = "no-store"


def apply_prediction_cache_headers(headers, max_age_seconds: int = 60) -> None:
    headers["Cache-Control"] = f"private, max-age={max_age_seconds}"
