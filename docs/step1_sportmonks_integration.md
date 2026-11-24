# Sportmonks data integration

This historical implementation note documents the paid API v3 integration for fixtures, odds, squads, expected lineups and player information. The prediction CLI calls SportmonksIngestion, which coordinates the HTTP client and normalized provider records.

## Client and adapter

The client authenticates with SPORTMONKS_API_KEY, keys its JSON cache by the MD5 of endpoint and parameters, counts requests against the 2,000/day budget, waits 65 seconds before retrying a 429 response and disables environment proxy inheritance for the documented SSL timeout. Cache files live under data/processed/sportmonks_cache/. The integration uses the existing requests dependency.

| Function | Endpoint | Cache TTL | Purpose |
|------|------|----------|------|
| `get_fixture(id, includes)` | `/fixtures/{id}` | 10min | Fixture details |
| `get_fixtures_between(from, to, ...)` | `/fixtures/between/{}/{}/` | 10min | Fixtures by date with pagination |
| `get_fixture_odds_1x2(id)` | `/fixtures/{id}?include=odds` | 10min | Market 1, grouped by bookmaker |
| `get_squad(team_id)` | `/squads/teams/{id}` | 6h | Current squad |
| `get_player(player_id)` | `/players/{id}` | 24h | Player details including birth date |
| `get_team(team_id)` | `/teams/{id}` | 24h | Team metadata |
| `get_national_teams()` | `/teams` | — | National teams only |
| `get_expected_lineups(fixture_id)` | `/expected-lineups/fixtures/{id}` | 30min | Expected starting lineup |
| `get_bookmaker_name(id)` | `/bookmakers/{id}` | 7d | Bookmaker name |
| `paginate(endpoint, params)` | Any endpoint | — | Collect paginated results |
| `batch_get_players(ids)` | Batch `/players/{id}` | — | Fetch multiple players |

```python
POSITION_MAP = {24: "GK", 25: "DF", 26: "MF", 27: "MF", 28: "FW", 29: "FW", 30: "DF", 31: "DF", 32: "MF"}
```

The adapter implements the ProviderAdapter contract. It resolves home/away through participants' meta.location, uses CURRENT scores (type 1525), and maps states 1/2 to scheduled, 3/4 to live, 5 to finished and 7–9 to cancelled or postponed. Team aliases align with results.csv, including Congo DR → DR Congo and Türkiye → Turkey. Each 1X2 snapshot contains the bookmaker and timestamp. The integration averages available bookmaker prices for market fusion. It enriches squad entries with names and positions and fetches expected starters when the subscription includes that feature. A player's squad end date serves as a limited proxy for unavailability.

## Ingestion and prediction

SportmonksIngestion supports tournament fixtures and odds, upcoming international fixtures, recent results, fixture batches and JSON export. ingest_prediction_ready returns the match, average odds when available, context and input quality for the prediction engine. A squad-size advantage above 15% yields an attack adjustment capped at 0.10. Average age uses the first five cached player records per team. A gap above 1.5 years yields a lineup adjustment capped at 0.08. Odds volatility uses the scaled coefficient of variation of home prices across bookmakers, clipped to [0, 1].

Registered competitions in the historical integration:

```python
INTERNATIONAL_LEAGUES = {
    732: "World Cup",          714: "WC Qualification Asia",
    717: "WC Qualification Concacaf",  720: "WC Qualification Europe",
    723: "WC Qualification Oceania",   726: "WC Qualification South America",
    711: "CAF World Cup Qualifiers",   729: "WC Qualification Intercontinental Playoffs",
    1105: "AFC Asian Cup",     1114: "Copa America",
    1117: "Africa Cup of Nations",     1118: "AFCON Qualifications",
    1325: "Euro Qualification",        1326: "European Championship",
    1538: "UEFA Nations League",       1082: "Friendly International",
    1103: "African Nations Championship", 1112: "CONCACAF Gold Cup",
    1124: "OFC Nations Cup",   1125: "Olympic Games",
}
```

The prediction CLI supports single fixtures, upcoming games and date ranges. Snapshot metadata includes tournament, UTC kickoff, match status and actual goals for later comparisons.

```sh
python src/predict_production_live.py --fixture-id 19609197 [--tier vip] [--output path.json]
python src/predict_production_live.py --upcoming --days 7
python src/predict_production_live.py --range --from 2026-06-01 --to 2026-06-30
```

## Recorded verification

For fixture 19609197, 21 bookmakers supplied average home/draw/away prices of 3.41/2.16/3.40. After 1.10× goal calibration, the model probabilities were 30.0%/33.5%/36.5%. The margin-adjusted market probabilities were 27.9%/44.1%/28.0%. The snapshot used 64% model weight and 36% market weight and included expected goals, totals, both-teams-to-score probability, the 15 most likely scores, a heatmap and market edge. The match finished 3–3. Over 2.5 had a 51.0% forecast and the exact draw was outside the top 15.

The original verification recorded 20 passing tests in 0.004 seconds: eight existing Phase 7 checks and 12 adapter/ingestion tests using mock data. Expected lineups typically arrived only one to two hours before kickoff under the add-on. Subscription coverage, approximate player experience, squad-end injury proxies and the initial six hand-maintained team mappings limit coverage across more than 200 teams.

The planned follow-up work included a chronological backtest runner, Streamlit integration with ProductionPredictionEngine, persistence of prediction snapshots, odds and model runs in SQLite or PostgreSQL, and richer lineup and injury features.
