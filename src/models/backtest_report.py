"""
Backtest reporting helpers for model release comparisons.
"""
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class BacktestReport:
    model_name: str
    n_matches: int
    hda_logloss: float
    hda_brier: float
    ece: float
    market_logloss: float | None = None
    clv_mean: float | None = None

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "n_matches": self.n_matches,
            "hda_logloss": self.hda_logloss,
            "hda_brier": self.hda_brier,
            "ece": self.ece,
            "market_logloss": self.market_logloss,
            "clv_mean": self.clv_mean,
        }


def _outcome_index(outcome: str) -> int:
    normalized = outcome.lower()
    if normalized in ("h", "home"):
        return 0
    if normalized in ("d", "draw"):
        return 1
    if normalized in ("a", "away"):
        return 2
    raise ValueError(f"Unknown outcome: {outcome}")


def _matrix_from_prob_dicts(probabilities: list[Mapping[str, float]]) -> np.ndarray:
    rows = []
    for probs in probabilities:
        arr = np.array([float(probs[o]) for o in OUTCOMES], dtype=float)
        rows.append(arr / arr.sum())
    return np.vstack(rows)


def expected_calibration_error(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> float:
    confidence = probs.max(axis=1)
    predicted = probs.argmax(axis=1)
    correct = (predicted == outcomes).astype(float)
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (confidence >= bins[i]) & (confidence < bins[i + 1])
        if mask.any():
            ece += mask.mean() * abs(confidence[mask].mean() - correct[mask].mean())
    return float(ece)


def evaluate_hda_release(
    *,
    model_name: str,
    probabilities: list[Mapping[str, float]],
    actual_outcomes: list[str],
    market_probabilities: list[Mapping[str, float]] | None = None,
    closing_probabilities: list[Mapping[str, float]] | None = None,
) -> BacktestReport:
    probs = _matrix_from_prob_dicts(probabilities)
    actual = np.array([_outcome_index(o) for o in actual_outcomes], dtype=int)
    n = len(actual)
    eps = 1e-15

    logloss = float(-np.log(np.clip(probs[np.arange(n), actual], eps, 1.0)).mean())
    one_hot = np.zeros_like(probs)
    one_hot[np.arange(n), actual] = 1.0
    brier = float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))
    ece = expected_calibration_error(probs, actual)

    market_logloss = None
    if market_probabilities:
        market = _matrix_from_prob_dicts(market_probabilities)
        market_logloss = float(-np.log(np.clip(market[np.arange(n), actual], eps, 1.0)).mean())

    clv_mean = None
    if closing_probabilities:
        closing = _matrix_from_prob_dicts(closing_probabilities)
        selected = probs.argmax(axis=1)
        clv_mean = float(np.mean(probs[np.arange(n), selected] - closing[np.arange(n), selected]))

    return BacktestReport(
        model_name=model_name,
        n_matches=n,
        hda_logloss=logloss,
        hda_brier=brier,
        ece=ece,
        market_logloss=market_logloss,
        clv_mean=clv_mean,
    )


def reports_to_frame(reports: list[BacktestReport]) -> pd.DataFrame:
    return pd.DataFrame([r.to_dict() for r in reports]).sort_values("hda_logloss")
