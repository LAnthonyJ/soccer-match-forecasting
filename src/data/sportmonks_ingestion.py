"""
Sportmonks data ingestion pipeline.

Fetches fixtures, odds, squads, lineups, and match facts for international
tournaments and converts them into canonical records for the prediction stack.

Typical usage:
    pipeline = SportmonksIngestion()
    matches, odds, lineups = pipeline.ingest_tournament(league_id=732)
    # or
    ready = pipeline.ingest_prediction_ready(fixture_id=19609197)
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchRecord,
    OddsSnapshot,
)
from src.data.sportmonks_adapter import SportmonksAdapter, TEAM_NAME_NORMALIZE
from src.data.sportmonks_client import (
    get_current_season,
    get_fixture,
    get_fixtures_between,
    get_national_teams,
    get_squad,
    get_team,
    batch_get_players,
    position_code,
)

# Key international tournament league IDs from Sportmonks
INTERNATIONAL_LEAGUES = {
    732: "World Cup",
    714: "WC Qualification Asia",
    717: "WC Qualification Concacaf",
    720: "WC Qualification Europe",
    723: "WC Qualification Oceania",
    726: "WC Qualification South America",
    711: "CAF World Cup Qualifiers",
    729: "WC Qualification Intercontinental Playoffs",
    1105: "AFC Asian Cup",
    1114: "Copa America",
    1117: "Africa Cup of Nations",
    1118: "Africa Cup of Nations Qualifications",
    1325: "Euro Qualification",
    1326: "European Championship",
    1538: "UEFA Nations League",
    1082: "Friendly International",
    1103: "African Nations Championship",
    1112: "CONCACAF Gold Cup",
    1124: "OFC Nations Cup",
    1125: "Olympic Games",
}

OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "processed"


@dataclass
class IngestionResult:
    """Container for a batch ingestion run."""

    matches: list[MatchRecord] = field(default_factory=list)
    odds_snapshots: list[OddsSnapshot] = field(default_factory=list)
    lineup_players: list[LineupPlayerRecord] = field(default_factory=list)
    injuries: list[InjuryRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def n_errors(self) -> int:
        return len(self.errors)


class SportmonksIngestion:
    """
    Orchestrates data ingestion from Sportmonks into canonical records.

    Cache is built into the client layer (JSON files in data/processed/sportmonks_cache/),
    so repeated calls within the cache TTL are free.
    """

    def __init__(self):
        self.adapter = SportmonksAdapter()
        self._team_cache: dict[int, dict] = {}
        self._player_cache: dict[int, dict] = {}

    # -- High-level ingestion ---------------------------------------------------

    def ingest_tournament(
        self,
        league_id: int,
        season_year: int | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> IngestionResult:
        """
        Pull all fixtures + odds for an international tournament.

        If dates are not provided, tries to use the current season range.
        """
        result = IngestionResult()

        if from_date is None or to_date is None:
            season = get_current_season(league_id)
            if season:
                from_date = datetime.fromisoformat(season.get("starting_at", "2020-01-01"))
                to_date = datetime.fromisoformat(season.get("ending_at", "2030-12-31"))
            else:
                from_date = datetime(2020, 1, 1)
                to_date = datetime(2030, 12, 31)

        fixtures = get_fixtures_between(
            from_date, to_date,
            league_ids=[league_id],
            includes="participants;scores;league",
        )

        for fixt in fixtures:
            try:
                match = self.adapter.parse_match(fixt)
                result.matches.append(match)

                # Odds
                snaps = self.adapter.fetch_odds_snapshots(fixt["id"])
                result.odds_snapshots.extend(snaps)
            except Exception as e:
                result.errors.append(f"Fixture {fixt.get('id', '?')}: {e}")

        return result

    def ingest_upcoming(
        self,
        days_ahead: int = 30,
        league_ids: list[int] | None = None,
    ) -> IngestionResult:
        """Pull upcoming fixtures with odds for the next N days."""
        if league_ids is None:
            league_ids = list(INTERNATIONAL_LEAGUES.keys())

        now = datetime.now(timezone.utc)
        to_date = now + timedelta(days=days_ahead)

        fixtures = get_fixtures_between(
            now, to_date,
            league_ids=league_ids,
            includes="participants;scores;league",
        )

        result = IngestionResult()
        for fixt in fixtures:
            try:
                match = self.adapter.parse_match(fixt)
                result.matches.append(match)

                snaps = self.adapter.fetch_odds_snapshots(fixt["id"])
                result.odds_snapshots.extend(snaps)
            except Exception as e:
                result.errors.append(f"Fixture {fixt.get('id', '?')}: {e}")

        return result

    def ingest_recent_results(
        self,
        days_back: int = 14,
        league_ids: list[int] | None = None,
    ) -> IngestionResult:
        """Pull recently completed fixtures with results and odds across all specified leagues."""
        if league_ids is None:
            league_ids = list(INTERNATIONAL_LEAGUES.keys())

        now = datetime.now(timezone.utc)
        from_date = now - timedelta(days=days_back)

        fixtures = get_fixtures_between(
            from_date, now,
            league_ids=league_ids,
            includes="participants;scores;league",
        )

        result = IngestionResult()
        for fixt in fixtures:
            try:
                match = self.adapter.parse_match(fixt)
                result.matches.append(match)
                snaps = self.adapter.fetch_odds_snapshots(fixt["id"])
                result.odds_snapshots.extend(snaps)
            except Exception as e:
                result.errors.append(f"Fixture {fixt.get('id', '?')}: {e}")

        return result

    def ingest_prediction_ready(self, fixture_id: int) -> dict[str, Any] | None:
        """
        Fetch everything needed for a prediction for one specific fixture.

        Returns a dict ready to be passed to ProductionPredictionEngine.
        """
        fixt = get_fixture(fixture_id, includes="participants;scores;league")
        if not fixt:
            return None

        match = self.adapter.parse_match(fixt)
        odds_avg = self.adapter.average_odds(fixture_id)

        # Find team IDs from participants
        home_team_id = None
        away_team_id = None
        for p in fixt.get("participants", []):
            meta = p.get("meta", {})
            if isinstance(meta, dict):
                if meta.get("location") == "home":
                    home_team_id = p["id"]
                elif meta.get("location") == "away":
                    away_team_id = p["id"]

        # Get squads for team strength context
        home_squad_size = 0
        away_squad_size = 0
        home_squad = []
        away_squad = []

        if home_team_id:
            home_squad = get_squad(home_team_id)
            home_squad_size = len(home_squad)
        if away_team_id:
            away_squad = get_squad(away_team_id)
            away_squad_size = len(away_squad)

        # Build context features for Dynamic Dixon-Coles
        context = _build_match_context(
            fixt, home_squad, away_squad, home_team_id, away_team_id
        )

        # Determine input quality
        # has_lineups = True only when expected starting XI is available for this match.
        # Squad data (26 players) is NOT a lineup — it's just the pool of eligible players.
        # Expected Lineups requires an add-on; degrade gracefully if unavailable.
        try:
            expected_lineups = self.adapter.fetch_expected_lineup_players(
                fixture_id, match.home_team, match.away_team
            )
        except Exception:
            expected_lineups = []
        has_lineups = len(expected_lineups) > 0
        has_injuries = False
        odds_snapshots_count = len(self.adapter.fetch_odds_snapshots(fixture_id))

        return {
            "match": match,
            "odds": odds_avg,
            "context": context,
            "quality": {
                "team_sample_home": len(home_squad),
                "team_sample_away": len(away_squad),
                "has_lineups": has_lineups,
                "has_injuries": has_injuries,
                "odds_snapshots": odds_snapshots_count,
                "odds_volatility": _estimate_odds_volatility(fixture_id),
            },
            "home_squad_size": home_squad_size,
            "away_squad_size": away_squad_size,
        }

    def ingest_fixture_batch(self, fixture_ids: list[int]) -> list[dict[str, Any]]:
        """Ingest multiple fixtures in prediction-ready format."""
        results = []
        for fid in fixture_ids:
            data = self.ingest_prediction_ready(fid)
            if data:
                results.append(data)
        return results

    # -- Export -----------------------------------------------------------------

    def export_to_json(
        self,
        result: IngestionResult,
        filename: str = "sportmonks_export.json",
    ) -> Path:
        """Export ingestion result as JSON for downstream consumption."""
        path = OUTPUT_DIR / filename
        payload = {
            "matches": [m.to_dict() for m in result.matches],
            "odds_snapshots": [o.to_dict() for o in result.odds_snapshots],
            "lineup_players": [l.to_dict() for l in result.lineup_players],
            "injuries": [i.to_dict() for i in result.injuries],
            "errors": result.errors,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# -- Context feature builder ----------------------------------------------------

def _build_match_context(
    fixture: dict,
    home_squad: list,
    away_squad: list,
    home_team_id: int | None,
    away_team_id: int | None,
) -> dict[str, float]:
    """
    Build pre-match context delta features for Dynamic Dixon-Coles.

    These are KEY/VALUE float pairs that feed into the `context` parameter
    of DynamicDixonColesService.predict_lambdas().
    """
    ctx: dict[str, float] = {}

    # Squad depth ratio (proxy for team strength outside of ELO)
    if home_squad and away_squad:
        h_size = len(home_squad)
        a_size = len(away_squad)
        ratio = min(h_size / max(a_size, 1), 2.0)
        # If home has significantly more squad depth, small attack boost
        if ratio > 1.15:
            ctx["home_attack_delta"] = min((ratio - 1.0) * 0.15, 0.10)
        elif ratio < 0.85:
            ctx["away_attack_delta"] = min(((1.0 / max(ratio, 0.1)) - 1.0) * 0.15, 0.10)

    # Average squad experience (proxied by number of capped players)
    if home_squad:
        home_avg_age = _average_squad_age(home_squad)
        if away_squad:
            away_avg_age = _average_squad_age(away_squad)
            age_diff = home_avg_age - away_avg_age
            if abs(age_diff) > 1.5:
                # More experienced team gets a small boost
                if age_diff > 0:
                    ctx["home_lineup_delta"] = min(age_diff * 0.008, 0.08)
                else:
                    ctx["away_lineup_delta"] = min(abs(age_diff) * 0.008, 0.08)

    return ctx


def _average_squad_age(squad: list) -> float:
    """
    Estimate average age from squad player data.

    Tries cached player data first; skips individual lookups to avoid
    excessive API calls during ingestion. Falls back to a neutral default.
    """
    ages = []
    # Only use the top 5 player IDs to minimize lookups
    player_ids = [s.get("player_id") for s in squad[:5] if s.get("player_id")]
    if not player_ids:
        return 25.0

    from src.data.sportmonks_client import get_player

    for pid in player_ids:
        try:
            p = get_player(pid)
            if p and p.get("date_of_birth"):
                birth = datetime.fromisoformat(p["date_of_birth"])
                age = (datetime.now() - birth).days / 365.25
                if 15 < age < 45:
                    ages.append(age)
        except Exception:
            pass

    return sum(ages) / len(ages) if ages else 25.0


def _estimate_odds_volatility(fixture_id: int) -> float:
    """
    Estimate odds volatility by checking spread across bookmakers.

    Higher spread = more uncertainty/volatility.
    Returns a value roughly in [0, 1] range.
    """
    from src.data.sportmonks_client import get_fixture_odds_1x2

    by_bookie = get_fixture_odds_1x2(fixture_id)
    if len(by_bookie) < 2:
        return 0.25

    homes = [v["home"] for v in by_bookie.values()]
    if not homes:
        return 0.25

    # Coefficient of variation of home odds as proxy
    mean_h = sum(homes) / len(homes)
    if mean_h <= 0:
        return 0.25

    std_h = (sum((h - mean_h) ** 2 for h in homes) / len(homes)) ** 0.5
    cv = std_h / mean_h

    return min(cv * 4, 1.0)  # scale up: typical CV ~0.05-0.15


# -- CLI test -------------------------------------------------------------------

if __name__ == "__main__":
    pipeline = SportmonksIngestion()

    # Test: fetch one WC 2026 match
    data = pipeline.ingest_prediction_ready(19609197)
    if data:
        match = data["match"]
        print(f"Match: {match.home_team} vs {match.away_team}")
        print(f"  Tournament: {match.tournament}")
        print(f"  Status: {match.status}")
        print(f"  Score: {match.home_score} - {match.away_score}")
        print(f"  Avg Odds: {data['odds']}")
        print(f"  Context: {data['context']}")
        print(f"  Quality: {data['quality']}")
        print(f"  Squad sizes: H={data['home_squad_size']} A={data['away_squad_size']}")
