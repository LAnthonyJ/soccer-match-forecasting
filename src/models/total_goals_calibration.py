"""
Total-goals calibration layer for score matrices.

Corrects systematic under/over prediction in the total-goals distribution
without retraining the base Dixon-Coles model.

Two modes:
  Manual:   provide a fixed lambda_multiplier (e.g. 1.10 → +10% goals)
  Learned:  estimate multiplier from historical matches, with tournament /
            stage / neutral grouping and a fallback chain.

The calibration tilts the score matrix by total goals:
  P'(i,j) ∝ P(i,j) * multiplier^(i+j)
then renormalizes. This inflates or compresses the goal distribution tail
while preserving the HDA ratio structure.
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from src.data.provider_schema import MatchRecord
from src.models.score_models import MAX_GOALS, ScoreMatrix


# -- Config ---------------------------------------------------------------------

@dataclass(frozen=True)
class TotalGoalsCalibrationConfig:
    default_multiplier: float = 1.0
    min_multiplier: float = 0.85
    max_multiplier: float = 1.30
    min_group_matches: int = 20


# -- Group keys -----------------------------------------------------------------

def _group_key(match: MatchRecord) -> tuple[str, str, int]:
    """Build a canonical group key from a match record."""
    tournament = match.tournament or "Unknown"
    stage = match.stage or "Unknown"
    neutral = 1 if match.neutral else 0
    return (tournament, stage, neutral)


FALLBACK_ORDER = [
    "exact",       # (tournament, stage, neutral)
    "tournament",  # (tournament, *, *)
]


def _fallback_keys(group: tuple) -> list[tuple]:
    return [
        group,                              # exact
        (group[0], "*", group[2]),          # tournament + neutral
        (group[0], "*", "*"),               # tournament only
        ("*", "*", "*"),                    # global default
    ]


# -- Matrix tilting -------------------------------------------------------------

class TotalGoalsCalibrator:
    """Tilt a score matrix by total goals using a multiplicative weight."""

    def __init__(self, lambda_multiplier: float = 1.0):
        if lambda_multiplier <= 0:
            raise ValueError("lambda_multiplier must be positive")
        self.lambda_multiplier = float(lambda_multiplier)

    def apply(self, score_matrix: ScoreMatrix) -> ScoreMatrix:
        if self.lambda_multiplier == 1.0:
            return score_matrix

        weights = np.ones((MAX_GOALS, MAX_GOALS), dtype=float)
        for hg in range(MAX_GOALS):
            for ag in range(MAX_GOALS):
                weights[hg, ag] = self.lambda_multiplier ** (hg + ag)

        return ScoreMatrix(score_matrix.matrix * weights)


# -- Learned calibrator ---------------------------------------------------------

class LearnedTotalGoalsCalibrator:
    """
    Multi-group calibrator that estimates per-group lambda_multiplier
    from historical data, with a fallback chain.

    Usage:
        cal = LearnedTotalGoalsCalibrator(config)
        cal.fit(warmup_matches, score_predictor_fn)
        calibrated_matrix = cal.apply(match, raw_score_matrix)
    """

    def __init__(self, config: TotalGoalsCalibrationConfig | None = None):
        self.config = config or TotalGoalsCalibrationConfig()
        self._multipliers: dict[tuple, float] = {}
        self._group_counts: dict[tuple, int] = {}
        self._fitted = False

    def fit(
        self,
        matches: Iterable[MatchRecord],
        predict_fn,  # callable(match) → ScoreMatrix
    ) -> "LearnedTotalGoalsCalibrator":
        """
        Estimate per-group multipliers from historical matches.

        predict_fn(match) → ScoreMatrix: returns the RAW (uncalibrated)
        prediction for a given match. The calibrator compares predicted
        total goals against actual total goals.
        """
        groups: dict[tuple, list[float]] = defaultdict(list)
        # Also accumulate to global
        groups[("*", "*", "*")] = []

        for match in matches:
            if match.home_score is None or match.away_score is None:
                continue
            actual_total = float(match.home_score + match.away_score)
            try:
                sm = predict_fn(match)
            except Exception:
                continue
            pred_h, pred_a = sm.expected_goals()
            pred_total = float(pred_h + pred_a)

            ratio = actual_total / max(pred_total, 0.01)

            key = _group_key(match)
            groups[key].append(ratio)
            groups[("*", "*", "*")].append(ratio)

            # Tournament-only
            t_key = (key[0], "*", "*")
            groups[t_key].append(ratio)

            # Tournament + neutral
            tn_key = (key[0], "*", key[2])
            groups[tn_key].append(ratio)

        for key, ratios in groups.items():
            if len(ratios) >= self.config.min_group_matches:
                multiplier = float(np.clip(
                    np.mean(ratios),
                    self.config.min_multiplier,
                    self.config.max_multiplier,
                ))
                self._multipliers[key] = multiplier

        self._group_counts = {k: len(v) for k, v in groups.items()}
        self._fitted = True
        return self

    def multiplier_for(self, match: MatchRecord) -> float:
        """Resolve the calibration multiplier for a match via fallback chain."""
        if not self._fitted:
            return self.config.default_multiplier

        key = _group_key(match)
        for fk in _fallback_keys(key):
            m = self._multipliers.get(fk)
            if m is not None:
                return m
        return self.config.default_multiplier

    def apply(self, match: MatchRecord, score_matrix: ScoreMatrix) -> ScoreMatrix:
        m = self.multiplier_for(match)
        if m == 1.0:
            return score_matrix
        return TotalGoalsCalibrator(m).apply(score_matrix)

    def multiplier_map(self) -> dict[str, float]:
        """Human-readable multiplier table for reporting."""
        return {str(k): v for k, v in sorted(self._multipliers.items())}


# -- Goal calibration metrics ---------------------------------------------------

@dataclass(frozen=True)
class GoalCalibrationMetrics:
    mean_predicted_total: float
    mean_actual_total: float
    over25_ece: float
    btts_ece: float


def learn_global_multiplier(
    matches: Iterable[MatchRecord],
    predict_fn,  # callable(match) → ScoreMatrix
    config: TotalGoalsCalibrationConfig | None = None,
) -> float:
    """
    Learn a single global lambda_multiplier from historical matches.

    multiplier = mean(actual_total_goals) / mean(predicted_total_goals)
    clipped to [min_multiplier, max_multiplier].

    If there are fewer than 5 valid matches, returns default_multiplier.
    """
    cfg = config or TotalGoalsCalibrationConfig()
    ratios = []
    for match in matches:
        if match.home_score is None or match.away_score is None:
            continue
        actual_total = float(match.home_score + match.away_score)
        try:
            sm = predict_fn(match)
        except Exception:
            continue
        pred_h, pred_a = sm.expected_goals()
        pred_total = float(pred_h + pred_a)
        if pred_total <= 0:
            continue
        ratios.append(actual_total / pred_total)

    if len(ratios) < 5:
        return cfg.default_multiplier

    return float(np.clip(
        np.mean(ratios),
        cfg.min_multiplier,
        cfg.max_multiplier,
    ))


def compute_goal_calibration_metrics(
    results: Sequence,  # HistoricalBacktestMatchResult
    n_bins: int = 5,
) -> GoalCalibrationMetrics:
    """Compute goal-distribution calibration metrics from backtest results."""
    if not results:
        return GoalCalibrationMetrics(0.0, 0.0, 0.0, 0.0)

    pred_totals = []
    actual_totals = []
    over25_preds = []
    over25_actuals = []
    btts_preds = []
    btts_actuals = []

    for r in results:
        sm = ScoreMatrix(np.array(r.snapshot.score_heatmap, dtype=float))
        ph, pa = sm.expected_goals()
        pred_totals.append(float(ph + pa))
        actual_totals.append(float(r.actual_home_goals + r.actual_away_goals))

        over_p, _ = sm.over_under_probability(2.5)
        over25_preds.append(float(over_p))
        over25_actuals.append(1.0 if (r.actual_home_goals + r.actual_away_goals) > 2.5 else 0.0)

        btts_p, _ = sm.btts_probability()
        btts_preds.append(float(btts_p))
        btts_actuals.append(1.0 if (r.actual_home_goals > 0 and r.actual_away_goals > 0) else 0.0)

    mean_pred = float(np.mean(pred_totals))
    mean_act = float(np.mean(actual_totals))

    def _ece(preds, actuals):
        bins = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            if i == n_bins - 1:
                mask = (np.array(preds) >= bins[i]) & (np.array(preds) <= bins[i + 1])
            else:
                mask = (np.array(preds) >= bins[i]) & (np.array(preds) < bins[i + 1])
            if mask.any():
                ece += mask.mean() * abs(np.array(preds)[mask].mean() - np.array(actuals)[mask].mean())
        return float(ece)

    return GoalCalibrationMetrics(
        mean_predicted_total=mean_pred,
        mean_actual_total=mean_act,
        over25_ece=_ece(over25_preds, over25_actuals),
        btts_ece=_ece(btts_preds, btts_actuals),
    )
