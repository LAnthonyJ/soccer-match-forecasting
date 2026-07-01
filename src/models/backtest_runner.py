"""
Historical backtest runner for versioned production predictions.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

import numpy as np

from src.data.provider_schema import MatchRecord
from src.models.backtest_report import evaluate_hda_release
from src.models.prediction_snapshot import PredictionSnapshot
from src.models.production_prediction import PredictionInputQuality
from src.models.score_evaluation import compute_logloss_exact_scores, compute_rps_vectorized
from src.models.total_goals_calibration import compute_goal_calibration_metrics


FINISHED_STATUSES = {"finished", "ft", "completed"}


@dataclass(frozen=True)
class HistoricalBacktestFixture:
    match: MatchRecord
    odds: tuple[float, float, float] | None = None
    quality: PredictionInputQuality | None = None
    context: Mapping[str, float] | None = None
    prediction_time: datetime | None = None
    raw_payload: dict | None = None  # raw Sportmonks fixture for team IDs etc.


@dataclass(frozen=True)
class HistoricalBacktestReport:
    model_name: str
    n_matches: int
    hda_logloss: float
    hda_brier: float
    ece: float
    market_logloss: float | None = None
    clv_mean: float | None = None
    rps_total_goals: float | None = None
    exact_score_logloss: float | None = None
    top3_score_hit_rate: float | None = None
    top5_score_hit_rate: float | None = None
    mean_predicted_goals: float | None = None
    mean_actual_goals: float | None = None
    over25_ece: float | None = None
    btts_ece: float | None = None

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "n_matches": self.n_matches,
            "hda_logloss": self.hda_logloss,
            "hda_brier": self.hda_brier,
            "ece": self.ece,
            "market_logloss": self.market_logloss,
            "clv_mean": self.clv_mean,
            "rps_total_goals": self.rps_total_goals,
            "exact_score_logloss": self.exact_score_logloss,
            "top3_score_hit_rate": self.top3_score_hit_rate,
            "top5_score_hit_rate": self.top5_score_hit_rate,
            "mean_predicted_goals": self.mean_predicted_goals,
            "mean_actual_goals": self.mean_actual_goals,
            "over25_ece": self.over25_ece,
            "btts_ece": self.btts_ece,
        }


@dataclass(frozen=True)
class HistoricalBacktestMatchResult:
    snapshot: PredictionSnapshot
    actual_home_goals: int
    actual_away_goals: int
    actual_outcome: str
    kickoff_utc: datetime
    prediction_time: datetime

    def to_dict(self, tier: str = "vip") -> dict:
        payload = self.snapshot.to_public_dict(tier=tier)
        payload["actual_score"] = {
            "home": self.actual_home_goals,
            "away": self.actual_away_goals,
        }
        payload["actual_outcome"] = self.actual_outcome
        payload["kickoff_utc"] = self.kickoff_utc.isoformat()
        payload["prediction_time"] = self.prediction_time.isoformat()
        return payload


@dataclass(frozen=True)
class HistoricalBacktestRun:
    model_version: str
    data_version: str
    generated_at: datetime
    results: list[HistoricalBacktestMatchResult]
    report: HistoricalBacktestReport

    def to_dict(self, tier: str = "vip") -> dict:
        return {
            "model_version": self.model_version,
            "data_version": self.data_version,
            "generated_at": self.generated_at.isoformat(),
            "report": self.report.to_dict(),
            "matches": [result.to_dict(tier=tier) for result in self.results],
        }


class HistoricalBacktestRunner:
    def __init__(self, engine, *, default_lead_hours: int = 1):
        self.engine = engine
        self.default_lead_hours = default_lead_hours

    def run(self, fixtures: list[HistoricalBacktestFixture]) -> HistoricalBacktestRun:
        generated_at = datetime.now(timezone.utc)
        results: list[HistoricalBacktestMatchResult] = []

        for fixture in fixtures:
            if not _is_finished_match(fixture.match):
                continue

            prediction_time = _resolve_prediction_time(
                fixture.prediction_time,
                fixture.match.kickoff_utc,
                self.default_lead_hours,
            )
            snapshot = self.engine.predict(
                match_id=fixture.match.match_id,
                home_team=fixture.match.home_team,
                away_team=fixture.match.away_team,
                odds=fixture.odds,
                quality=fixture.quality or PredictionInputQuality(),
                neutral=fixture.match.neutral,
                context=fixture.context,
                generated_at=prediction_time,
            )
            results.append(
                HistoricalBacktestMatchResult(
                    snapshot=snapshot,
                    actual_home_goals=int(fixture.match.home_score),
                    actual_away_goals=int(fixture.match.away_score),
                    actual_outcome=_actual_outcome(
                        int(fixture.match.home_score),
                        int(fixture.match.away_score),
                    ),
                    kickoff_utc=fixture.match.kickoff_utc,
                    prediction_time=prediction_time,
                )
            )

        report = _build_report(results)
        model_version = getattr(self.engine, "model_version", "unknown-model")
        data_version = getattr(self.engine, "data_version", "unknown-data")
        return HistoricalBacktestRun(
            model_version=model_version,
            data_version=data_version,
            generated_at=generated_at,
            results=results,
            report=report,
        )


def _is_finished_match(match: MatchRecord) -> bool:
    status = (match.status or "").strip().lower()
    return (
        status in FINISHED_STATUSES
        and match.home_score is not None
        and match.away_score is not None
    )


def _resolve_prediction_time(
    prediction_time: datetime | None,
    kickoff_utc: datetime,
    default_lead_hours: int,
) -> datetime:
    resolved = prediction_time or (kickoff_utc - timedelta(hours=default_lead_hours))
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    if resolved >= kickoff_utc:
        resolved = kickoff_utc - timedelta(minutes=1)
    return resolved


def _actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _top_score_hit_rate(
    results: list[HistoricalBacktestMatchResult],
    n: int,
) -> float:
    if not results:
        return 0.0
    hits = 0
    for result in results:
        top_scores = {(h, a) for h, a, _ in result.snapshot.top_scorelines[:n]}
        if (result.actual_home_goals, result.actual_away_goals) in top_scores:
            hits += 1
    return hits / len(results)


def _build_report(results: list[HistoricalBacktestMatchResult]) -> HistoricalBacktestReport:
    if not results:
        return HistoricalBacktestReport(
            model_name="historical-backtest",
            n_matches=0,
            hda_logloss=0.0,
            hda_brier=0.0,
            ece=0.0,
            market_logloss=None,
            clv_mean=None,
            rps_total_goals=0.0,
            exact_score_logloss=0.0,
            top3_score_hit_rate=0.0,
            top5_score_hit_rate=0.0,
            mean_predicted_goals=None,
            mean_actual_goals=None,
            over25_ece=None,
            btts_ece=None,
        )

    probabilities = [result.snapshot.final_probs for result in results]
    actual_outcomes = [result.actual_outcome for result in results]
    market_probabilities = None
    if all(result.snapshot.market_probs for result in results):
        market_probabilities = [result.snapshot.market_probs for result in results]

    base_report = evaluate_hda_release(
        model_name="historical-backtest",
        probabilities=probabilities,
        actual_outcomes=actual_outcomes,
        market_probabilities=market_probabilities,
    )

    score_matrices = np.array(
        [np.array(result.snapshot.score_heatmap, dtype=float) for result in results]
    )
    actual_scores = np.array(
        [[result.actual_home_goals, result.actual_away_goals] for result in results],
        dtype=int,
    )

    goal_metrics = compute_goal_calibration_metrics(results)

    return HistoricalBacktestReport(
        model_name=base_report.model_name,
        n_matches=base_report.n_matches,
        hda_logloss=base_report.hda_logloss,
        hda_brier=base_report.hda_brier,
        ece=base_report.ece,
        market_logloss=base_report.market_logloss,
        clv_mean=base_report.clv_mean,
        rps_total_goals=float(compute_rps_vectorized(score_matrices, actual_scores)),
        exact_score_logloss=float(compute_logloss_exact_scores(score_matrices, actual_scores)),
        top3_score_hit_rate=_top_score_hit_rate(results, 3),
        top5_score_hit_rate=_top_score_hit_rate(results, 5),
        mean_predicted_goals=goal_metrics.mean_predicted_total,
        mean_actual_goals=goal_metrics.mean_actual_total,
        over25_ece=goal_metrics.over25_ece,
        btts_ece=goal_metrics.btts_ece,
    )
