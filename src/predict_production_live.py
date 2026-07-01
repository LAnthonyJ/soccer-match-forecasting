"""
Live production prediction using Sportmonks data.

Combines the Sportmonks ingestion pipeline with the ProductionPredictionEngine
to produce versioned, market-fused snapshots for active fixtures.

Usage:
    # Single match prediction
    python src/predict_production_live.py --fixture-id 19609197

    # All upcoming international matches
    python src/predict_production_live.py --upcoming --days 7

    # All finished matches in a date range (for backtesting)
    python src/predict_production_live.py --from 2026-06-01 --to 2026-06-30
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Don't fail on optional ML deps at import time
try:
    from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
    from src.models.score_models import load_score_models
    from src.data.sportmonks_ingestion import SportmonksIngestion, INTERNATIONAL_LEAGUES
    from src.data.sportmonks_client import get_fixtures_between
    from src.data.sportmonks_adapter import SportmonksAdapter
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    print("Some dependencies may be missing. Install with: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "predictions"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build_engine() -> ProductionPredictionEngine:
    score_models = load_score_models()
    dc_model = score_models.get("dixon_coles")
    if dc_model is None:
        raise RuntimeError("score_models.pkl does not contain a dixon_coles model")

    goal_multiplier = float(os.environ.get("TOTAL_GOALS_MULTIPLIER", "1.10"))

    return ProductionPredictionEngine(
        base_dc_model=dc_model,
        model_version=f"sportmonks-dc-v1",
        data_version=f"sportmonks-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        goal_multiplier=goal_multiplier,
    )


def predict_fixture_historical(fixture_id: int, tier: str = "vip") -> dict | None:
    """Predict a PAST fixture using ONLY historical data — no current squads/odds.

    This avoids time leakage: for backtesting, we use the fixture's closing odds
    (which are historical snapshots from the fixture endpoint) and skip
    current-squad lookups that would leak future information.
    """
    from src.data.sportmonks_client import get_fixture

    fixt = get_fixture(fixture_id, includes="participants;scores;league")
    if not fixt:
        print(f"  [SKIP] Fixture {fixture_id}: no historical data")
        return None

    adapter = SportmonksAdapter()
    match = adapter.parse_match(fixt)

    # Use historical closing odds (from fixture endpoint, NOT current market)
    odds_avg = adapter.average_odds(fixture_id)

    # Conservative defaults — no current squad data for historical matches
    quality = PredictionInputQuality(
        team_sample_home=30,
        team_sample_away=30,
        has_lineups=False,
        has_injuries=False,
        odds_snapshots=1,
        odds_volatility=0.25,
    )

    engine = build_engine()

    try:
        snapshot = engine.predict(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            odds=odds_avg,
            quality=quality,
            neutral=match.neutral,
            context=None,
        )
    except Exception as e:
        print(f"  [ERROR] Historical prediction failed for {match.home_team} vs {match.away_team}: {e}")
        return None

    payload = snapshot.to_public_dict(tier=tier)
    payload["_meta"] = {
        "tournament": match.tournament,
        "kickoff_utc": match.kickoff_utc.isoformat(),
        "status": match.status,
        "home_score": match.home_score,
        "away_score": match.away_score,
    }
    return payload


def predict_fixture(fixture_id: int, tier: str = "vip") -> dict | None:
    """Run production prediction for a single Sportmonks fixture (live/upcoming)."""
    pipeline = SportmonksIngestion()
    data = pipeline.ingest_prediction_ready(fixture_id)

    if not data:
        print(f"  [SKIP] Fixture {fixture_id}: no data")
        return None

    match = data["match"]
    odds = data["odds"]
    quality_dict = data["quality"]
    context = data["context"]

    engine = build_engine()

    try:
        snapshot = engine.predict(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            odds=odds,
            quality=PredictionInputQuality(
                team_sample_home=quality_dict["team_sample_home"],
                team_sample_away=quality_dict["team_sample_away"],
                has_lineups=quality_dict["has_lineups"],
                has_injuries=quality_dict["has_injuries"],
                odds_snapshots=quality_dict["odds_snapshots"],
                odds_volatility=quality_dict["odds_volatility"],
            ),
            neutral=match.neutral,
            context=context,
        )
    except Exception as e:
        print(f"  [ERROR] Prediction failed for {match.home_team} vs {match.away_team}: {e}")
        return None

    payload = snapshot.to_public_dict(tier=tier)
    payload["_meta"] = {
        "tournament": match.tournament,
        "kickoff_utc": match.kickoff_utc.isoformat(),
        "status": match.status,
        "home_score": match.home_score,
        "away_score": match.away_score,
    }

    return payload


def predict_upcoming(days: int = 7, tier: str = "vip") -> list[dict]:
    """Predict all upcoming international fixtures in the next N days."""
    now = datetime.now(timezone.utc)
    to_date = now + timedelta(days=days)
    league_ids = list(INTERNATIONAL_LEAGUES.keys())

    fixtures = get_fixtures_between(
        now, to_date,
        league_ids=league_ids,
        includes="participants;league",
    )

    results = []
    adapter = SportmonksAdapter()

    print(f"Found {len(fixtures)} upcoming fixtures in {days} days\n")

    for fixt in fixtures:
        fid = fixt["id"]
        match = adapter.parse_match(fixt)
        print(f"  [{match.tournament}] {match.home_team} vs {match.away_team} @ {match.kickoff_utc}")

        payload = predict_fixture(fid, tier=tier)
        if payload:
            results.append(payload)

    return results


def predict_range(from_date: str, to_date: str, tier: str = "vip") -> list[dict]:
    """Predict all international fixtures in a date range (for backtesting)."""
    fd = datetime.fromisoformat(from_date)
    td = datetime.fromisoformat(to_date)
    league_ids = list(INTERNATIONAL_LEAGUES.keys())

    fixtures = get_fixtures_between(
        fd, td,
        league_ids=league_ids,
        includes="participants;scores;league",
    )

    results = []
    adapter = SportmonksAdapter()

    print(f"Found {len(fixtures)} fixtures in {from_date} to {to_date}\n")

    for fixt in fixtures:
        fid = fixt["id"]
        match = adapter.parse_match(fixt)
        actual = ""
        if match.home_score is not None:
            actual = f"  Actual: {match.home_score}-{match.away_score}"
        print(f"  [{match.tournament}] {match.home_team} vs {match.away_team}{actual}")

        payload = predict_fixture_historical(fid, tier=tier)
        if payload:
            results.append(payload)

    return results


def _save(results: list[dict], prefix: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = OUTPUT_DIR / f"{prefix}_{ts}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# -- CLI ------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Live production prediction via Sportmonks")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fixture-id", type=int, help="Single fixture ID to predict")
    group.add_argument("--upcoming", action="store_true", help="Predict all upcoming matches")
    group.add_argument("--range", action="store_true", help="Predict all matches in a date range")
    parser.add_argument("--days", type=int, default=7, help="Days ahead for --upcoming (default: 7)")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--tier", choices=["basic", "vip"], default="vip")
    parser.add_argument("--output", help="Output file path (optional)")
    args = parser.parse_args()

    results = []

    if args.fixture_id:
        payload = predict_fixture(args.fixture_id, tier=args.tier)
        if payload:
            results = [payload]
    elif args.upcoming:
        results = predict_upcoming(days=args.days, tier=args.tier)
    elif args.range:
        if not args.from_date or not args.to_date:
            print("Error: --from and --to required with --range", file=sys.stderr)
            sys.exit(1)
        results = predict_range(args.from_date, args.to_date, tier=args.tier)

    # Save
    if results:
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            prefix = f"fixture_{args.fixture_id}" if args.fixture_id else "upcoming" if args.upcoming else "range"
            path = _save(results, prefix)

        print(f"\nSaved {len(results)} prediction(s) to {path}")

        # Print summary
        for r in results:
            probs = r["probabilities"]
            edge = r.get("market_edge", {})
            print(f"\n  {r['match_id']}")
            print(f"    H: {probs['home']:.1%}  D: {probs['draw']:.1%}  A: {probs['away']:.1%}")
            print(f"    Confidence: {r['confidence']:.0%}")
            if edge:
                print(f"    Edge vs Mkt: H={edge['home']:+.1%}  D={edge['draw']:+.1%}  A={edge['away']:+.1%}")

    print("\nDone.")


if __name__ == "__main__":
    main()
