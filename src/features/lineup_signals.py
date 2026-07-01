"""
Pre-match lineup and injury signal builder.

Converts raw availability and player-strength data into MatchLineupSignal
objects suitable for feeding into the Dynamic Dixon-Coles lambda adjustment
layer and PredictionInputQuality scoring.

Key design rule: missing data → data_quality degrades gracefully.
No data → empty signal → model runs unchanged.
"""
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchLineupSignal,
    MatchRecord,
    PlayerAvailabilityRecord,
    PlayerStrengthRecord,
)

# -- Position classification ---------------------------------------------------

POSITION_GROUPS = {
    "GK": "goalkeeper",
    "DF": "defender",
    "MF": "midfielder",
    "FW": "attacker",
}

ATTACKING_POSITIONS = {"FW", "MF"}
DEFENSIVE_POSITIONS = {"DF", "GK"}


def _group(position: str | None) -> str:
    if not position:
        return "midfielder"
    return POSITION_GROUPS.get(position[:2].upper(), "midfielder")


# -- Player importance ---------------------------------------------------------

def compute_importance_score(
    player: PlayerStrengthRecord,
) -> float:
    """
    Compute a 0-1 importance score for a player.

    Priority: market_value > recent_minutes > caps+goals > default by position.
    """
    if player.importance_score is not None:
        return max(0.0, min(1.0, player.importance_score))

    # Market value: normalize around 50M EUR
    if player.market_value_eur is not None and player.market_value_eur > 0:
        return min(float(player.market_value_eur) / 50_000_000.0, 1.0)

    # Recent minutes: normalize around 2000 min (full season starter)
    if player.recent_minutes is not None and player.recent_minutes > 0:
        return min(float(player.recent_minutes) / 2000.0, 1.0)

    # Caps + goals proxy
    caps = player.caps or 0
    goals = player.goals or 0
    if caps > 0:
        score = min(caps / 100.0, 0.7) + min(goals / 30.0, 0.3)
        return min(score, 1.0)

    # Default by position
    group = _group(player.position)
    defaults = {"goalkeeper": 0.6, "defender": 0.5, "midfielder": 0.5, "attacker": 0.6}
    return defaults.get(group, 0.5)


# -- Signal builder ------------------------------------------------------------

def build_lineup_signals(
    match: MatchRecord,
    lineups: Mapping[str, Sequence[LineupPlayerRecord]] | None = None,
    injuries: Sequence[InjuryRecord] | None = None,
    player_strengths: Sequence[PlayerStrengthRecord] | None = None,
    as_of_date: datetime | None = None,
    lineup_type: str = "confirmed",  # "confirmed" | "expected" | "none"
) -> dict[str, MatchLineupSignal]:
    """
    Build lineup signals for both teams in a match.

    Returns {"home": MatchLineupSignal, "away": MatchLineupSignal}.
    Missing data → signal with data_quality="none" / "partial".

    lineup_type controls has_confirmed_lineup vs has_expected_lineup flags
    and their effect on confidence scoring.
    """
    lineups = lineups or {}
    injuries = list(injuries or [])
    strengths = list(player_strengths or [])

    home_signal = _build_team_signal(
        match=match,
        team=match.home_team,
        side="home",
        lineups=lineups,
        injuries=injuries,
        strengths=strengths,
        as_of_date=as_of_date,
        lineup_type=lineup_type,
    )
    away_signal = _build_team_signal(
        match=match,
        team=match.away_team,
        side="away",
        lineups=lineups,
        injuries=injuries,
        strengths=strengths,
        as_of_date=as_of_date,
        lineup_type=lineup_type,
    )
    return {"home": home_signal, "away": away_signal}


def _build_team_signal(
    match: MatchRecord,
    team: str,
    side: str,
    lineups: Mapping[str, Sequence[LineupPlayerRecord]],
    injuries: Sequence[InjuryRecord],
    strengths: Sequence[PlayerStrengthRecord],
    as_of_date: datetime | None,
    lineup_type: str = "confirmed",
) -> MatchLineupSignal:
    """Build signal for one team."""
    team_lineups = [p for p in lineups.get(team, [])]
    team_injuries = [i for i in injuries if i.team == team]
    strength_map = {s.player_name: s for s in strengths if s.team == team}

    has_confirmed = len(team_lineups) > 0 and lineup_type == "confirmed"
    has_expected = len(team_lineups) > 0 and lineup_type == "expected"

    # Build strength records from lineup players if strengths not provided
    if not strength_map and team_lineups:
        for p in team_lineups:
            strength_map[p.player_name] = PlayerStrengthRecord(
                player_id=None,
                player_name=p.player_name,
                team=team,
                position=p.position,
            )

    # Compute starting XI strength
    starting_xi_strength = None
    if team_lineups:
        scores = [compute_importance_score(strength_map.get(p.player_name, _empty_strength(p))) for p in team_lineups[:11]]
        if scores:
            starting_xi_strength = sum(scores) / len(scores)

    # Compute unavailable strength loss
    unavailable_strength_loss = None
    doubtful_strength_loss = None
    missing_players: list[str] = []
    doubtful_players: list[str] = []
    goalkeeper_missing = False
    key_attacker_missing = False
    key_defender_missing = False

    if team_injuries or team_lineups:
        # Expected total strength = starting XI average, or default 0.5 per player
        expected_strength = starting_xi_strength or 0.5
        expected_total = expected_strength * 11

        unavailable_loss = 0.0
        doubtful_loss = 0.0

        for inj in team_injuries:
            name = inj.player_name
            if name in [p.player_name for p in team_lineups]:
                continue  # already in lineup, not missing

            rec = strength_map.get(name, _empty_strength_for_name(name, team))
            imp = compute_importance_score(rec)
            group = _group(rec.position)

            if inj.status in ("injured", "suspended", "unavailable"):
                unavailable_loss += imp
                missing_players.append(name)
                if group == "goalkeeper":
                    goalkeeper_missing = True
                if group == "attacker" and imp > 0.4:
                    key_attacker_missing = True
                if group == "defender" and imp > 0.4:
                    key_defender_missing = True
            elif inj.status == "doubtful":
                doubtful_loss += imp * 0.5
                doubtful_players.append(name)

        unavailable_strength_loss = unavailable_loss / max(expected_total, 1.0) if unavailable_loss > 0 else 0.0
        doubtful_strength_loss = doubtful_loss / max(expected_total, 1.0) if doubtful_loss > 0 else 0.0

    # Recent minutes score
    recent_minutes_score = None
    if team_lineups:
        mins = [
            (strength_map.get(p.player_name) or _empty_strength(p)).recent_minutes
            for p in team_lineups[:11]
        ]
        valid_mins = [m for m in mins if m is not None]
        if valid_mins:
            recent_minutes_score = sum(valid_mins) / (len(valid_mins) * 90.0)
            recent_minutes_score = min(recent_minutes_score or 0.0, 1.0)

    # Data quality
    if lineup_type == "confirmed" and has_confirmed:
        data_quality = "confirmed"
    elif lineup_type == "expected" and has_expected:
        data_quality = "expected"
    elif team_injuries:
        data_quality = "partial"
    else:
        data_quality = "none"

    return MatchLineupSignal(
        match_id=match.match_id,
        team=team,
        has_confirmed_lineup=has_confirmed,
        has_expected_lineup=has_expected,
        starting_xi_strength=starting_xi_strength,
        unavailable_strength_loss=unavailable_strength_loss,
        doubtful_strength_loss=doubtful_strength_loss,
        recent_minutes_score=recent_minutes_score,
        goalkeeper_missing=goalkeeper_missing,
        key_attacker_missing=key_attacker_missing,
        key_defender_missing=key_defender_missing,
        data_quality=data_quality,
        missing_players=missing_players,
        doubtful_players=doubtful_players,
    )


def _empty_strength(p: LineupPlayerRecord) -> PlayerStrengthRecord:
    return PlayerStrengthRecord(
        player_id=None, player_name=p.player_name, team="", position=p.position,
    )


def _empty_strength_for_name(name: str, team: str) -> PlayerStrengthRecord:
    return PlayerStrengthRecord(
        player_id=None, player_name=name, team=team, position=None,
    )
