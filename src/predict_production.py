"""
Production-grade prediction entrypoint.

This keeps the Streamlit prototype intact while exposing the upgraded model
stack for H5/API integration:
Dynamic Dixon-Coles -> total-goals calibration -> market-prior fusion ->
versioned tiered snapshot.
"""
import argparse
import json
import os
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

warnings.filterwarnings('ignore', message='.*serialized model.*', category=UserWarning)
warnings.filterwarnings('ignore', message='Trying to unpickle estimator.*', category=UserWarning)

from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.score_models import load_score_models


def build_engine(model_version: str = "dynamic-dc-v1", data_version: str | None = None) -> ProductionPredictionEngine:
    score_models = load_score_models()
    dc_model = score_models.get("dixon_coles")
    if dc_model is None:
        raise RuntimeError("score_models.pkl does not contain a dixon_coles model")

    goal_multiplier = float(os.environ.get("TOTAL_GOALS_MULTIPLIER", "1.10"))
    if data_version is None:
        data_version = f"local-files-{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    return ProductionPredictionEngine(
        base_dc_model=dc_model,
        model_version=model_version,
        data_version=data_version,
        goal_multiplier=goal_multiplier,
    )


def predict_one(
    *,
    home_team: str,
    away_team: str,
    odds: tuple[float, float, float] | None,
    tier: str,
    output_path: str | None = None,
) -> dict:
    engine = build_engine()
    snapshot = engine.predict(
        match_id=f"{home_team}-{away_team}-{datetime.now(UTC).date()}",
        home_team=home_team,
        away_team=away_team,
        odds=odds,
        quality=PredictionInputQuality(
            team_sample_home=60,
            team_sample_away=60,
            has_lineups=False,
            has_injuries=False,
            odds_snapshots=1 if odds else 0,
            odds_volatility=0.20,
        ),
        neutral=True,
    )
    payload = snapshot.to_public_dict(tier=tier)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def main():
    parser = argparse.ArgumentParser(description="Run upgraded production prediction")
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--odds-home", type=float)
    parser.add_argument("--odds-draw", type=float)
    parser.add_argument("--odds-away", type=float)
    parser.add_argument("--tier", choices=["basic", "vip"], default="vip")
    parser.add_argument("--output")
    args = parser.parse_args()

    odds = None
    if args.odds_home and args.odds_draw and args.odds_away:
        odds = (args.odds_home, args.odds_draw, args.odds_away)

    payload = predict_one(
        home_team=args.home,
        away_team=args.away,
        odds=odds,
        tier=args.tier,
        output_path=args.output,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()



