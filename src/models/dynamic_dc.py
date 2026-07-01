"""
Dynamic Dixon-Coles service layer with time-weighted online updates.

Wraps the stable base Dixon-Coles model. Applies bounded, time-decayed
team adjustments from completed matches while preventing data leakage
(via as_of_date checks) and parameter oscillation (via daily caps and
exponential smoothing).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from src.data.provider_schema import MatchRecord
from src.models.score_models import ScoreMatrix


# -- Config ---------------------------------------------------------------------

@dataclass(frozen=True)
class DynamicDixonColesConfig:
    """Hyperparameters controlling update behaviour."""

    time_half_life_days: float = 365 * 2  # ~2 years
    max_daily_delta: float = 0.03         # per-team, per-field cap
    learning_rate: float = 0.015          # base step size
    smoothing: float = 0.85               # exponential smoothing toward base params

    tournament_weight: dict[str, float] = field(default_factory=lambda: {
        "World Cup": 5.0,
        "European Championship": 3.0,
        "Copa America": 3.0,
        "Africa Cup of Nations": 3.0,
        "AFC Asian Cup": 3.0,
        "CONCACAF Gold Cup": 3.0,
        "OFC Nations Cup": 3.0,
        "Olympic Games": 3.0,
        "UEFA Nations League": 2.5,
        "World Cup Qualification": 2.5,
        "WC Qualification Asia": 2.5,
        "WC Qualification Concacaf": 2.5,
        "WC Qualification Europe": 2.5,
        "WC Qualification Oceania": 2.5,
        "WC Qualification South America": 2.5,
        "CAF World Cup Qualifiers": 2.5,
        "Euro Qualification": 2.5,
        "Africa Cup of Nations Qualifications": 2.5,
        "Asian Cup Qualification": 2.5,
        "WC Qualification Intercontinental Playoffs": 1.5,
        "African Nations Championship": 2.0,
        "Friendly International": 0.5,
        "Friendly": 0.5,
    })


def _tournament_weight(tournament: str | None, config: DynamicDixonColesConfig) -> float:
    """Resolve tournament weight with fuzzy matching fallback."""
    if not tournament:
        return 1.0

    direct = config.tournament_weight.get(tournament)
    if direct is not None:
        return direct

    tournament_lower = tournament.lower()
    for key, weight in config.tournament_weight.items():
        if key.lower() in tournament_lower or tournament_lower in key.lower():
            return weight

    # Heuristic fallback based on keywords (ordered from most specific)
    if "world cup" in tournament_lower:
        if "qualif" in tournament_lower:
            return 2.5
        return 5.0
    if "qualif" in tournament_lower:
        return 2.5
    if "nations league" in tournament_lower:
        return 2.5
    if any(kw in tournament_lower for kw in (
        "european championship", "copa américa", "copa america",
        "africa cup of nations", "afcon", "asian cup", "gold cup",
        "ofc nations cup", "olympic",
    )):
        return 3.0
    if "friendly" in tournament_lower:
        return 0.5
    return 1.0


def _coerce_utc(dt: datetime) -> datetime:
    """Return a UTC-aware copy; naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _time_decay(match_date: datetime, as_of_date: datetime, half_life_days: float) -> float:
    """Exponential decay weight: 1.0 at as_of_date, 0.5 at half_life days ago."""
    match_date = _coerce_utc(match_date)
    as_of_date = _coerce_utc(as_of_date)
    age_days = (as_of_date - match_date).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return float(np.exp(-np.log(2) * age_days / half_life_days))


# -- Team adjustment state ------------------------------------------------------

@dataclass
class TeamAdjustment:
    attack: float = 0.0
    defense: float = 0.0


# -- Service --------------------------------------------------------------------

class DynamicDixonColesService:
    """
    Prediction wrapper around a base DixonColesModel instance.

    Maintains per-team additive log-lambda adjustments that are:
      - bounded per day (max_daily_delta)
      - exponentially smoothed toward zero (smoothing pulls toward base params)
      - time-weighted (recent matches matter more)
      - tournament-weighted (WC > friendly)

    All update methods enforce as_of_date to prevent forward-looking leakage.
    """

    def __init__(
        self,
        base_model: Any,
        config: DynamicDixonColesConfig | None = None,
    ):
        self.base_model = base_model
        self.config = config or DynamicDixonColesConfig()
        self.team_adjustments: dict[str, TeamAdjustment] = {}
        self._daily_applied: dict[str, float] = {}

    # -- Daily cap management ---------------------------------------------------

    def reset_daily_limits(self) -> None:
        self._daily_applied.clear()

    def _remaining_cap(self, team: str) -> float:
        used = self._daily_applied.get(team, 0.0)
        return max(self.config.max_daily_delta - used, 0.0)

    def _apply_limited_delta(self, team: str, field: str, desired_delta: float) -> float:
        """Apply a delta bounded by remaining daily cap. Returns actual delta applied."""
        remaining = self._remaining_cap(team)
        clipped = float(np.clip(desired_delta, -remaining, remaining))

        adj = self.team_adjustments.get(team)
        if adj is None:
            adj = TeamAdjustment()
            self.team_adjustments[team] = adj

        old_value = getattr(adj, field)
        # Exponential smoothing toward zero (base model is the anchor)
        new_value = self.config.smoothing * old_value + (1.0 - self.config.smoothing) * clipped
        setattr(adj, field, new_value)
        self._daily_applied[team] = self._daily_applied.get(team, 0.0) + abs(clipped)
        return clipped

    # -- Core update ------------------------------------------------------------

    def update_from_result(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        match_weight: float = 1.0,
    ) -> None:
        """
        Apply a bounded online update from one completed match.

        Positive surprise → lift scoring team attack, lower opponent defense.
        Negative surprise → opposite.
        """
        exp_home, exp_away = self.predict_lambdas(home_team, away_team, neutral=True)
        home_surprise = float(home_goals) - exp_home
        away_surprise = float(away_goals) - exp_away
        scale = self.config.learning_rate * float(match_weight)

        self._apply_limited_delta(home_team, "attack", scale * home_surprise)
        self._apply_limited_delta(away_team, "defense", -scale * home_surprise)
        self._apply_limited_delta(away_team, "attack", scale * away_surprise)
        self._apply_limited_delta(home_team, "defense", -scale * away_surprise)

    def update_from_match(
        self,
        match: MatchRecord,
        as_of_date: datetime,
    ) -> bool:
        """
        Apply update from one MatchRecord, with time-safety and tournament weighting.

        Returns True if the update was applied, False if skipped (future match or
        missing scores).
        """
        if match.home_score is None or match.away_score is None:
            return False

        kickoff = _coerce_utc(match.kickoff_utc)
        cutoff = _coerce_utc(as_of_date)
        if kickoff >= cutoff:
            return False

        tourney_weight = _tournament_weight(match.tournament, self.config)
        time_weight = _time_decay(kickoff, cutoff, self.config.time_half_life_days)
        combined_weight = tourney_weight * time_weight

        self.update_from_result(
            home_team=match.home_team,
            away_team=match.away_team,
            home_goals=match.home_score,
            away_goals=match.away_score,
            match_weight=combined_weight,
        )
        return True

    def update_from_matches(
        self,
        matches: Sequence[MatchRecord],
        as_of_date: datetime,
    ) -> int:
        """
        Apply all matches with kickoff < as_of_date, sorted by date.

        Returns number of matches applied.
        """
        cutoff = _coerce_utc(as_of_date)
        eligible = [
            m for m in matches
            if _coerce_utc(m.kickoff_utc) < cutoff
            and m.home_score is not None
            and m.away_score is not None
        ]
        eligible.sort(key=lambda m: _coerce_utc(m.kickoff_utc))

        applied = 0
        for m in eligible:
            if self.update_from_match(m, as_of_date):
                applied += 1
        return applied

    # -- Prediction -------------------------------------------------------------

    def predict_lambdas(
        self,
        home_team: str,
        away_team: str,
        neutral: bool = False,
        context: Mapping[str, float] | None = None,
    ) -> tuple[float, float]:
        """Return (lambda_home, lambda_away) with dynamic adjustments applied."""
        lambda_home, lambda_away = self.base_model.predict_lambdas(
            home_team, away_team, neutral=neutral,
        )

        home_adj = self.team_adjustments.get(home_team, TeamAdjustment())
        away_adj = self.team_adjustments.get(away_team, TeamAdjustment())
        ctx = context or {}

        log_home = (
            home_adj.attack
            + away_adj.defense
            + float(ctx.get("home_attack_delta", 0.0))
            + float(ctx.get("away_defense_delta", 0.0))
            + float(ctx.get("home_lineup_delta", 0.0))
        )
        log_away = (
            away_adj.attack
            + home_adj.defense
            + float(ctx.get("away_attack_delta", 0.0))
            + float(ctx.get("home_defense_delta", 0.0))
            + float(ctx.get("away_lineup_delta", 0.0))
        )

        return (
            max(lambda_home * np.exp(log_home), 0.02),
            max(lambda_away * np.exp(log_away), 0.02),
        )

    def predict_score_matrix(
        self,
        home_team: str,
        away_team: str,
        neutral: bool = False,
        context: Mapping[str, float] | None = None,
    ) -> ScoreMatrix:
        lambda_home, lambda_away = self.predict_lambdas(
            home_team, away_team, neutral, context,
        )
        rho = float(getattr(self.base_model, "rho", -0.05))
        return ScoreMatrix.from_dixon_coles(lambda_home, lambda_away, rho)
