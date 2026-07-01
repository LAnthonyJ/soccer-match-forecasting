"""
Sportmonks → canonical schema adapter.

Converts Sportmonks API v3 payloads into the project's canonical record types
(MatchRecord, OddsSnapshot, LineupPlayerRecord, InjuryRecord) so model code
never touches provider-specific fields.
"""
from datetime import datetime, timezone
from typing import Any, Mapping

from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchRecord,
    OddsSnapshot,
    ProviderAdapter,
)
from src.data.sportmonks_client import (
    batch_get_players,
    get_fixture,
    get_fixture_odds_1x2,
    get_player,
    get_squad,
    get_team,
    position_code,
)

# -- Outcome label -> canonical -------------------------------------------------

_STATE_MAP = {
    1: "scheduled",
    2: "scheduled",
    3: "live",
    4: "live",
    5: "finished",
    7: "cancelled",
    8: "postponed",
    9: "abandoned",
    11: "scheduled",
    12: "cancelled",
}


def _parse_utc(ts: str | None) -> datetime:
    if not ts:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    ts = ts.replace(" ", "T")
    if "T" not in ts:
        ts += "T00:00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# -- Canonical team name normalization ------------------------------------------

# Sportmonks → canonical name mapping.
# Canonical names match the training data (see cleaner.py TEAM_NAME_FIXES).
# Direction: Sportmonks name → canonical name used in model training.
TEAM_NAME_NORMALIZE: dict[str, str] = {
    "Congo DR": "DR Congo",
    "Côte d'Ivoire": "Ivory Coast",
    "Korea Republic": "South Korea",
    "Korea DPR": "North Korea",
    "IR Iran": "Iran",
    "United States": "USA",      # cleaner canonical: United States → USA
    "Czech Republic": "Czechia",
    "Türkiye": "Turkey",
}


def normalize_team_name(name: str) -> str:
    return TEAM_NAME_NORMALIZE.get(name, name)


# -- Adapter --------------------------------------------------------------------

class SportmonksAdapter(ProviderAdapter):
    """
    Converts raw Sportmonks JSON into canonical prediction records.

    Handles:
      - Fixture → MatchRecord
      - 1X2 odds → list[OddsSnapshot]
      - Squad player → LineupPlayerRecord
      - Player absence/injury → InjuryRecord
    """

    provider_name = "sportmonks"

    # -- Match ------------------------------------------------------------------

    def parse_match(self, payload: Mapping[str, Any]) -> MatchRecord:
        participants = payload.get("participants", [])
        home = _find_participant(participants, "home")
        away = _find_participant(participants, "away")

        scores = payload.get("scores", [])
        home_score, away_score = _extract_final_score(scores)

        raw_home = home.get("name", "?") if home else "?"
        raw_away = away.get("name", "?") if away else "?"
        neutral = _is_neutral_venue(payload)

        # Determine tournament name from league include or use placeholder
        league = payload.get("league", {})
        tournament = league.get("name", "International") if isinstance(league, dict) else "International"

        return MatchRecord(
            provider="sportmonks",
            provider_match_id=str(payload.get("id", "")),
            match_id=f"sportmonks-{payload.get('id', '')}",
            kickoff_utc=_parse_utc(payload.get("starting_at")),
            home_team=normalize_team_name(raw_home),
            away_team=normalize_team_name(raw_away),
            tournament=tournament,
            neutral=neutral,
            venue=payload.get("venue", {}).get("name") if isinstance(payload.get("venue"), dict) else None,
            stage=(league or {}).get("round") if isinstance(league, dict) else None,
            status=_STATE_MAP.get(payload.get("state_id", 1), "scheduled"),
            home_score=home_score,
            away_score=away_score,
        )

    # -- Odds -------------------------------------------------------------------

    def parse_odds_snapshot(self, payload: Mapping[str, Any]) -> OddsSnapshot:
        """
        Parse a single 1X2 odds triplet from one bookmaker.

        Expected payload keys: match_id, bookmaker_id, home, draw, away
        """
        return OddsSnapshot(
            provider="sportmonks",
            match_id=f"sportmonks-{payload['match_id']}",
            bookmaker=str(payload.get("bookmaker_id", "?")),
            captured_at_utc=datetime.now(timezone.utc),
            home_odds=float(payload["home"]),
            draw_odds=float(payload["draw"]),
            away_odds=float(payload["away"]),
            label="latest",
        )

    def fetch_odds_snapshots(self, fixture_id: int) -> list[OddsSnapshot]:
        """Fetch all 1X2 odds for a fixture, one snapshot per bookmaker."""
        by_bookie = get_fixture_odds_1x2(fixture_id)
        snapshots = []
        for bookmaker_id, odds in by_bookie.items():
            snapshots.append(
                OddsSnapshot(
                    provider="sportmonks",
                    match_id=f"sportmonks-{fixture_id}",
                    bookmaker=bookmaker_id,
                    captured_at_utc=datetime.now(timezone.utc),
                    home_odds=odds["home"],
                    draw_odds=odds["draw"],
                    away_odds=odds["away"],
                    label="latest",
                )
            )
        return snapshots

    def average_odds(self, fixture_id: int) -> tuple[float, float, float] | None:
        """
        Get consensus 1X2 odds, averaging implied probabilities across bookmakers.

        Direct averaging of decimal odds would distort market consensus because
        big underdog odds (e.g. 15.0) dominate the arithmetic mean. Instead:
          1. Convert each bookmaker's decimal odds to raw implied probabilities
          2. Average the probabilities (equal weight per bookmaker)
          3. Convert back to decimal odds

        The resulting tuple can still be fed to normalize_three_way_odds() for
        vig removal and final market probabilities.
        """
        by_bookie = get_fixture_odds_1x2(fixture_id)
        if not by_bookie:
            return None

        # Accumulate probabilities, then average
        home_probs = []
        draw_probs = []
        away_probs = []
        for odds in by_bookie.values():
            home_probs.append(1.0 / odds["home"])
            draw_probs.append(1.0 / odds["draw"])
            away_probs.append(1.0 / odds["away"])

        n = len(home_probs)
        avg_home_prob = sum(home_probs) / n
        avg_draw_prob = sum(draw_probs) / n
        avg_away_prob = sum(away_probs) / n

        # Convert back to decimal odds
        return (
            1.0 / avg_home_prob,
            1.0 / avg_draw_prob,
            1.0 / avg_away_prob,
        )

    # -- Lineup / Squad ---------------------------------------------------------

    def parse_lineup_player(self, payload: Mapping[str, Any]) -> LineupPlayerRecord:
        """
        Convert a squad entry (+ optional player details) into a canonical record.

        Expected payload keys from squad: player_id, team_id, position_id, jersey_number, captain
        Additional from player detail: name, common_name, height, weight
        """
        pid = payload.get("player_id", 0)
        player = payload.get("_player", {})
        return LineupPlayerRecord(
            provider="sportmonks",
            match_id="",  # filled by caller
            team=payload.get("_team_name", "?"),
            player_name=player.get("display_name") or player.get("name") or str(pid),
            is_starting=payload.get("is_starting", True),
            position=position_code(payload.get("position_id", 0)),
            player_value_eur=None,
            recent_minutes=None,
        )

    def fetch_squad_players(self, team_id: int, team_name: str = "") -> list[LineupPlayerRecord]:
        """
        Fetch current squad for a national team as lineup records.

        These are not match-specific starters, but the full available squad.
        For actual starting XI, use fetch_expected_lineups().
        """
        squad = get_squad(team_id)
        player_ids = [s["player_id"] for s in squad if s.get("player_id")]
        player_map = batch_get_players(player_ids)

        records = []
        for entry in squad:
            pid = entry.get("player_id", 0)
            player = player_map.get(pid, {})
            records.append(
                LineupPlayerRecord(
                    provider="sportmonks",
                    match_id="",
                    team=team_name,
                    player_name=player.get("display_name") or player.get("name", f"Player-{pid}"),
                    is_starting=True,
                    position=position_code(entry.get("position_id", 0)),
                    player_value_eur=None,
                    recent_minutes=None,
                )
            )
        return records

    def fetch_expected_lineup_players(
        self, fixture_id: int, home_team: str, away_team: str
    ) -> list[LineupPlayerRecord]:
        """Get expected starting XI for both teams before a match."""
        from src.data.sportmonks_client import get_expected_lineups

        lineups = get_expected_lineups(fixture_id)
        records = []
        for lineup in lineups:
            team_name = lineup.get("team", {}).get("name", "?")
            is_home = team_name == home_team
            actual_team = home_team if is_home else away_team
            for player_entry in lineup.get("players", []):
                p = player_entry.get("player", {})
                records.append(
                    LineupPlayerRecord(
                        provider="sportmonks",
                        match_id=f"sportmonks-{fixture_id}",
                        team=actual_team,
                        player_name=p.get("display_name") or p.get("name", "?"),
                        is_starting=player_entry.get("is_starting", True),
                        position=position_code(p.get("position_id", 0)),
                        player_value_eur=None,
                        recent_minutes=None,
                    )
                )
        return records

    # -- Sportmonks API payload parsers -----------------------------------------

    def parse_lineup_from_sportmonks(
        self, payload: Mapping[str, Any], match_id: str, team_name: str,
    ) -> LineupPlayerRecord:
        """Convert a Sportmonks lineup player entry into a canonical record."""
        player = payload.get("player", payload)
        pid = player.get("player_id") or player.get("id")
        name = player.get("display_name") or player.get("name") or player.get("common_name", f"Player-{pid}")
        pos_id = player.get("position_id") or payload.get("position_id", 0)
        is_starting = bool(payload.get("is_starting", True))
        return LineupPlayerRecord(
            provider="sportmonks",
            match_id=match_id,
            team=normalize_team_name(team_name),
            player_name=name,
            is_starting=is_starting,
            position=position_code(pos_id),
            player_value_eur=None,
            recent_minutes=None,
        )

    def parse_strength_from_sportmonks(
        self, squad_entry: Mapping[str, Any], team_name: str,
    ) -> "PlayerStrengthRecord":
        """Convert a Sportmonks squad entry into a PlayerStrengthRecord."""
        from src.data.provider_schema import PlayerStrengthRecord

        pid = squad_entry.get("player_id", 0)
        pos_id = squad_entry.get("position_id", 0)
        # Player detail may be included via _player key
        player = squad_entry.get("_player", {})
        name = (player.get("display_name") or player.get("name")
                or squad_entry.get("player_name", f"Player-{pid}"))
        return PlayerStrengthRecord(
            player_id=str(pid) if pid else None,
            player_name=name,
            team=normalize_team_name(team_name),
            position=position_code(pos_id),
            market_value_eur=None,
            caps=None,
            goals=None,
            recent_minutes=None,
            importance_score=None,
        )

    # -- API fetch wrappers -----------------------------------------------------

    def fetch_fixture_lineups(self, fixture_id: int, home_team: str, away_team: str) -> list[LineupPlayerRecord]:
        """Fetch confirmed lineups from Sportmonks for a fixture."""
        from src.data.sportmonks_client import get_fixture_lineups

        records = []
        for lineup in get_fixture_lineups(fixture_id):
            team_name = lineup.get("team", {}).get("name", "")
            if isinstance(team_name, dict):
                team_name = team_name.get("name", "?")
            actual_team = home_team if team_name == home_team else (away_team if team_name == away_team else team_name)
            for player_entry in lineup.get("players", []):
                records.append(
                    self.parse_lineup_from_sportmonks(player_entry, f"sportmonks-{fixture_id}", actual_team)
                )
        return records

    def fetch_squad_strengths(self, team_id: int, team_name: str) -> list:
        """Fetch squad and convert to PlayerStrengthRecords."""
        from src.data.provider_schema import PlayerStrengthRecord
        from src.data.sportmonks_client import get_squad

        records: list[PlayerStrengthRecord] = []
        try:
            squad = get_squad(team_id)
        except Exception:
            return records
        for entry in squad:
            records.append(self.parse_strength_from_sportmonks(entry, team_name))
        return records

    # -- Injury -----------------------------------------------------------------

    def parse_injury(self, payload: Mapping[str, Any]) -> InjuryRecord:
        """
        Sportmonks doesn't have a dedicated injury endpoint in the base plan.
        This parses player unavailability from squad end-dates as a proxy.

        If a squad entry has 'end' date before a match, the player was unavailable.
        """
        pid = payload.get("player_id", 0)
        player_name = payload.get("_player_name", str(pid))
        return InjuryRecord(
            provider="sportmonks",
            match_id=payload.get("_match_id", ""),
            team=payload.get("_team_name", "?"),
            player_name=player_name,
            status=payload.get("status", "unavailable"),
            severity=payload.get("severity"),
            expected_return=payload.get("expected_return"),
        )


def _is_neutral_venue(fixture: Mapping[str, Any]) -> bool:
    """
    Determine whether a national-team fixture is played at a neutral venue.

    Heuristic based on tournament type:
      - Finals tournaments → always neutral
      - Qualifiers → home/away legs, NOT neutral
      - Nations League → home/away (group stage), NOT neutral
      - Friendlies → usually neutral unless venue matches home country

    League_id is the primary signal (does not depend on sub_type being populated).
    """
    league = fixture.get("league", {})
    if not isinstance(league, dict):
        league = {}
    league_id = fixture.get("league_id") or league.get("id")

    # ── Explicit league-level classification (primary signal) ──────────────

    NEUTRAL_LEAGUES = {
        732,   # World Cup
        1326,  # European Championship
        1114,  # Copa America
        1117,  # Africa Cup of Nations
        1105,  # AFC Asian Cup
        1112,  # CONCACAF Gold Cup
        1124,  # OFC Nations Cup
        1125,  # Olympic Games
        1104,  # ASEAN Championship
        1103,  # African Nations Championship (finals)
        1127,  # SAFF Championship
        1434,  # Atlantic Cup
        1379,  # Cosafa Cup
        1452,  # FIFA Intercontinental Cup
        1546,  # Central American & Caribbean Games
        1555,  # Asian Games
    }

    # Tournaments played as home/away legs (qualifiers, Nations League group)
    HOME_AWAY_LEAGUES = {
        711,   # CAF World Cup Qualifiers
        714,   # WC Qualification Asia
        717,   # WC Qualification Concacaf
        720,   # WC Qualification Europe
        723,   # WC Qualification Oceania
        726,   # WC Qualification South America
        729,   # WC Qualification Intercontinental Playoffs
        1106,  # Asian Cup Qualification
        1118,  # Africa Cup of Nations Qualifications
        1325,  # Euro Qualification
        1538,  # UEFA Nations League (group stage = home/away)
    }

    if league_id in NEUTRAL_LEAGUES:
        return True
    if league_id in HOME_AWAY_LEAGUES:
        return False

    # ── Friendlies (league 1082): venue-country check before generic sub_type ─

    if league_id == 1082:
        venue = fixture.get("venue", {})
        if isinstance(venue, dict):
            venue_country_id = venue.get("country_id")
            participants = fixture.get("participants", [])
            home_team = _find_participant(participants, "home")
            if home_team and venue_country_id:
                home_country_id = home_team.get("country_id")
                if home_country_id and venue_country_id == home_country_id:
                    return False  # true home friendly
        return True  # neutral friendly

    # ── Fallback: sub_type heuristic (when league_id is unknown) ───────────

    sub_type = league.get("sub_type", "")
    if sub_type in ("international", "play-offs"):
        return False
    if sub_type == "cup_international":
        return True
    # "friendly" without league_id=1082 → neutral by default
    if sub_type == "friendly":
        return True

    # Unknown league + empty sub_type: conservative default = home/away.
    return False


# -- Helpers --------------------------------------------------------------------

def _find_participant(participants: list[dict], location: str) -> dict | None:
    for p in participants:
        meta = p.get("meta", {})
        if isinstance(meta, dict) and meta.get("location") == location:
            return p
    # Fallback: first = home, second = away
    if participants:
        return participants[0] if location == "home" else (participants[1] if len(participants) > 1 else None)
    return None


def _extract_final_score(scores: list[dict]) -> tuple[int | None, int | None]:
    """Extract CURRENT (final) score from scores array."""
    for s in scores:
        if s.get("description") == "CURRENT" and s.get("type_id") == 1525:
            sc = s.get("score", {})
            participant = sc.get("participant", "")
            goals = sc.get("goals")
            if participant == "home":
                home_g = goals
                away_g = next(
                    (x.get("score", {}).get("goals")
                     for x in scores
                     if x.get("description") == "CURRENT"
                     and x.get("type_id") == 1525
                     and x.get("score", {}).get("participant") == "away"),
                    None,
                )
                return home_g, away_g
    # Fallback: take first CURRENT pair
    current = [s for s in scores if s.get("description") == "CURRENT"]
    home_g = None
    away_g = None
    for s in current:
        sc = s.get("score", {})
        if sc.get("participant") == "home":
            home_g = sc.get("goals")
        elif sc.get("participant") == "away":
            away_g = sc.get("goals")
    return home_g, away_g
