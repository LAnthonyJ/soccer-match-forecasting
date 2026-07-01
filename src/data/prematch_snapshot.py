"""
Pre-match data snapshot 鈥?reproducible pre-game context for every prediction.

Captures lineups, injuries, player strengths, and odds at a point in time
so that every prediction can be replayed and audited later.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.config.runtime import load_runtime_config
from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchRecord,
    OddsSnapshot,
    PlayerStrengthRecord,
)
from src.data.sportmonks_adapter import normalize_team_name

SNAPSHOT_DIR = load_runtime_config().data_dir / "prematch_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PrematchDataSnapshot:
    match_id: str
    home_team: str
    away_team: str
    data_source: str
    fetched_at: datetime
    lineups: list[LineupPlayerRecord] = field(default_factory=list)
    injuries: list[InjuryRecord] = field(default_factory=list)
    player_strengths: list[PlayerStrengthRecord] = field(default_factory=list)
    odds_snapshots: list[OddsSnapshot] = field(default_factory=list)
    odds_snapshot_time: datetime | None = None
    data_quality: str = "none"  # none / partial / expected / confirmed
    lineup_type: str = "none"  # none / expected / confirmed
    raw_refs: dict[str, str] = field(default_factory=dict)

    @property
    def snapshot_id(self) -> str:
        ts = self.fetched_at.strftime("%Y%m%dT%H%M%S")
        return f"{self.match_id}_{ts}"

    def has_lineups(self) -> bool:
        return len(self.lineups) > 0

    def has_injuries(self) -> bool:
        return len(self.injuries) > 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fetched_at"] = self.fetched_at.isoformat()
        if self.odds_snapshot_time:
            data["odds_snapshot_time"] = self.odds_snapshot_time.isoformat()
        # Convert nested records
        data["lineups"] = [asdict(r) for r in self.lineups]
        data["injuries"] = [asdict(r) for r in self.injuries]
        data["player_strengths"] = [asdict(r) for r in self.player_strengths]
        data["odds_snapshots"] = [asdict(r) for r in self.odds_snapshots]
        data["snapshot_id"] = self.snapshot_id
        return data

    def save(self, path: str | Path | None = None) -> Path:
        if path is None:
            ts = self.fetched_at.strftime("%Y%m%dT%H%M%S")
            path = SNAPSHOT_DIR / self.match_id / f"{ts}.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "PrematchDataSnapshot":
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("snapshot_id", None)
        raw["fetched_at"] = datetime.fromisoformat(raw["fetched_at"])
        if raw.get("odds_snapshot_time"):
            raw["odds_snapshot_time"] = datetime.fromisoformat(raw["odds_snapshot_time"])
        raw["lineups"] = [LineupPlayerRecord(**r) for r in raw.get("lineups", [])]
        raw["injuries"] = [InjuryRecord(**r) for r in raw.get("injuries", [])]
        raw["player_strengths"] = [PlayerStrengthRecord(**r) for r in raw.get("player_strengths", [])]
        raw["odds_snapshots"] = [OddsSnapshot(**r) for r in raw.get("odds_snapshots", [])]
        return cls(**raw)

    @classmethod
    def empty(cls, match: MatchRecord, data_source: str = "none") -> "PrematchDataSnapshot":
        return cls(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            data_source=data_source,
            fetched_at=datetime.now(timezone.utc),
            data_quality="none",
            lineup_type="none",
        )


def build_prematch_snapshot(
    match: MatchRecord,
    *,
    data_source: str = "sportmonks",
    lineups: Sequence[LineupPlayerRecord] | None = None,
    injuries: Sequence[InjuryRecord] | None = None,
    player_strengths: Sequence[PlayerStrengthRecord] | None = None,
    odds: Sequence[OddsSnapshot] | None = None,
    manual_lineups_path: str | None = None,
    manual_injuries_path: str | None = None,
    manual_strengths_path: str | None = None,
    fetched_at: datetime | None = None,
) -> PrematchDataSnapshot:
    """
    Build a prematch snapshot from API data, manual fallbacks, or both.

    Priority: explicit params > manual JSON files > empty.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)

    # Collect lineups (filtered by match_id to avoid cross-match pollution)
    final_lineups = [r for r in (lineups or []) if _matches(r, match)]
    if not final_lineups and manual_lineups_path:
        final_lineups = _load_json_records(manual_lineups_path, LineupPlayerRecord, match)

    # Collect injuries
    final_injuries = [r for r in (injuries or []) if _matches(r, match)]
    if not final_injuries and manual_injuries_path:
        final_injuries = _load_json_records(manual_injuries_path, InjuryRecord, match)

    # Collect strengths (per-team, not per-match 鈥?filter by team name)
    final_strengths = [r for r in (player_strengths or [])
                       if _team_belongs(r, match.home_team, match.away_team)]
    if not final_strengths and manual_strengths_path:
        final_strengths = _load_strength_records(manual_strengths_path, match)

    # Determine data quality and lineup type
    has_confirmed_lineup = any(
        p.is_starting for p in final_lineups
    ) and final_lineups
    if has_confirmed_lineup:
        quality = "confirmed"
        lineup_type = "confirmed"
    elif final_lineups:
        quality = "expected"
        lineup_type = "expected"
    elif final_injuries:
        quality = "partial"
        lineup_type = "none"
    else:
        quality = "none"
        lineup_type = "none"

    return PrematchDataSnapshot(
        match_id=match.match_id,
        home_team=match.home_team,
        away_team=match.away_team,
        data_source=data_source,
        fetched_at=fetched_at,
        lineups=final_lineups,
        injuries=final_injuries,
        player_strengths=final_strengths,
        odds_snapshots=list(odds or []),
        odds_snapshot_time=fetched_at,
        data_quality=quality,
        lineup_type=lineup_type,
        raw_refs={
            "manual_lineups": manual_lineups_path or "",
            "manual_injuries": manual_injuries_path or "",
            "manual_strengths": manual_strengths_path or "",
        },
    )


def _matches(record, match: MatchRecord) -> bool:
    """Check if a record belongs to the given match.

    Records with empty match_id are excluded (must be explicit)."""
    rid = getattr(record, "match_id", "")
    return bool(rid) and rid == match.match_id


def _team_belongs(record, home_team: str, away_team: str) -> bool:
    """Check if a team-scoped record belongs to either side of the match."""
    team = getattr(record, "team", "")
    if not team:
        return False
    team_norm = normalize_team_name(team)
    return team_norm in (normalize_team_name(home_team), normalize_team_name(away_team))


def _load_strength_records(path: str, match: MatchRecord) -> list:
    """Load PlayerStrengthRecords, filtered by team (not match_id)."""
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    records = [PlayerStrengthRecord(**item) for item in raw]
    return [r for r in records if _team_belongs(r, match.home_team, match.away_team)]


def _load_json_records(path: str, cls: type, match: MatchRecord) -> list:
    """Load a list of dataclass records from a JSON file, filtered by match_id."""
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    records = [cls(**item) for item in raw]
    return [r for r in records if _matches(r, match)]


# -- Sportmonks auto-fetch -----------------------------------------------------


def build_sportmonks_prematch_snapshot(
    match: MatchRecord,
    fixture_id: int | None = None,
    *,
    home_team_id: int | None = None,
    away_team_id: int | None = None,
    manual_lineups_path: str | None = None,
    manual_injuries_path: str | None = None,
    manual_strengths_path: str | None = None,
    fetched_at: datetime | None = None,
) -> PrematchDataSnapshot:
    """
    Build a prematch snapshot with Sportmonks API as the primary data source.

    Priority chain:
      1. Sportmonks API (lineups, squad -> strengths)
      2. Manual JSON files (if API returns empty)
      3. Empty snapshot (graceful degradation)
    """
    from src.data.sportmonks_adapter import SportmonksAdapter

    adapter = SportmonksAdapter()
    api_lineups: list[LineupPlayerRecord] = []
    api_strengths: list[PlayerStrengthRecord] = []

    if fixture_id:
        # Confirmed lineups (completed matches)
        try:
            api_lineups = adapter.fetch_fixture_lineups(
                fixture_id, match.home_team, match.away_team,
            )
        except Exception:
            pass

        # Expected lineups fallback (scheduled/pre-match fixtures)
        if not api_lineups:
            try:
                api_lineups = adapter.fetch_expected_lineup_players(
                    fixture_id, match.home_team, match.away_team,
                )
            except Exception:
                pass

    if home_team_id:
        try:
            api_strengths.extend(adapter.fetch_squad_strengths(home_team_id, match.home_team))
        except Exception:
            pass

    if away_team_id:
        try:
            api_strengths.extend(adapter.fetch_squad_strengths(away_team_id, match.away_team))
        except Exception:
            pass

    return build_prematch_snapshot(
        match,
        data_source="sportmonks-api",
        lineups=api_lineups if api_lineups else None,
        player_strengths=api_strengths if api_strengths else None,
        manual_lineups_path=manual_lineups_path,
        manual_injuries_path=manual_injuries_path,
        manual_strengths_path=manual_strengths_path,
        fetched_at=fetched_at,
    )

