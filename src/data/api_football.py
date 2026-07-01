"""
API-Football free tier client (100 req/day via RapidAPI).
Accessible from China. Provides: fixtures, lineups, team stats, standings.

Sign up for free key: https://rapidapi.com/api-sports/api/api-football
Set env var: export API_FOOTBALL_KEY="your-rapidapi-key"
"""
import os
import time
import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
CACHE_DIR = DATA_DIR / 'processed' / 'api_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = 'https://api-football-v1.p.rapidapi.com/v3'
RATE_LIMIT_DAILY = 100
_request_count = 0


def _get_headers() -> dict:
    key = os.environ.get('API_FOOTBALL_KEY', '')
    if not key:
        raise ValueError("API_FOOTBALL_KEY environment variable not set. "
                         "Get free key at: https://rapidapi.com/api-sports/api/api-football")
    return {
        'x-rapidapi-host': 'api-football-v1.p.rapidapi.com',
        'x-rapidapi-key': key,
    }


def _get(endpoint: str, params: dict = None, cache_ttl_min: int = 60) -> dict:
    """Rate-limited API call with local JSON cache."""
    global _request_count

    cache_key = f"{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
    cache_file = CACHE_DIR / f"{hash(cache_key)}.json"

    # Check cache
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < cache_ttl_min * 60:
            with open(cache_file) as f:
                return json.load(f)

    # Rate limit check
    if _request_count >= RATE_LIMIT_DAILY - 5:
        print(f"WARNING: Nearing daily rate limit ({_request_count}/{RATE_LIMIT_DAILY})")

    resp = requests.get(f"{BASE_URL}{endpoint}", headers=_get_headers(),
                         params=params, timeout=15)
    _request_count += 1

    if resp.status_code == 429:
        print("Rate limited. Waiting 60s...")
        time.sleep(60)
        return _get(endpoint, params, cache_ttl_min)

    resp.raise_for_status()
    data = resp.json()

    # Cache response
    with open(cache_file, 'w') as f:
        json.dump(data, f)

    return data


def get_national_team_id(team_name: str, cache_ttl_min: int = 1440) -> int:
    """Search for a national team by name. Returns API-Football team ID."""
    data = _get('/teams', {'search': team_name, 'country': team_name}, cache_ttl_min)
    teams = data.get('response', [])

    # Prefer national teams (team.national == True)
    national = [t for t in teams if t.get('team', {}).get('national', False)]
    if national:
        return national[0]['team']['id']
    if teams:
        return teams[0]['team']['id']
    return None


# --- World Cup / International Tournament IDs ---
# API-Football league IDs for national team tournaments:
TOURNAMENT_IDS = {
    'World Cup': 1,
    'World Cup Qualifiers (AFC)': 26,
    'World Cup Qualifiers (CAF)': 26,
    'European Championship': 4,
    'Copa America': 9,
    'African Cup of Nations': 6,
    'Asian Cup': 7,
    'CONCACAF Gold Cup': 22,
    'UEFA Nations League': 5,
    'Friendlies': 10,
}


def get_upcoming_fixtures(tournament_id: int = 1,
                           season: int = 2026) -> pd.DataFrame:
    """Get upcoming fixtures for a tournament/season."""
    data = _get('/fixtures', {
        'league': tournament_id,
        'season': season,
        'next': 50,  # next 50 fixtures
    }, cache_ttl_min=30)  # short cache for upcoming matches

    fixtures = []
    for item in data.get('response', []):
        f = item['fixture']
        t = item['teams']
        fixtures.append({
            'fixture_id': f['id'],
            'date': f['date'],
            'timestamp': f['timestamp'],
            'home_team': t['home']['name'],
            'away_team': t['away']['name'],
            'home_team_id': t['home']['id'],
            'away_team_id': t['away']['id'],
            'tournament': item['league']['name'],
            'round': item['league'].get('round', ''),
            'venue': f.get('venue', {}).get('name', ''),
            'venue_city': f.get('venue', {}).get('city', ''),
        })
    return pd.DataFrame(fixtures)


def get_team_squad(team_id: int) -> pd.DataFrame:
    """Get current squad for a national team."""
    data = _get('/players/squads', {'team': team_id}, cache_ttl_min=1440)

    players = []
    for item in data.get('response', []):
        for p in item.get('players', []):
            players.append({
                'player_id': p['id'],
                'name': p['name'],
                'age': p.get('age'),
                'position': p.get('position'),
                'number': p.get('number'),
            })
    return pd.DataFrame(players)


def get_team_statistics(team_id: int, tournament_id: int = 1,
                         season: int = 2026) -> dict:
    """Get team statistics for a given tournament season."""
    data = _get('/teams/statistics', {
        'team': team_id,
        'league': tournament_id,
        'season': season,
    }, cache_ttl_min=360)

    resp = data.get('response', {})
    if not resp:
        return {}

    stats = resp.get('statistics', {})
    fixtures = resp.get('fixtures', {})

    return {
        'form': fixtures.get('form', ''),  # e.g., "WWDLW"
        'played_home': fixtures.get('played', {}).get('home', 0),
        'played_away': fixtures.get('played', {}).get('away', 0),
        'wins_home': fixtures.get('wins', {}).get('home', 0),
        'wins_away': fixtures.get('wins', {}).get('away', 0),
        'draws_home': fixtures.get('draws', {}).get('home', 0),
        'goals_for_avg_home': float(fixtures.get('goals', {}).get('for', {}).get('average', {}).get('home', '0') or 0),
        'goals_against_avg_home': float(fixtures.get('goals', {}).get('against', {}).get('average', {}).get('home', '0') or 0),
    }


def get_fixture_lineups(fixture_id: int) -> dict:
    """Get lineups for a completed fixture."""
    data = _get('/fixtures/lineups', {'fixture': fixture_id}, cache_ttl_min=1440)

    lineups = {}
    for item in data.get('response', []):
        team_name = item['team']['name']
        formation = item.get('formation', '')
        start_xi = [p['player']['name'] for p in item.get('startXI', [])]
        substitutes = [p['player']['name'] for p in item.get('substitutes', [])]
        lineups[team_name] = {
            'formation': formation,
            'start_xi': start_xi,
            'substitutes': substitutes,
        }
    return lineups


def get_head_to_head(team_id_1: int, team_id_2: int,
                      last_n: int = 10) -> pd.DataFrame:
    """Get H2H fixtures between two teams."""
    data = _get('/fixtures/headtohead', {
        'h2h': f'{team_id_1}-{team_id_2}',
        'last': last_n,
    }, cache_ttl_min=1440)

    matches = []
    for item in data.get('response', []):
        f = item['fixture']
        t = item['teams']
        g = item['goals']
        matches.append({
            'date': f['date'],
            'home_team': t['home']['name'],
            'away_team': t['away']['name'],
            'home_score': g['home'],
            'away_score': g['away'],
        })
    return pd.DataFrame(matches)


def get_completed_fixtures(tournament_id: int = 1,
                            season: int = 2022) -> pd.DataFrame:
    """Get ALL completed fixtures for a tournament to build training data."""
    data = _get('/fixtures', {
        'league': tournament_id,
        'season': season,
    }, cache_ttl_min=10080)  # cache 1 week for historical data

    fixtures = []
    for item in data.get('response', []):
        f = item['fixture']
        t = item['teams']
        g = item['goals']
        fixtures.append({
            'fixture_id': f['id'],
            'date': f['date'],
            'home_team': t['home']['name'],
            'away_team': t['away']['name'],
            'home_team_id': t['home']['id'],
            'away_team_id': t['away']['id'],
            'home_score': g.get('home'),
            'away_score': g.get('away'),
            'tournament': item['league']['name'],
            'round': item['league'].get('round', ''),
        })
    return pd.DataFrame(fixtures)


def build_national_team_dataset(tournaments: list = None) -> pd.DataFrame:
    """
    Build a comprehensive national team match dataset from API-Football.
    Covers key international tournaments.
    """
    if tournaments is None:
        tournaments = [
            (1, 2022),   # World Cup 2022
            (1, 2018),   # World Cup 2018
            (4, 2024),   # Euro 2024
            (4, 2020),   # Euro 2020
            (9, 2024),   # Copa America 2024
            (9, 2021),   # Copa America 2021
            (6, 2023),   # AFCON 2023
            (6, 2021),   # AFCON 2021
            (7, 2023),   # Asian Cup 2023
            (5, 2024),   # Nations League 2024
            (10, 2024),  # Friendlies 2024
        ]

    all_matches = []
    for league_id, season in tournaments:
        try:
            df = get_completed_fixtures(league_id, season)
            print(f"Tournament {league_id} season {season}: {len(df)} matches")
            all_matches.append(df)
        except Exception as e:
            print(f"Failed to get league={league_id} season={season}: {e}")
        time.sleep(0.5)  # be nice to the API

    if all_matches:
        result = pd.concat(all_matches, ignore_index=True)
        result['date'] = pd.to_datetime(result['date'])
        result = result.sort_values('date').drop_duplicates(subset=['fixture_id'])
        print(f"\nTotal: {len(result)} national team matches")
        return result

    return pd.DataFrame()


if __name__ == '__main__':
    # Quick test
    try:
        fixtures = get_upcoming_fixtures(tournament_id=1, season=2026)
        print(f"Upcoming WC 2026 fixtures: {len(fixtures)}")
        if not fixtures.empty:
            print(fixtures[['date', 'home_team', 'away_team', 'round']].head(10))
    except Exception as e:
        print(f"Error: {e}")
        print("Set API_FOOTBALL_KEY env var to use this module.")
