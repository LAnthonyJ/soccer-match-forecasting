"""
Market-prior fusion utilities for production predictions.

The market is treated as a strong prior. The football model can move away from
it only when input quality is high enough to justify that deviation.
"""
from dataclasses import dataclass
from typing import Mapping

import numpy as np


OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class MarketFusionConfig:
    min_confidence: float = 0.15
    max_confidence: float = 0.85
    target_team_sample: int = 60
    target_odds_snapshots: int = 4
    high_volatility: float = 0.25
    lineup_bonus: float = 0.10
    injury_bonus: float = 0.08


def normalize_three_way_odds(home_odds: float, draw_odds: float, away_odds: float) -> dict[str, float]:
    """Convert 1X2 decimal odds to de-vigged implied probabilities."""
    odds = np.array([home_odds, draw_odds, away_odds], dtype=float)
    if np.any(~np.isfinite(odds)) or np.any(odds <= 1.0):
        raise ValueError("Decimal odds must be finite values greater than 1.0")

    raw = 1.0 / odds
    probs = raw / raw.sum()
    return {outcome: float(prob) for outcome, prob in zip(OUTCOMES, probs)}


def _normalize_probs(probs: Mapping[str, float]) -> dict[str, float]:
    arr = np.array([float(probs[k]) for k in OUTCOMES], dtype=float)
    if np.any(~np.isfinite(arr)) or np.any(arr < 0):
        raise ValueError("Probabilities must be finite non-negative values")
    total = arr.sum()
    if total <= 0:
        raise ValueError("At least one probability must be positive")
    arr = arr / total
    return {outcome: float(prob) for outcome, prob in zip(OUTCOMES, arr)}


def blend_with_market(
    model_probs: Mapping[str, float],
    market_probs: Mapping[str, float],
    confidence_weight: float,
) -> dict[str, float]:
    """
    Blend model probabilities with market probabilities.

    confidence_weight=0 returns market only; confidence_weight=1 returns model
    only. Values are clipped to [0, 1] and the result is normalized.
    """
    model = _normalize_probs(model_probs)
    market = _normalize_probs(market_probs)
    w = float(np.clip(confidence_weight, 0.0, 1.0))
    blended = {
        outcome: w * model[outcome] + (1.0 - w) * market[outcome]
        for outcome in OUTCOMES
    }
    return _normalize_probs(blended)


def confidence_from_inputs(
    *,
    team_sample_home: int,
    team_sample_away: int,
    has_lineups: bool,
    has_injuries: bool,
    odds_snapshots: int,
    odds_volatility: float,
    config: MarketFusionConfig | None = None,
) -> float:
    """Score input quality as a bounded model-vs-market confidence weight."""
    cfg = config or MarketFusionConfig()

    sample_quality = min(team_sample_home, team_sample_away) / max(cfg.target_team_sample, 1)
    sample_quality = float(np.clip(sample_quality, 0.0, 1.0))

    odds_depth = odds_snapshots / max(cfg.target_odds_snapshots, 1)
    odds_depth = float(np.clip(odds_depth, 0.0, 1.0))

    volatility_penalty = odds_volatility / max(cfg.high_volatility, 1e-9)
    volatility_quality = 1.0 - float(np.clip(volatility_penalty, 0.0, 1.0))

    base = 0.55 * sample_quality + 0.25 * odds_depth + 0.20 * volatility_quality
    if has_lineups:
        base += cfg.lineup_bonus
    if has_injuries:
        base += cfg.injury_bonus

    return float(np.clip(base, cfg.min_confidence, cfg.max_confidence))


def market_edge(final_probs: Mapping[str, float], market_probs: Mapping[str, float]) -> dict[str, float]:
    """Return probability differences for analysis display, not staking."""
    final_norm = _normalize_probs(final_probs)
    market_norm = _normalize_probs(market_probs)
    return {outcome: final_norm[outcome] - market_norm[outcome] for outcome in OUTCOMES}


def average_bookmaker_probs(
    bookmaker_odds: list[tuple[float, float, float]],
) -> dict[str, float] | None:
    """
    Average implied probabilities across bookmakers, then de-vig.

    Each element in bookmaker_odds is (home_odds, draw_odds, away_odds) from one
    bookmaker.  We convert each to raw implied probs, average the probabilities
    (equal weight per bookmaker), then remove the overround from the averaged
    probability triplet.

    Returns None if the input is empty.
    """
    if not bookmaker_odds:
        return None

    home_sum = 0.0
    draw_sum = 0.0
    away_sum = 0.0
    valid = 0

    for ho, do, ao in bookmaker_odds:
        if ho <= 1.0 or do <= 1.0 or ao <= 1.0:
            continue
        home_sum += 1.0 / ho
        draw_sum += 1.0 / do
        away_sum += 1.0 / ao
        valid += 1

    if valid == 0:
        return None

    raw = np.array([home_sum / valid, draw_sum / valid, away_sum / valid], dtype=float)
    probs = raw / raw.sum()
    return {outcome: float(prob) for outcome, prob in zip(OUTCOMES, probs)}


def build_market_baseline_snapshot(
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    market_probs: Mapping[str, float],
    score_matrix,  # ScoreMatrix — only used for xG / O/U / BTTS display
    generated_at: "datetime | None" = None,
) -> "PredictionSnapshot":
    """
    Build a prediction snapshot where HDA probabilities come from the market alone.

    The score_matrix is only used to populate expected-goals / Over-2.5 / BTTS
    for display completeness; the HDA decision is entirely market-driven.
    """
    from datetime import datetime as _dt, timezone as _tz

    if generated_at is None:
        generated_at = _dt.now(_tz.utc)
    elif generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=_tz.utc)

    from src.models.prediction_snapshot import PredictionSnapshot

    over25, _ = score_matrix.over_under_probability(2.5)
    btts, _ = score_matrix.btts_probability()
    e_h, e_a = score_matrix.expected_goals()

    return PredictionSnapshot(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        model_version="market-baseline",
        data_version="sportmonks-odds",
        generated_at=generated_at,
        confidence=1.0,
        final_probs={k: float(v) for k, v in market_probs.items()},
        market_probs={k: float(v) for k, v in market_probs.items()},
        top_scorelines=score_matrix.top_scorelines(15),
        expected_goals=(float(e_h), float(e_a)),
        over25=float(over25),
        btts=float(btts),
        score_heatmap=score_matrix.matrix.tolist(),
    )
