"""
Unified prediction service 鈥?single entry point for all prediction consumers.

CLI, web, API, smoke tests all call predict_match(). The service handles:
  - Sportmonks data fetching (fixture, odds, lineups, squad strengths)
  - Prematch snapshot construction
  - Lineup signal 鈫?lambda adjustment
  - Engine prediction with market fusion + goal calibration
  - Basic/VIP output
  - Graceful degradation at every layer

Usage:
    service = PredictionService()
    result = service.predict_match(fixture_id=19606960)
    payload = result.snapshot.to_public_dict(tier="basic")
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.prematch_snapshot import (
    PrematchDataSnapshot,
    build_prematch_snapshot,
    build_sportmonks_prematch_snapshot,
)
from src.data.provider_schema import MatchRecord
from src.data.sportmonks_adapter import SportmonksAdapter
from src.data.sportmonks_client import get_fixture
from src.features.lineup_signals import build_lineup_signals
from src.models.lineup_adjustment import (
    LineupAdjustmentConfig,
    LineupLambdaAdjustment,
    build_lineup_lambda_adjustment,
)
from src.models.prediction_snapshot import PredictionSnapshot
from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.score_models import ScoreMatrix, load_score_models
from src.config.runtime import load_runtime_config

# Cache directory
CACHE_DIR = load_runtime_config().data_dir / "prediction_snapshots"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PredictionResult:
    snapshot: PredictionSnapshot
    fallback_used: list[str]
    warnings: list[str]


class PredictionService:
    """
    Unified prediction service.

    All prediction paths (CLI, web, API) should call predict_match()
    rather than assembling the pipeline manually.
    """

    def __init__(
        self,
        goal_multiplier: float = 1.10,
        model_version: str = "dynamic-dc-v1",
    ):
        self._goal_multiplier = goal_multiplier
        self._model_version = model_version

    def predict_match(
        self,
        fixture_id: int | None = None,
        match: MatchRecord | None = None,
        *,
        use_prematch_api: bool = True,
        manual_lineups_path: str | None = None,
        manual_injuries_path: str | None = None,
        manual_strengths_path: str | None = None,
    ) -> PredictionResult:
        """
        Run a full prediction for one match.

        Returns PredictionResult with:
          - snapshot: the PredictionSnapshot
          - fallback_used: machine-readable degradation chain (e.g. odds_missing, prematch_api_failed)
          - warnings: human-readable explanatory messages
        """
        fallback: list[str] = []
        warnings: list[str] = []
        adapter = SportmonksAdapter()

        # 鈹€鈹€ 1. Resolve fixture (fatal if fails) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        if match is None and fixture_id is not None:
            try:
                raw = get_fixture(fixture_id, includes="participants;scores;league")
                if raw is None:
                    raise RuntimeError(f"Fixture {fixture_id} not found")
                match = adapter.parse_match(raw)
            except Exception as e:
                raise RuntimeError(f"Failed to fetch fixture {fixture_id}: {e}")
        elif match is None:
            raise ValueError("Either fixture_id or match must be provided")

        # 鈹€鈹€ 2. Fetch odds (degradable) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        odds = None
        try:
            odds = adapter.average_odds(fixture_id) if fixture_id else None
        except Exception:
            pass
        if odds is None:
            fallback.append("odds_missing")
            warnings.append("market odds unavailable 鈥?model-only prediction")

        # 鈹€鈹€ 3. Build prematch snapshot 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        hid = aid = None
        if fixture_id and use_prematch_api:
            try:
                raw = get_fixture(fixture_id, includes="participants")
            except Exception:
                raw = None
            if raw:
                for p in raw.get("participants", []):
                    meta = p.get("meta", {})
                    if isinstance(meta, dict):
                        loc = meta.get("location", "")
                        if loc == "home":
                            hid = p.get("id")
                        elif loc == "away":
                            aid = p.get("id")

        prematch_api_ok = True
        if use_prematch_api and fixture_id:
            try:
                snap = build_sportmonks_prematch_snapshot(
                    match, fixture_id=fixture_id,
                    home_team_id=hid, away_team_id=aid,
                    manual_lineups_path=manual_lineups_path,
                    manual_injuries_path=manual_injuries_path,
                    manual_strengths_path=manual_strengths_path,
                )
            except Exception:
                fallback.append("prematch_api_failed")
                warnings.append("Sportmonks prematch API failed 鈥?using manual/empty snapshot")
                prematch_api_ok = False
        if not use_prematch_api or not fixture_id or not prematch_api_ok:
            snap = build_prematch_snapshot(
                match,
                manual_lineups_path=manual_lineups_path,
                manual_injuries_path=manual_injuries_path,
                manual_strengths_path=manual_strengths_path,
            )

        # 鈹€鈹€ 4. Build lineup signals 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        lineups_by_team: dict[str, list] = {}
        for p in snap.lineups:
            lineups_by_team.setdefault(p.team, []).append(p)

        lineup_adj: LineupLambdaAdjustment | None = None
        try:
            signals = build_lineup_signals(
                match,
                lineups=lineups_by_team or None,
                injuries=snap.injuries or None,
                player_strengths=snap.player_strengths or None,
                lineup_type=snap.lineup_type if snap.has_lineups() else "none",
            )
            lineup_adj = build_lineup_lambda_adjustment(
                signals["home"], signals["away"],
                config=LineupAdjustmentConfig(),
            )
        except Exception:
            fallback.append("lineup_signals_failed")
            warnings.append("lineup signal build failed 鈥?continuing without adjustments")
            lineup_adj = None

        # 鈹€鈹€ 5. Build engine + predict (fatal if DC model missing) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        try:
            score_models = load_score_models()
            dc_model = score_models.get("dixon_coles")
            if dc_model is None:
                raise RuntimeError("DC model not found in score_models.pkl")
        except Exception:
            fallback.append("dc_model_load_failed")
            raise RuntimeError("Critical: DC model cannot be loaded 鈥?prediction unavailable")

        engine = ProductionPredictionEngine(
            base_dc_model=dc_model,
            model_version=self._model_version,
            data_version=f"sportmonks-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            goal_multiplier=self._goal_multiplier,
        )

        quality = PredictionInputQuality(
            team_sample_home=max(len(snap.player_strengths) // 2, 1),
            team_sample_away=max(len(snap.player_strengths) // 2, 1),
            has_lineups=snap.has_lineups(),
            has_injuries=snap.has_injuries(),
            odds_snapshots=21 if odds else 0,
            odds_volatility=0.15 if odds else 0.25,
        )

        snapshot = engine.predict(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            odds=odds,
            quality=quality,
            neutral=match.neutral,
            lineup_adjustment=lineup_adj if (lineup_adj and lineup_adj.has_signal()) else None,
            prematch_snapshot=snap,
        )

        return PredictionResult(
            snapshot=snapshot,
            fallback_used=fallback,
            warnings=warnings,
        )

    def predict_and_save(
        self,
        fixture_id: int,
        tier: str = "vip",
        **kwargs,
    ) -> tuple[PredictionSnapshot, Path]:
        """Predict + persist to cache. tier only affects saved JSON output."""
        result = self.predict_match(fixture_id=fixture_id, **kwargs)
        path = save_prediction_snapshot(result.snapshot, tier=tier)
        return result.snapshot, path


# -- Cache helpers ---------------------------------------------------------------

def save_prediction_snapshot(
    snapshot: PredictionSnapshot,
    path: str | Path | None = None,
    tier: str = "internal",
) -> Path:
    """Persist a prediction snapshot to JSON cache. Always saves full internal payload.

    tier only affects the PUBLIC output layer. Cache always saves complete data
    so that any user tier can be served from the same cached file.
    """
    if path is None:
        ts = snapshot.generated_at.strftime("%Y%m%dT%H%M%S")
        model_dir = CACHE_DIR / snapshot.match_id / snapshot.model_version
        model_dir.mkdir(parents=True, exist_ok=True)
        path = model_dir / f"{ts}.json"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_public_dict(tier="internal")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_latest_prediction(
    match_id: str,
    model_version: str | None = None,
) -> dict | None:
    """Load the most recent prediction snapshot for a match."""
    search_dir = CACHE_DIR / match_id
    if model_version:
        search_dir = search_dir / model_version

    json_files = sorted(search_dir.glob("**/*.json")) if search_dir.exists() else []
    if not json_files:
        return None

    return json.loads(json_files[-1].read_text(encoding="utf-8"))


def load_prediction_by_snapshot_id(snapshot_id: str) -> dict | None:
    """Load a prediction by its snapshot_id (match_id_timestamp format)."""
    parts = snapshot_id.rsplit("_", 1)
    if len(parts) != 2:
        return None
    match_id = parts[0]
    ts = parts[1]
    search_dir = CACHE_DIR / match_id
    for f in sorted(search_dir.glob("**/*.json"), reverse=True):
        if ts in f.name:
            return json.loads(f.read_text(encoding="utf-8"))
    return None


def list_cached_predictions(match_id: str) -> list[dict]:
    """List all cached prediction snapshots for a match (newest first)."""
    search_dir = CACHE_DIR / match_id
    if not search_dir.exists():
        return []
    results = []
    for f in sorted(search_dir.glob("**/*.json"), reverse=True):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return results



