"""
Canonical provider schema for paid football data APIs and manual overrides.

Adapters should convert vendor payloads into these records before storage or
model input. This keeps provider-specific fields out of model code.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class TeamRecord:
    provider: str
    provider_team_id: str
    canonical_name: str
    country_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchRecord:
    provider: str
    provider_match_id: str
    match_id: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    tournament: str
    neutral: bool = True
    venue: str | None = None
    stage: str | None = None
    status: str = "scheduled"
    home_score: int | None = None
    away_score: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kickoff_utc"] = self.kickoff_utc.isoformat()
        return data


@dataclass(frozen=True)
class OddsSnapshot:
    provider: str
    match_id: str
    bookmaker: str
    captured_at_utc: datetime
    home_odds: float
    draw_odds: float
    away_odds: float
    label: str = "latest"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["captured_at_utc"] = self.captured_at_utc.isoformat()
        return data


@dataclass(frozen=True)
class LineupPlayerRecord:
    provider: str
    match_id: str
    team: str
    player_name: str
    is_starting: bool
    position: str | None = None
    player_value_eur: float | None = None
    recent_minutes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InjuryRecord:
    provider: str
    match_id: str
    team: str
    player_name: str
    status: str
    severity: str | None = None
    expected_return: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderAdapter:
    """
    Base adapter interface.

    Concrete paid-API adapters should implement these methods and return
    canonical records. Manual CSV imports can implement the same interface.
    """

    provider_name = "base"

    def parse_match(self, payload: Mapping[str, Any]) -> MatchRecord:
        raise NotImplementedError

    def parse_odds_snapshot(self, payload: Mapping[str, Any]) -> OddsSnapshot:
        raise NotImplementedError

    def parse_lineup_player(self, payload: Mapping[str, Any]) -> LineupPlayerRecord:
        raise NotImplementedError

    def parse_injury(self, payload: Mapping[str, Any]) -> InjuryRecord:
        raise NotImplementedError


# -- Step 6: Lineup / injury signal structures ----------------------------------


@dataclass(frozen=True)
class PlayerAvailabilityRecord:
    match_id: str
    team: str
    player_id: str | None
    player_name: str
    status: str  # available, doubtful, injured, suspended, unavailable
    reason: str | None = None
    severity: str | None = None  # low, medium, high
    updated_at: "datetime | None" = None


@dataclass(frozen=True)
class PlayerStrengthRecord:
    player_id: str | None
    player_name: str
    team: str
    position: str | None = None
    market_value_eur: float | None = None
    caps: int | None = None
    goals: int | None = None
    recent_minutes: int | None = None
    importance_score: float | None = None


@dataclass(frozen=True)
class MatchLineupSignal:
    match_id: str
    team: str
    has_confirmed_lineup: bool = False
    has_expected_lineup: bool = False
    starting_xi_strength: float | None = None
    unavailable_strength_loss: float | None = None
    doubtful_strength_loss: float | None = None
    recent_minutes_score: float | None = None
    goalkeeper_missing: bool = False
    key_attacker_missing: bool = False
    key_defender_missing: bool = False
    data_quality: str = "none"  # none, partial, expected, confirmed
    missing_players: list[str] = field(default_factory=list)
    doubtful_players: list[str] = field(default_factory=list)
