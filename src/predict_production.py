"""
Production-grade prediction entrypoint.

This keeps the Streamlit prototype intact while exposing the upgraded model
stack for H5/API integration:
Dynamic Dixon-Coles -> total-goals calibration -> market-prior fusion ->
versioned tiered snapshot.
"""
import argparse
import json
import math
import os
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

warnings.filterwarnings('ignore', message='.*serialized model.*', category=UserWarning)
warnings.filterwarnings('ignore', message='Trying to unpickle estimator.*', category=UserWarning)

from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.score_models import DixonColesModel, load_score_models


def build_engine(model_version: str = "dynamic-dc-v1", data_version: str | None = None,
                 *, bundled_model: bool = False) -> ProductionPredictionEngine:
    if bundled_model:
        path = Path(__file__).resolve().parents[1] / "config" / "dixon_coles.json"
        params = json.loads(path.read_text(encoding="utf-8"))
        dc_model = DixonColesModel()
        dc_model.alpha, dc_model.beta = params["alpha"], params["beta"]
        dc_model.gamma, dc_model.rho = params["gamma"], params["rho"]
        dc_model.teams_ = sorted(dc_model.alpha)
        data_version = data_version or params["version"]
    else:
        dc_model = load_score_models().get("dixon_coles")
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
    bundled_model: bool = False,
) -> dict:
    engine = build_engine(bundled_model=bundled_model)
    if bundled_model:
        for team in (home_team, away_team):
            if team not in engine.dynamic_service.base_model.alpha:
                raise ValueError(f"No bundled model parameters for {team!r}. See config/dixon_coles.json.")
    snapshot = engine.predict(
        match_id=f"{home_team}-{away_team}-{datetime.now(UTC).date()}",
        home_team=home_team,
        away_team=away_team,
        odds=odds,
        quality=PredictionInputQuality(
            has_lineups=False,
            has_injuries=False,
            odds_snapshots=1 if odds else 0,
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
    parser.add_argument("--bundled-model", action="store_true",
                        help="Use the bundled Dixon-Coles JSON parameters for an offline forecast")
    args = parser.parse_args()

    odds = None
    supplied = (args.odds_home, args.odds_draw, args.odds_away)
    if any(value is not None for value in supplied):
        if any(value is None for value in supplied):
            parser.error("Supply --odds-home, --odds-draw and --odds-away together")
        if any(not math.isfinite(value) or value <= 1 for value in supplied):
            parser.error("Decimal odds must be finite values greater than 1")
        odds = supplied

    try:
        payload = predict_one(home_team=args.home, away_team=args.away, odds=odds,
                              tier=args.tier, output_path=args.output, bundled_model=args.bundled_model)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()



