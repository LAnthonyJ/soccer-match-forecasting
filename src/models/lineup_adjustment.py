"""
Lineup / injury → lambda adjustment layer.

Converts MatchLineupSignal into home/away attack and defense deltas
that feed into DynamicDixonColesService.predict_lambdas() via the
`context` dict.

Design: rule-based with configurable caps.  No ML training needed —
the adjustment bounds are conservative so small samples cannot
destabilize the model.
"""
from dataclasses import dataclass
from typing import Mapping

from src.data.provider_schema import MatchLineupSignal


# -- Config ---------------------------------------------------------------------

@dataclass(frozen=True)
class LineupAdjustmentConfig:
    max_attack_delta: float = 0.12       # per-team attack cap
    max_defense_delta: float = 0.10      # per-team defense cap
    max_total_delta: float = 0.18        # combined home+away cap
    doubtful_weight: float = 0.5
    goalkeeper_defense_penalty: float = 0.04
    key_attacker_attack_penalty: float = 0.05
    key_defender_defense_penalty: float = 0.04
    unavailable_attack_coeff: float = 0.60
    unavailable_defense_coeff: float = 0.50
    doubtful_attack_coeff: float = 0.30
    doubtful_defense_coeff: float = 0.25


# -- Adjustment result ----------------------------------------------------------

@dataclass(frozen=True)
class LineupLambdaAdjustment:
    home_attack_delta: float = 0.0
    home_defense_delta: float = 0.0
    away_attack_delta: float = 0.0
    away_defense_delta: float = 0.0
    confidence_bonus: float = 0.0
    explanation: list[str] | None = None

    def to_context(self) -> dict[str, float]:
        """Convert to the context dict expected by DynamicDixonColesService."""
        return {
            "home_lineup_delta": self.home_attack_delta + self.away_defense_delta,
            "away_lineup_delta": self.away_attack_delta + self.home_defense_delta,
        }

    def has_signal(self) -> bool:
        return any([
            self.home_attack_delta != 0.0,
            self.home_defense_delta != 0.0,
            self.away_attack_delta != 0.0,
            self.away_defense_delta != 0.0,
        ])


# -- Builder --------------------------------------------------------------------

def build_lineup_lambda_adjustment(
    home_signal: MatchLineupSignal,
    away_signal: MatchLineupSignal,
    config: LineupAdjustmentConfig | None = None,
) -> LineupLambdaAdjustment:
    """
    Convert lineup signals from both teams into lambda deltas.

    Core formula:
      attack_delta = -(unavailable_coeff * unavailable_loss
                       + doubtful_coeff * doubtful_loss)
      defense_delta = +(unavailable_coeff * unavailable_def_loss
                        + doubtful_coeff * doubtful_def_loss)

    Direction:
      - Attacking players missing → own attack delta negative
      - Defensive players missing → own defense delta positive
        (makes opponent's attack relatively stronger)
      - GK missing → additional defense penalty
    """
    cfg = config or LineupAdjustmentConfig()
    explanation: list[str] = []

    def _team_deltas(signal: MatchLineupSignal, side: str) -> tuple[float, float, list[str]]:
        notes: list[str] = []
        if signal.data_quality == "none":
            return 0.0, 0.0, notes

        # Separate loss into attacking and defensive contributions
        u_loss = signal.unavailable_strength_loss or 0.0
        d_loss = signal.doubtful_strength_loss or 0.0

        # Approximate split: assume 50% attacking, 50% defensive contribution
        # from the total strength loss, adjusted by key player signals
        u_att = u_loss * 0.5
        u_def = u_loss * 0.5
        d_att = d_loss * 0.5
        d_def = d_loss * 0.5

        attack_delta = -(
            cfg.unavailable_attack_coeff * u_att
            + cfg.doubtful_attack_coeff * d_att
        )
        defense_delta = +(
            cfg.unavailable_defense_coeff * u_def
            + cfg.doubtful_defense_coeff * d_def
        )

        if signal.key_attacker_missing:
            attack_delta -= cfg.key_attacker_attack_penalty
            notes.append(f"{side} key attacker missing")
        if signal.key_defender_missing:
            defense_delta += cfg.key_defender_defense_penalty
            notes.append(f"{side} key defender missing")
        if signal.goalkeeper_missing:
            defense_delta += cfg.goalkeeper_defense_penalty
            notes.append(f"{side} goalkeeper missing")

        # Clip to per-team bounds
        attack_delta = float(np.clip(attack_delta, -cfg.max_attack_delta, cfg.max_attack_delta))
        defense_delta = float(np.clip(defense_delta, -cfg.max_defense_delta, cfg.max_defense_delta))

        if signal.missing_players:
            notes.append(f"{side} missing: {', '.join(signal.missing_players[:5])}")
        if signal.doubtful_players:
            notes.append(f"{side} doubtful: {', '.join(signal.doubtful_players[:3])}")

        return attack_delta, defense_delta, notes

    import numpy as np

    h_atk, h_def, h_notes = _team_deltas(home_signal, "home")
    a_atk, a_def, a_notes = _team_deltas(away_signal, "away")
    explanation = h_notes + a_notes

    # Clip to combined cap
    total = abs(h_atk) + abs(h_def) + abs(a_atk) + abs(a_def)
    if total > cfg.max_total_delta and total > 0:
        scale = cfg.max_total_delta / total
        h_atk *= scale
        h_def *= scale
        a_atk *= scale
        a_def *= scale
        explanation.append(f"total delta capped (scale={scale:.2f})")

    # Confidence bonus: how much to boost model-vs-market weight
    confidence_bonus = 0.0
    if home_signal.data_quality in ("confirmed", "expected"):
        confidence_bonus += 0.05
    if away_signal.data_quality in ("confirmed", "expected"):
        confidence_bonus += 0.05

    return LineupLambdaAdjustment(
        home_attack_delta=h_atk,
        home_defense_delta=h_def,
        away_attack_delta=a_atk,
        away_defense_delta=a_def,
        confidence_bonus=confidence_bonus,
        explanation=explanation if explanation else None,
    )
