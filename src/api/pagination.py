def parse_limit(raw: str | None, default: int = 20, max_limit: int = 100) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("limit must be an integer")
    if value < 1:
        raise ValueError("limit must be positive")
    return min(value, max_limit)
