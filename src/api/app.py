"""
Prediction API server (Flask).

Endpoints:
    GET  /health                  — model/data/cache health
    GET  /api/predictions/<id>    — prediction (tier from auth header)
    GET  /api/predictions/<id>/vip — VIP prediction (requires vip+ auth)

Start:
    python src/api/app.py
    # or: FLASK_APP=src/api/app.py flask run --port 5000
"""
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from src.api.cache_headers import apply_no_store, apply_prediction_cache_headers
from src.api.context import build_request_context
from src.api.errors import ApiError
from src.api.pagination import parse_limit
from src.api.rate_limit import FixedWindowRateLimiter
from src.api.responses import api_error_response, api_success
from src.api.validation import get_json_body, require_fields
from src.auth.access_control import (
    add_debug_info,
    filter_prediction_payload,
)
from src.auth.card_codes import CardCodeService, CardCodeStore
from src.data.team_directory import merge_team_rows, search_team_aliases
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService
from src.monitoring.health import check_all, check_all_live
from src.services.deepseek_analysis_service import DeepSeekAnalysisService
from src.services.prediction_service import (
    PredictionService,
    load_latest_prediction,
)
from src.config.runtime import load_runtime_config

app = Flask(__name__)
_runtime_config = load_runtime_config()
_service = PredictionService()

_BILLING_DIR = _runtime_config.data_dir / "billing"
_AUTH_DIR = _runtime_config.data_dir / "auth"
_ANALYSIS_DIR = _runtime_config.data_dir / "ai_analysis"
from src.billing.audit_log import AuditLog
_billing_audit_log = AuditLog(_BILLING_DIR / "audit_log.json")
_billing_service = SubscriptionService(BillingStore(_BILLING_DIR), audit_log=_billing_audit_log)
_card_code_service = CardCodeService(CardCodeStore(_AUTH_DIR), _billing_service)
_analysis_service = DeepSeekAnalysisService(
    cache_dir=_ANALYSIS_DIR,
    api_key=_runtime_config.deepseek_api_key,
    base_url=_runtime_config.deepseek_base_url,
    model=_runtime_config.deepseek_model,
    enabled=_runtime_config.enable_ai_analysis,
)

_prediction_limiter = FixedWindowRateLimiter(limit=120, window_seconds=60)
_billing_limiter = FixedWindowRateLimiter(limit=20, window_seconds=60)
_WORLD_CUP_LEAGUE_ID = 732
_WORLD_CUP_FALLBACK_FROM = datetime(2026, 6, 1, tzinfo=timezone.utc)
_WORLD_CUP_FALLBACK_TO = datetime(2026, 7, 21, 23, 59, tzinfo=timezone.utc)
_world_cup_fixture_cache: dict[str, object] = {"expires_at": None, "fixtures": None}


# -- Helpers ---------------------------------------------------------------

def _ctx():
    return build_request_context(_billing_service)



def _resolve_fixture_id(fixture_id: str) -> int:
    try:
        return int(fixture_id)
    except (ValueError, TypeError):
        pass
    if fixture_id.startswith("sportmonks-"):
        try:
            return int(fixture_id.split("-", 1)[1])
        except (ValueError, IndexError):
            pass
    raise ApiError("bad_request", f"Invalid fixture_id: {fixture_id}", status_code=400)


def _normalize_search_name(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    aliases = {
        "usa": "united states",
        "us": "united states",
        "u s a": "united states",
        "iran": "ir iran",
        "south korea": "korea republic",
        "north korea": "korea dpr",
        "ivory coast": "cote d ivoire",
        "czechia": "czech republic",
        "turkiye": "turkey",
    }
    return aliases.get(cleaned, cleaned)


def _name_matches(query: str, candidate: str) -> bool:
    q = _normalize_search_name(query)
    c = _normalize_search_name(candidate)
    return bool(q and c and (q == c or q in c or c in q))


def _participant_name(fixture: dict, location: str) -> str:
    return next(
        (
            p.get("name", "?")
            for p in fixture.get("participants", [])
            if p.get("meta", {}).get("location") == location
        ),
        "?",
    )


def _fixture_payload(fixture: dict) -> dict:
    return {
        "fixture_id": f"api-{fixture['id']}",
        "sportmonks_id": fixture["id"],
        "home_team": _participant_name(fixture, "home"),
        "away_team": _participant_name(fixture, "away"),
        "kickoff_utc": fixture.get("starting_at"),
        "tournament": (fixture.get("league") or {}).get("name", "?"),
    }


def _world_cup_window() -> tuple[datetime, datetime]:
    try:
        from src.data.sportmonks_client import get_current_season

        season = get_current_season(_WORLD_CUP_LEAGUE_ID)
        if season:
            start = season.get("starting_at")
            end = season.get("ending_at")
            if start and end:
                from_date = datetime.fromisoformat(str(start).replace(" ", "T"))
                to_date = datetime.fromisoformat(str(end).replace(" ", "T"))
                if from_date.tzinfo is None:
                    from_date = from_date.replace(tzinfo=timezone.utc)
                if to_date.tzinfo is None:
                    to_date = to_date.replace(tzinfo=timezone.utc)
                if to_date > _WORLD_CUP_FALLBACK_TO:
                    to_date = _WORLD_CUP_FALLBACK_TO
                if from_date < _WORLD_CUP_FALLBACK_FROM:
                    from_date = _WORLD_CUP_FALLBACK_FROM
                return from_date, to_date
    except Exception:
        pass
    return _WORLD_CUP_FALLBACK_FROM, _WORLD_CUP_FALLBACK_TO


def _get_world_cup_fixtures() -> tuple[list[dict], str]:
    now = datetime.now(timezone.utc)
    expires_at = _world_cup_fixture_cache.get("expires_at")
    cached = _world_cup_fixture_cache.get("fixtures")
    if isinstance(expires_at, datetime) and expires_at > now and isinstance(cached, list):
        return cached, "hit"

    from src.data.sportmonks_client import get_fixtures_between

    from_date, to_date = _world_cup_window()
    fixtures = get_fixtures_between(
        from_date,
        to_date,
        league_ids=[_WORLD_CUP_LEAGUE_ID],
        includes="participants;league",
        per_page=200,
        max_pages=2,
        force_refresh=False,
    )
    _world_cup_fixture_cache["fixtures"] = fixtures
    _world_cup_fixture_cache["expires_at"] = now + timedelta(minutes=30)
    return fixtures, "miss"


def _world_cup_team_rows(fixtures: list[dict]) -> list[dict]:
    rows = []
    for fixture in fixtures:
        for participant in fixture.get("participants", []):
            name = participant.get("name")
            if not name:
                continue
            rows.append({
                "display_name": name,
                "sportmonks_name": name,
                "aliases": [name],
            })
    return merge_team_rows(rows)


def _filter_fixture_matches(fixtures: list[dict], home: str, away: str, limit: int) -> list[dict]:
    matches = []
    seen_fixture_ids = set()
    for fixture in fixtures:
        actual_home = _participant_name(fixture, "home")
        actual_away = _participant_name(fixture, "away")
        direct = _name_matches(home, actual_home) and _name_matches(away, actual_away)
        reversed_match = _name_matches(home, actual_away) and _name_matches(away, actual_home)
        if not direct and not reversed_match:
            continue
        item = _fixture_payload(fixture)
        if item["sportmonks_id"] in seen_fixture_ids:
            continue
        seen_fixture_ids.add(item["sportmonks_id"])
        item["match_direction"] = "direct" if direct else "reversed"
        matches.append(item)
        if len(matches) >= limit:
            break
    matches.sort(key=lambda row: row.get("kickoff_utc") or "")
    return matches


def _find_national_team(candidates: list[dict], query: str) -> dict | None:
    nationals = [team for team in candidates if team.get("type") == "national"]
    normalized_query = _normalize_search_name(query)
    for team in nationals:
        if _normalize_search_name(team.get("name", "")) == normalized_query:
            return team
    for team in nationals:
        if _name_matches(query, team.get("name", "")):
            return team
    return None


def _search_fixtures_via_team_schedule(home: str, away: str, from_date, to_date, limit: int) -> list[dict]:
    from src.data.sportmonks_client import get_team, search_teams
    from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES

    home_team = _find_national_team(search_teams(home, force_refresh=True), home)
    away_team = _find_national_team(search_teams(away, force_refresh=True), away)
    if not home_team or not away_team:
        return []

    payload = get_team(
        int(home_team["id"]),
        includes="fixtures;fixtures.participants;fixtures.league",
        cache_ttl_min=30,
        force_refresh=True,
    )
    fixtures = payload.get("fixtures", []) if isinstance(payload, dict) else []
    allowed_leagues = set(INTERNATIONAL_LEAGUES.keys())
    matches = []
    seen_fixture_ids = set()

    for fixture in fixtures:
        fixture_id = fixture.get("id")
        if fixture_id in seen_fixture_ids or fixture.get("league_id") not in allowed_leagues:
            continue
        kickoff_raw = fixture.get("starting_at")
        if not kickoff_raw:
            continue
        kickoff = datetime.fromisoformat(kickoff_raw.replace(" ", "T"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff < from_date or kickoff > to_date:
            continue

        actual_home = _participant_name(fixture, "home")
        actual_away = _participant_name(fixture, "away")
        direct = _name_matches(home, actual_home) and _name_matches(away, actual_away)
        reversed_match = _name_matches(home, actual_away) and _name_matches(away, actual_home)
        if not direct and not reversed_match:
            continue

        seen_fixture_ids.add(fixture_id)
        item = _fixture_payload(fixture)
        item["match_direction"] = "direct" if direct else "reversed"
        matches.append(item)
        if len(matches) >= limit:
            break

    matches.sort(key=lambda row: row.get("kickoff_utc") or "")
    return matches


def _search_window_size_days(from_date, to_date) -> int:
    span_days = max((to_date - from_date).days + 1, 1)
    if span_days <= 31:
        return 1
    if span_days <= 120:
        return 3
    return 7

# -- Flask error handler ---------------------------------------------------

@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"
    return api_error_response(error, request_id)


# -- Endpoints -------------------------------------------------------------

@app.route("/health")
def health():
    status = check_all()
    code = 200 if status["healthy"] else 503
    return jsonify(status), code


@app.route("/health/live")
def health_live():
    status = check_all_live()
    code = 200 if status["healthy"] else 503
    return jsonify(status), code


@app.route("/api/predictions/<fixture_id>")
def get_prediction(fixture_id: str):
    ctx = _ctx()
    ctx.require_user()
    _prediction_limiter.check(ctx.user_id)

    fid = _resolve_fixture_id(fixture_id)

    cached = load_latest_prediction(f"sportmonks-{fid}")
    if cached:
        payload = filter_prediction_payload(cached, ctx.tier)
        if ctx.is_admin:
            payload = add_debug_info(payload, cached.get("_fallback", []), cached.get("_warnings", []))
        response, code = api_success(payload, ctx.request_id, meta={"source": "cache", "tier": ctx.tier})
        apply_prediction_cache_headers(response.headers)
        return response, code

    try:
        result = _service.predict_match(fixture_id=fid, use_prematch_api=True)
    except Exception as e:
        return api_error_response(
            ApiError("prediction_failed", f"Prediction failed: {e}", status_code=503),
            ctx.request_id,
        )

    internal = result.snapshot.to_public_dict(tier="internal")
    payload = filter_prediction_payload(internal, ctx.tier)
    if ctx.is_admin:
        payload = add_debug_info(payload, result.fallback_used, result.warnings)
    response, code = api_success(payload, ctx.request_id, meta={"source": "live", "tier": ctx.tier})
    apply_prediction_cache_headers(response.headers)
    return response, code


@app.route("/api/predictions/<fixture_id>/vip")
def get_vip_prediction(fixture_id: str):
    ctx = _ctx()
    ctx.require_tier("vip")
    _prediction_limiter.check(ctx.user_id)

    fid = _resolve_fixture_id(fixture_id)

    cached = load_latest_prediction(f"sportmonks-{fid}")
    full = cached if cached else None

    if not full:
        try:
            result = _service.predict_match(fixture_id=fid, use_prematch_api=True)
        except Exception as e:
            return api_error_response(
                ApiError("prediction_failed", f"Prediction failed: {e}", status_code=503),
                ctx.request_id,
            )
        full = result.snapshot.to_public_dict(tier="vip")
        if ctx.is_admin:
            full = add_debug_info(full, result.fallback_used, result.warnings)
    elif ctx.is_admin:
        full = add_debug_info(dict(full), full.get("_fallback", []), full.get("_warnings", []))

    return api_success(full, ctx.request_id, meta={"tier": ctx.tier})


@app.route("/api/predictions/<fixture_id>/analysis", methods=["POST"])
def get_prediction_analysis(fixture_id: str):
    ctx = _ctx()
    ctx.require_user()
    ctx.require_tier("vip")
    fid = _resolve_fixture_id(fixture_id)
    payload = load_latest_prediction(f"sportmonks-{fid}")
    if payload is None:
        try:
            result = _service.predict_match(fixture_id=fid, use_prematch_api=True)
        except Exception as e:
            return api_error_response(
                ApiError("prediction_failed", f"Prediction failed: {e}", status_code=503),
                ctx.request_id,
            )
        payload = result.snapshot.to_public_dict(tier="vip")
    analysis = _analysis_service.generate(payload)
    return api_success(analysis, ctx.request_id, meta={"tier": ctx.tier})



@app.route("/api/search/fixtures")
def search_fixtures():
    ctx = _ctx()
    ctx.require_user()
    from src.data.sportmonks_client import get_fixtures_between
    from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES

    home = (request.args.get("home") or "").strip()
    away = (request.args.get("away") or "").strip()
    if not home or not away:
        raise ApiError("bad_request", "home and away are required", status_code=400)

    try:
        limit = parse_limit(request.args.get("limit"), default=20, max_limit=100)
    except ValueError:
        raise ApiError("bad_request", "limit must be a positive integer", status_code=400)

    try:
        from_raw = request.args.get("from")
        to_raw = request.args.get("to")
        has_explicit_dates = bool(from_raw or to_raw)
        if from_raw or to_raw:
            from_date = datetime.fromisoformat(from_raw) if from_raw else _WORLD_CUP_FALLBACK_FROM
            to_date = datetime.fromisoformat(to_raw) if to_raw else _WORLD_CUP_FALLBACK_TO
        else:
            from_date, to_date = _world_cup_window()
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
        if to_date.tzinfo is None:
            to_date = to_date.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError("bad_request", "from/to must be ISO dates", status_code=400)

    if to_date < from_date:
        raise ApiError("bad_request", "to must be greater than or equal to from", status_code=400)

    fixtures = []
    seen_fixture_ids = set()
    meta_extra = {"source": "sportmonks_world_cup", "league_id": _WORLD_CUP_LEAGUE_ID}
    try:
        if not has_explicit_dates:
            try:
                world_cup_fixtures, cache_status = _get_world_cup_fixtures()
                meta_extra["cache"] = cache_status
                fixtures = _filter_fixture_matches(world_cup_fixtures, home, away, limit)
            except Exception:
                fixtures = []
        if not fixtures:
            meta_extra["source"] = "live"
            meta_extra.pop("cache", None)
            try:
                fixtures = _search_fixtures_via_team_schedule(home, away, from_date, to_date, limit)
            except Exception:
                fixtures = []
        if not fixtures:
            window_days = _search_window_size_days(from_date, to_date)
            window_start = from_date
            while window_start <= to_date and len(fixtures) < limit:
                window_end = min(window_start + timedelta(days=window_days - 1), to_date)
                raw = get_fixtures_between(
                    window_start,
                    window_end,
                    league_ids=list(INTERNATIONAL_LEAGUES.keys()),
                    includes="participants;league",
                    per_page=100,
                    max_pages=2,
                    force_refresh=True,
                )
                for fixture in raw:
                    actual_home = _participant_name(fixture, "home")
                    actual_away = _participant_name(fixture, "away")
                    direct = _name_matches(home, actual_home) and _name_matches(away, actual_away)
                    reversed_match = _name_matches(home, actual_away) and _name_matches(away, actual_home)
                    if direct or reversed_match:
                        item = _fixture_payload(fixture)
                        if item["sportmonks_id"] in seen_fixture_ids:
                            continue
                        seen_fixture_ids.add(item["sportmonks_id"])
                        item["match_direction"] = "direct" if direct else "reversed"
                        fixtures.append(item)
                        if len(fixtures) >= limit:
                            break
                window_start = window_end + timedelta(days=1)
    except Exception:
        meta_extra.pop("source", None)
        meta_extra["warning"] = "upstream_unavailable"

    data = {
        "matches": fixtures,
        "count": len(fixtures),
        "limit": limit,
        "query": {"home": home, "away": away},
    }
    return api_success(data, ctx.request_id, meta=meta_extra if meta_extra else None)


@app.route("/api/teams/search")
def search_teams():
    ctx = _ctx()
    query = (request.args.get("q") or "").strip()
    if not query:
        raise ApiError("bad_request", "q is required", status_code=400)
    extra_rows = []
    try:
        fixtures, _ = _get_world_cup_fixtures()
        extra_rows = _world_cup_team_rows(fixtures)
    except Exception:
        extra_rows = []
    teams = search_team_aliases(query, limit=10, extra_rows=extra_rows)
    return api_success({"teams": teams, "count": len(teams), "query": query}, ctx.request_id)

@app.route("/api/matches/today")
def matches_today():
    ctx = _ctx()
    from datetime import datetime, timezone
    from src.data.sportmonks_client import get_fixtures_between
    from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES

    try:
        limit = parse_limit(request.args.get("limit"), default=20, max_limit=100)
    except ValueError:
        raise ApiError("bad_request", "limit must be a positive integer", status_code=400)

    now = datetime.now(timezone.utc)
    fixtures = []
    meta_extra = {}
    try:
        raw = get_fixtures_between(
            now, now.replace(hour=23, minute=59),
            league_ids=list(INTERNATIONAL_LEAGUES.keys()),
            includes="participants;league",
        )
        for f in raw[:limit]:
            fixtures.append({
                "fixture_id": f"api-{f['id']}",
                "sportmonks_id": f["id"],
                "home_team": next((p["name"] for p in f.get("participants", [])
                                   if p.get("meta", {}).get("location") == "home"), "?"),
                "away_team": next((p["name"] for p in f.get("participants", [])
                                   if p.get("meta", {}).get("location") == "away"), "?"),
                "kickoff_utc": f.get("starting_at"),
                "tournament": (f.get("league") or {}).get("name", "?"),
            })
    except Exception:
        meta_extra["warning"] = "upstream_unavailable"


    data = {"matches": fixtures, "count": len(fixtures)}
    if limit:
        data["limit"] = limit
    return api_success(data, ctx.request_id, meta=meta_extra if meta_extra else None)


# -- Billing / Subscription endpoints ------------------------------------------

@app.route("/api/me/subscription")
def get_my_subscription():
    ctx = _ctx()
    ctx.require_user()
    sub = _billing_service.get_subscription(ctx.user_id)
    data = {
        "user_id": ctx.user_id,
        "tier": _billing_service.resolve_tier(ctx.user_id),
        "status": sub.status if sub else "none",
        "expires_at": sub.expires_at if sub else None,
    }
    return api_success(data, ctx.request_id)


@app.route("/api/auth/session", methods=["POST"])
def create_beta_session():
    ctx = _ctx()
    payload = get_json_body()
    require_fields(payload, ["user_id"])
    cfg = load_runtime_config()
    if cfg.is_prod:
        raise ApiError("not_available", "Direct beta session creation is disabled in production", status_code=403)
    from src.auth.session_tokens import create_session_token
    token = create_session_token(
        payload["user_id"],
        cfg.beta_auth_secret or "dev-secret",
        ttl_seconds=cfg.beta_session_ttl_hours * 3600,
    )
    return api_success({"token": token, "user_id": payload["user_id"]}, ctx.request_id)


@app.route("/api/auth/redeem", methods=["POST"])
def redeem_card_code():
    ctx = _ctx()
    payload = get_json_body()
    require_fields(payload, ["username", "card_code"])
    try:
        result = _card_code_service.redeem_code(payload["username"], payload["card_code"])
    except ValueError as e:
        raise ApiError("auth_error", str(e), status_code=400)

    from src.auth.session_tokens import create_session_token
    cfg = load_runtime_config()
    token = create_session_token(
        result["username"],
        cfg.beta_auth_secret or "dev-secret",
        ttl_seconds=cfg.beta_session_ttl_hours * 3600,
    )
    return api_success(
        {
            "token": token,
            "user_id": result["username"],
            "tier": result["tier"],
            "expires_at": result["expires_at"],
        },
        ctx.request_id,
    )


@app.route("/api/billing/checkout", methods=["POST"])
def create_checkout():
    ctx = _ctx()
    ctx.require_user()
    _billing_limiter.check(ctx.user_id)
    payload = get_json_body()
    require_fields(payload, ["plan_id"])
    try:
        order = _billing_service.create_checkout_order(ctx.user_id, payload["plan_id"])
    except ValueError as e:
        raise ApiError("billing_error", str(e), status_code=400)
    data = order.to_dict()
    # Generate payment URL via configured gateway
    from src.billing.payment_gateway import MockPaymentGateway
    cfg = load_runtime_config()
    if cfg.payment_provider == "mock":
        gw = MockPaymentGateway(base_url=cfg.app_base_url, return_url=cfg.payment_return_url)
        checkout_result = gw.create_checkout(order.order_id, order.amount_cents, order.currency)
        data["payment_url"] = checkout_result["payment_url"]
    return api_success(data, ctx.request_id)


@app.route("/api/billing/webhook", methods=["POST"])
def payment_webhook():
    ctx = _ctx()
    cfg = load_runtime_config()
    raw = request.get_data()
    signature = request.headers.get("X-Payment-Signature", "")
    from src.billing.payment_gateway import verify_webhook_signature
    if not verify_webhook_signature(raw, signature, cfg.payment_webhook_secret or "dev-webhook-secret"):
        raise ApiError("unauthorized", "Invalid payment signature", status_code=401)
    payload = get_json_body()
    require_fields(payload, ["event_id", "order_id", "event_type"])
    try:
        result = _billing_service.handle_payment_event(payload)
    except ValueError as e:
        raise ApiError("billing_error", str(e), status_code=400)
    return api_success(result, ctx.request_id)


@app.route("/api/billing/webhook/mock", methods=["POST"])
def mock_payment_webhook():
    ctx = _ctx()
    cfg = load_runtime_config()
    if not cfg.enable_dev_payment_webhook:
        raise ApiError("not_found", "Mock webhook disabled", status_code=404)
    payload = get_json_body()
    require_fields(payload, ["event_id", "order_id", "event_type"])
    try:
        result = _billing_service.handle_payment_event(payload)
    except ValueError as e:
        raise ApiError("billing_error", str(e), status_code=400)
    return api_success(result, ctx.request_id)


# -- H5 Frontend static serving ------------------------------------------------

_H5_DIR = Path(__file__).parent.parent.parent / "frontend" / "h5"


@app.route("/h5")
def h5_index():
    if not _runtime_config.enable_h5:
        raise ApiError("not_found", "H5 frontend is disabled", status_code=404)
    return send_from_directory(_H5_DIR, "index.html")


@app.route("/h5/<path:filename>")
def h5_asset(filename):
    if not _runtime_config.enable_h5:
        raise ApiError("not_found", "H5 frontend is disabled", status_code=404)
    return send_from_directory(_H5_DIR, filename)


# -- Admin / Refresh Job endpoints ---------------------------------------------

from src.jobs.job_store import RefreshJobStore
from src.jobs.runner import RefreshJobRunner

_JOBS_DIR = _runtime_config.data_dir / "jobs"
_refresh_runner = RefreshJobRunner(RefreshJobStore(_JOBS_DIR / "refresh_runs.json"))


@app.route("/api/admin/jobs/refresh/latest")
def admin_latest_refresh_run():
    ctx = _ctx()
    ctx.require_tier("admin")
    run = _refresh_runner.store.latest_run()
    if run is None:
        return api_success({"run": None, "attempts": []}, ctx.request_id)
    attempts = _refresh_runner.store.list_attempts(run.run_id)
    return api_success(
        {"run": run.to_dict(), "attempts": [item.to_dict() for item in attempts]},
        ctx.request_id,
    )


@app.route("/api/admin/jobs/refresh/run", methods=["POST"])
def admin_run_refresh_job():
    ctx = _ctx()
    ctx.require_tier("admin")
    payload = get_json_body()
    try:
        window_hours = int(payload.get("window_hours", 48))
    except (ValueError, TypeError):
        raise ApiError("bad_request", "window_hours must be an integer", status_code=400)
    if window_hours < 1:
        raise ApiError("bad_request", "window_hours must be >= 1", status_code=400)
    raw_force = payload.get("force", False)
    if not isinstance(raw_force, bool):
        raise ApiError("validation_error", "force must be a boolean", status_code=400)
    force = bool(raw_force)
    run = _refresh_runner.run(window_hours=window_hours, force=force)
    return api_success(run.to_dict(), ctx.request_id)


# -- Main -----------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=_runtime_config.port, debug=_runtime_config.flask_debug)






