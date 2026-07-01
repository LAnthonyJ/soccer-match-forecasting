"""
Production prediction orchestration.

This module combines the stable Dixon-Coles base model with the new production
layers: dynamic adjustments, total-goals calibration, market-prior fusion, and
versioned snapshots for H5/API output.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from src.models.dynamic_dc import DynamicDixonColesService
from src.models.market_fusion import (
    MarketFusionConfig,
    blend_with_market,
    confidence_from_inputs,
    normalize_three_way_odds,
)
from src.models.prediction_snapshot import PredictionSnapshot, build_prediction_snapshot
from src.models.total_goals_calibration import TotalGoalsCalibrator


@dataclass(frozen=True)
class PredictionInputQuality:
    team_sample_home: int = 0
    team_sample_away: int = 0
    has_lineups: bool = False
    has_injuries: bool = False
    odds_snapshots: int = 1
    odds_volatility: float = 0.25


class ProductionPredictionEngine:
    """High-level prediction service used by web/API layers."""

    def __init__(
        self,
        *,
        base_dc_model,
        model_version: str,
        data_version: str,
        goal_multiplier: float = 1.0,
        dynamic_service: DynamicDixonColesService | None = None,
    ):
        self.model_version = model_version
        self.data_version = data_version
        self.dynamic_service = dynamic_service or DynamicDixonColesService(base_dc_model)
        self.goal_calibrator = TotalGoalsCalibrator(goal_multiplier)

    def predict(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        odds: tuple[float, float, float] | None = None,
        quality: PredictionInputQuality | None = None,
        neutral: bool = True,
        context: Mapping[str, float] | None = None,
        generated_at: datetime | None = None,
        match_record=None,
        learned_calibrator=None,
        lineup_adjustment=None,
        prematch_snapshot=None,  # PrematchDataSnapshot
    ) -> PredictionSnapshot:
        # Merge lineup adjustment context with caller-provided context
        merged_context = dict(context or {})
        if lineup_adjustment is not None:
            merged_context.update(lineup_adjustment.to_context())

        raw_matrix = self.dynamic_service.predict_score_matrix(
            home_team,
            away_team,
            neutral=neutral,
            context=merged_context or None,
        )
        if learned_calibrator is not None and match_record is not None:
            score_matrix = learned_calibrator.apply(match_record, raw_matrix)
        else:
            score_matrix = self.goal_calibrator.apply(raw_matrix)
        model_probs = score_matrix.match_outcome_probs()

        market_probs = None
        confidence = 1.0
        final_probs = model_probs

        if odds is not None:
            market_probs = normalize_three_way_odds(*odds)
            q = quality or PredictionInputQuality()
            confidence = confidence_from_inputs(
                team_sample_home=q.team_sample_home,
                team_sample_away=q.team_sample_away,
                has_lineups=q.has_lineups,
                has_injuries=q.has_injuries,
                odds_snapshots=q.odds_snapshots,
                odds_volatility=q.odds_volatility,
            )
            if lineup_adjustment is not None:
                confidence = min(confidence + lineup_adjustment.confidence_bonus,
                                 MarketFusionConfig().max_confidence)
            final_probs = blend_with_market(model_probs, market_probs, confidence)

        # Step 7: production metadata
        lq = (prematch_snapshot.data_quality if prematch_snapshot else None)
        sid = prematch_snapshot.snapshot_id if prematch_snapshot else None
        data_ts = (prematch_snapshot.fetched_at.isoformat() if prematch_snapshot
                   else (generated_at.isoformat() if generated_at else None))
        lu_adj = (lineup_adjustment.to_context() if (lineup_adjustment and lineup_adjustment.has_signal()) else None)
        missing = (
            lineup_adjustment.explanation
            if (lineup_adjustment and lineup_adjustment.has_signal())
            else None
        )

        pipeline = self._build_pipeline(lineup_adjustment is not None and lineup_adjustment.has_signal())

        return build_prediction_snapshot(
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            score_matrix=score_matrix,
            final_probs=final_probs,
            model_version=self.model_version,
            data_version=self.data_version,
            confidence=confidence,
            generated_at=generated_at,
            market_probs=market_probs,
            lineup_data_quality=lq,
            lineup_adjustment=lu_adj,
            missing_key_players=missing,
            prematch_snapshot_id=sid,
            data_updated_at=data_ts,
            model_pipeline=pipeline,
        )

    def _build_pipeline(self, has_lineup: bool) -> dict:
        return {
            "base_model": "dynamic-dc-v1",
            "market_fusion": True,
            "goal_calibration": self.goal_calibrator.lambda_multiplier != 1.0,
            "goal_multiplier": self.goal_calibrator.lambda_multiplier,
            "lineup_adjustment": has_lineup,
            "model_version": self.model_version,
        }
