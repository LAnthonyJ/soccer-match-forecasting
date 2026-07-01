"""
Versioned prediction snapshots for web/API output.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from src.models.market_fusion import market_edge
from src.models.score_models import ScoreMatrix


@dataclass(frozen=True)
class PredictionSnapshot:
    match_id: str
    home_team: str
    away_team: str
    model_version: str
    data_version: str
    generated_at: datetime
    confidence: float
    final_probs: dict[str, float]
    market_probs: dict[str, float] | None
    top_scorelines: list[tuple[int, int, float]]
    expected_goals: tuple[float, float]
    over25: float
    btts: float
    score_heatmap: list[list[float]]
    # Step 7 production fields
    lineup_data_quality: str | None = None
    lineup_adjustment: dict | None = None
    missing_key_players: list | None = None
    prematch_snapshot_id: str | None = None
    data_updated_at: str | None = None
    model_pipeline: dict | None = None

    def to_public_dict(self, tier: str = "basic") -> dict:
        payload = {
            "match_id": self.match_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "model_version": self.model_version,
            "data_version": self.data_version,
            "generated_at": self.generated_at.isoformat(),
            "confidence": self.confidence,
            "probabilities": self.final_probs,
            "top_scorelines": self.top_scorelines[:3],
            "expected_goals": {
                "home": self.expected_goals[0],
                "away": self.expected_goals[1],
            },
            # Basic production info
            "lineup_data_status": self.lineup_data_quality or "none",
            "data_updated_at": self.data_updated_at,
        }
        if tier in ("vip", "internal"):
            payload.update({
                "top_scorelines": self.top_scorelines,
                "over25": self.over25,
                "btts": self.btts,
                "score_heatmap": self.score_heatmap,
                "market_probs": self.market_probs,
                "market_edge": market_edge(self.final_probs, self.market_probs) if self.market_probs else None,
                "lineup_adjustment": self.lineup_adjustment,
                "missing_key_players": self.missing_key_players,
                "prematch_snapshot_id": self.prematch_snapshot_id,
                "model_pipeline": self.model_pipeline,
            })
        if tier == "internal":
            payload["_tier"] = "internal"
        return payload


def build_prediction_snapshot(
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    score_matrix: ScoreMatrix,
    final_probs: Mapping[str, float],
    model_version: str,
    data_version: str,
    confidence: float,
    generated_at: datetime | None = None,
    market_probs: Mapping[str, float] | None = None,
    lineup_data_quality: str | None = None,
    lineup_adjustment: dict | None = None,
    missing_key_players: list | None = None,
    prematch_snapshot_id: str | None = None,
    data_updated_at: str | None = None,
    model_pipeline: dict | None = None,
) -> PredictionSnapshot:
    over25, _ = score_matrix.over_under_probability(2.5)
    btts, _ = score_matrix.btts_probability()
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    return PredictionSnapshot(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        model_version=model_version,
        data_version=data_version,
        generated_at=generated_at,
        confidence=float(confidence),
        final_probs={k: float(v) for k, v in final_probs.items()},
        market_probs={k: float(v) for k, v in market_probs.items()} if market_probs else None,
        top_scorelines=score_matrix.top_scorelines(15),
        expected_goals=score_matrix.expected_goals(),
        over25=float(over25),
        btts=float(btts),
        score_heatmap=score_matrix.matrix.tolist(),
        lineup_data_quality=lineup_data_quality,
        lineup_adjustment=lineup_adjustment,
        missing_key_players=missing_key_players,
        prematch_snapshot_id=prematch_snapshot_id,
        data_updated_at=data_updated_at,
        model_pipeline=model_pipeline,
    )
