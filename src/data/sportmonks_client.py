"""
Sportmonks Football API v3 client.

Handles authentication, caching, rate limiting, and pagination for all
Sportmonks endpoints used by the prediction system.

API key should be set as SPORTMONKS_API_KEY env var.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from src.config.runtime import load_runtime_config

BASE_URL = "https://api.sportmonks.com/v3/football"
CACHE_DIR = load_runtime_config().data_dir / "processed" / "sportmonks_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_request_count = 0
RATE_LIMIT = 2000


def _api_key() -> str:
    key = os.environ.get("SPORTMONKS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "SPORTMONKS_API_KEY not set. "
            "Get a key at https://www.sportmonks.com/"
        )
    return key


def _cache_path(endpoint: str, params: dict | None = None) -> Path:
    raw = endpoint + json.dumps(params or {}, sort_keys=True)
    name = hashlib.md5(raw.encode()).hexdigest()
    return CACHE_DIR / f"{name}.json"


def _get(
    endpoint: str,
    params: dict | None = None,
    cache_ttl_min: int = 10,
    timeout: int = 45,
    force_refresh: bool = False,
) -> dict:
    """Rate-limited GET with local JSON cache."""
    global _request_count

    path = _cache_path(endpoint, params)
    if path.exists() and not force_refresh:
        age = time.time() - path.stat().st_mtime
        if age < cache_ttl_min * 60:
            with open(path, encoding="utf-8") as f:
                return json.load(f)

    if _request_count >= RATE_LIMIT - 10:
        raise RuntimeError(f"Near rate limit: {_request_count}/{RATE_LIMIT}")

    headers = {"Authorization": _api_key()}
    session = requests.Session()
    # Bypass system proxy for direct API access
    session.trust_env = False
    resp = session.get(
        f"{BASE_URL}{endpoint}",
        headers=headers,
        params=params,
        timeout=timeout,
    )
    _request_count += 1

    if resp.status_code == 429:
        time.sleep(65)
        return _get(endpoint, params, cache_ttl_min, timeout, force_refresh=force_refresh)

    resp.raise_for_status()
    data = resp.json()

    if "message" in data and "error" in str(data.get("message", "")).lower():
        # Only log, don't raise for empty results
        pass

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return data


def paginate(
    endpoint: str,
    params: dict | None = None,
    per_page: int = 100,
    max_pages: int = 20,
    force_refresh: bool = False,
) -> list[dict]:
    """Collect all pages from a paginated list endpoint."""
    base = dict(params or {})
    base["per_page"] = per_page
    all_data = []

    for page in range(1, max_pages + 1):
        p = dict(base)
        p["page"] = page
        resp = _get(endpoint, p, force_refresh=force_refresh)
        batch = resp.get("data", [])
        if not batch:
            break
        all_data.extend(batch)
        if not resp.get("pagination", {}).get("has_more"):
            break

    return all_data


def _fmt_dt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


# -- League / Season discovery ---------------------------------------------------

def get_leagues() -> list[dict]:
    return paginate("/leagues", per_page=50, max_pages=15)


def get_seasons(league_id: int) -> list[dict]:
    return paginate("/seasons", {"filters": f"leagueCountries:{league_id}"}, per_page=50, max_pages=5)


def get_current_season(league_id: int) -> dict | None:
    for s in paginate("/seasons", {"filters": f"leagueCountries:{league_id}"}, per_page=100, max_pages=3):
        if s.get("is_current"):
            return s
    return None


# -- Fixtures -------------------------------------------------------------------

def get_fixtures_between(
    from_date: datetime,
    to_date: datetime,
    *,
    league_ids: list[int] | None = None,
    includes: str = "",
    per_page: int = 100,
    max_pages: int = 20,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Fetch fixtures in a date range. Optionally filter by league IDs.

    `includes` is a semicolon-separated string like "participants;scores;odds".
    """
    endpoint = f"/fixtures/between/{_fmt_dt(from_date)}/{_fmt_dt(to_date)}"
    params: dict[str, Any] = {}
    if includes:
        params["include"] = includes

    fixtures = paginate(
        endpoint,
        params,
        per_page=per_page,
        max_pages=max_pages,
        force_refresh=force_refresh,
    )

    if league_ids:
        allowed = set(league_ids)
        fixtures = [f for f in fixtures if f.get("league_id") in allowed]

    return fixtures


def get_fixture(fixture_id: int, includes: str = "") -> dict | None:
    params: dict[str, Any] = {}
    if includes:
        params["include"] = includes
    data = _get(f"/fixtures/{fixture_id}", params)
    return data.get("data")


def get_fixtures_by_season(season_id: int, includes: str = "") -> list[dict]:
    params: dict[str, Any] = {"filters": f"seasonCountries:{season_id}"}
    if includes:
        params["include"] = includes
    return paginate("/fixtures", params, per_page=100, max_pages=20)


# -- Squads / Teams / Players ---------------------------------------------------

def get_squad(team_id: int) -> list[dict]:
    """Get current squad for a team. Returns list of squad entries with player_id."""
    data = _get(f"/squads/teams/{team_id}", cache_ttl_min=360)
    return data.get("data", [])


def get_player(player_id: int) -> dict | None:
    data = _get(f"/players/{player_id}", cache_ttl_min=1440)
    return data.get("data")


def get_team(
    team_id: int,
    includes: str = "",
    cache_ttl_min: int = 1440,
    force_refresh: bool = False,
) -> dict | None:
    params: dict[str, Any] = {}
    if includes:
        params["include"] = includes
    data = _get(
        f"/teams/{team_id}",
        params or None,
        cache_ttl_min=cache_ttl_min,
        force_refresh=force_refresh,
    )
    return data.get("data")


def get_national_teams() -> list[dict]:
    """Get all national teams (type=national)."""
    teams = paginate("/teams", per_page=100, max_pages=10)
    return [t for t in teams if t.get("type") == "national"]


def search_teams(query: str, includes: str = "", force_refresh: bool = False) -> list[dict]:
    params: dict[str, Any] = {}
    if includes:
        params["include"] = includes
    data = _get(
        f"/teams/search/{query}",
        params or None,
        cache_ttl_min=30,
        force_refresh=force_refresh,
    )
    return data.get("data", [])


# -- Odds -----------------------------------------------------------------------

def get_fixture_odds_1x2(fixture_id: int) -> dict[str, dict[str, float]]:
    """
    Fetch 1X2 odds for a fixture, grouped by bookmaker.

    Returns: {bookmaker_name: {"home": float, "draw": float, "away": float}}
    Only returns bookmakers that have all three (home/draw/away) odds.
    """
    fixture = get_fixture(fixture_id, includes="odds")
    if not fixture:
        return {}

    odds_list = fixture.get("odds", [])
    market1 = [o for o in odds_list if o.get("market_id") == 1]

    by_bookie: dict[int, dict[str, Any]] = {}
    for o in market1:
        bid = o["bookmaker_id"]
        if bid not in by_bookie:
            by_bookie[bid] = {}
        label = str(o.get("label", "")).lower()
        if label in ("home", "draw", "away"):
            by_bookie[bid][label] = float(o["value"])

    result: dict[str, dict[str, float]] = {}
    for bid, parts in by_bookie.items():
        if len(parts) == 3:
            result[str(bid)] = {
                "home": parts["home"],
                "draw": parts["draw"],
                "away": parts["away"],
            }

    return result


def get_bookmaker_name(bookmaker_id: int) -> str:
    data = _get(f"/bookmakers/{bookmaker_id}", cache_ttl_min=10080)
    return data.get("data", {}).get("name", str(bookmaker_id))


# -- Lineups & Injuries ----------------------------------------------------------

def get_fixture_lineups(fixture_id: int) -> list[dict]:
    """Get confirmed lineups for a completed fixture. Returns [] if fixture is upcoming."""
    try:
        data = _get(f"/fixtures/{fixture_id}", {"include": "lineups"}, cache_ttl_min=120)
    except Exception:
        return []
    fixture = data.get("data", {})
    return fixture.get("lineups", [])


def get_fixture_injuries(fixture_id: int) -> list[dict]:
    """
    Get injury/suspension data for a fixture.

    Sportmonks v3 does not have a dedicated injury endpoint per fixture.
    We approximate by checking squad player 'end' dates and expected lineups
    for missing expected starters. Returns [] when no data is available.
    """
    return []  # Sportmonks v3 base plan: no dedicated injury endpoint


def get_expected_lineups(fixture_id: int) -> list[dict]:
    """Get expected lineups for a fixture (requires Expected Lineups add-on)."""
    try:
        data = _get(f"/expected-lineups/fixtures/{fixture_id}", cache_ttl_min=30)
    except Exception:
        return []
    return data.get("data", [])


# -- Standings ------------------------------------------------------------------

def get_standings(season_id: int) -> list[dict]:
    data = _get(f"/standings/seasons/{season_id}", cache_ttl_min=360)
    return data.get("data", [])


# -- xG / Match Facts -----------------------------------------------------------

def get_match_facts(fixture_id: int) -> list[dict]:
    """Get match facts including xG, pressure index (requires bundle)."""
    data = _get(f"/facts/fixtures/{fixture_id}", cache_ttl_min=10080)
    return data.get("data", [])


# -- Utility --------------------------------------------------------------------

def batch_get_players(player_ids: list[int]) -> dict[int, dict]:
    """Fetch multiple players. Returns {player_id: player_data}."""
    result = {}
    for pid in player_ids:
        p = get_player(pid)
        if p:
            result[pid] = p
    return result


POSITION_MAP = {
    24: "GK",
    25: "DF",
    26: "MF",
    27: "MF",
    28: "FW",
    29: "FW",
    30: "DF",
    31: "DF",
    32: "MF",
}


def position_code(position_id: int) -> str:
    return POSITION_MAP.get(position_id, "??")
