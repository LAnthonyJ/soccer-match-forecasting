"""
Extract 13 pre-match signals from transfermarkt + StatsBomb data.

Each function takes match-level DataFrames and returns match-level signal columns.
Signals are from the HOME team's perspective by default.
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data'


def load_transfermarkt_data():
    """Load core transfermarkt tables."""
    base = DATA_DIR / 'raw' / 'transfermarkt-data'
    return {
        'players': pd.read_csv(base / 'players.csv'),
        'valuations': pd.read_csv(base / 'player_valuations.csv'),
        'lineups': pd.read_csv(base / 'game_lineups.csv'),
        'national_teams': pd.read_csv(base / 'national_teams.csv'),
        'games': pd.read_csv(base / 'games.csv'),
    }


# ---------------------------------------------------------------------------
# Signal extractors
# ---------------------------------------------------------------------------

def extract_key_player_missing(matches: pd.DataFrame,
                                 lineups: pd.DataFrame,
                                 players: pd.DataFrame) -> pd.Series:
    """
    #46: Key player missing (injury + suspension combined).

    Logic:
      For each match, identify the team's top 3 players by international_goals.
      If any of them is NOT in the starting lineup → signal = 1 (for that team).
      Weighted: missing 1 top player = 0.33, 2 = 0.67, 3 = 1.0.
    """
    # Build lookup: player_id → international_goals
    player_goals = players.set_index('player_id')['international_goals'].fillna(0)

    # Get starting lineups only
    starters = lineups[lineups['type'] == 'starting_lineup'].copy()
    starters['player_id'] = starters['player_id'].astype(int)

    signals = []
    for _, match in matches.iterrows():
        game_id = match.get('game_id')
        if pd.isna(game_id):
            signals.append(np.nan)
            continue

        game_lineups = starters[starters['game_id'] == int(game_id)]
        if game_lineups.empty:
            signals.append(np.nan)
            continue

        # Home team players who started
        home_starters = game_lineups[game_lineups['club_id'] == match.get('home_club_id', -1)]
        if home_starters.empty:
            # Try by team name if club_id doesn't match
            signals.append(np.nan)
            continue

        home_player_ids = home_starters['player_id'].tolist()

        # Get top 3 goal scorers for the home team's country
        country = match.get('home_team_country', '')
        if country:
            country_players = players[players['country_of_citizenship'] == country]
            top3 = country_players.nlargest(3, 'international_goals')
            missing = sum(1 for pid in top3['player_id'] if pid not in home_player_ids)
            signals.append(missing / 3.0)
        else:
            signals.append(np.nan)

    return pd.Series(signals, index=matches.index, name='key_player_missing')


def extract_missing_pct(matches: pd.DataFrame,
                          lineups: pd.DataFrame,
                          players: pd.DataFrame,
                          metric: str = 'caps') -> pd.Series:
    """
    #47 (caps) / #48 (goals): Percentage of team's total caps/goals missing.

    missing_pct = 1 - (sum of caps/goals of starting XI) / (sum of caps/goals of called-up squad)
    """
    player_metric = players.set_index('player_id')[f'international_{metric}'].fillna(0)

    starters = lineups[lineups['type'] == 'starting_lineup'].copy()

    signals = []
    for _, match in matches.iterrows():
        game_id = match.get('game_id')
        if pd.isna(game_id):
            signals.append(np.nan)
            continue

        game_lineups = starters[starters['game_id'] == int(game_id)]
        if game_lineups.empty:
            signals.append(np.nan)
            continue

        home_starters = game_lineups[game_lineups['club_id'] == match.get('home_club_id', -1)]
        if home_starters.empty:
            signals.append(np.nan)
            continue

        starter_ids = home_starters['player_id'].astype(int).tolist()
        starter_total = sum(player_metric.get(pid, 0) for pid in starter_ids)

        # Squad total — approximate by all players from that country with caps > 0
        country = match.get('home_team_country', '')
        if country:
            country_players = players[players['country_of_citizenship'] == country]
            squad_total = country_players[f'international_{metric}'].fillna(0).sum()
            if squad_total > 0:
                pct = starter_total / squad_total
                signals.append(1.0 - pct)  # missing % = 1 - present %
            else:
                signals.append(np.nan)
        else:
            signals.append(np.nan)

    name = f'missing_{metric}_pct'
    return pd.Series(signals, index=matches.index, name=name)


def extract_starting_xi_strength(matches: pd.DataFrame,
                                   lineups: pd.DataFrame,
                                   valuations: pd.DataFrame) -> pd.Series:
    """
    #49: Starting XI strength = actual XI market value / best possible XI value.

    Requires knowing the best possible XI — approximated by the top-valued
    players at each position from that country.
    """
    starters = lineups[lineups['type'] == 'starting_lineup'].copy()

    signals = []
    for _, match in matches.iterrows():
        game_id = match.get('game_id')
        match_date = match.get('date')
        if pd.isna(game_id) or pd.isna(match_date):
            signals.append(np.nan)
            continue

        game_lineups = starters[starters['game_id'] == int(game_id)]
        if game_lineups.empty:
            signals.append(np.nan)
            continue

        home_starters = game_lineups[game_lineups['club_id'] == match.get('home_club_id', -1)]
        if home_starters.empty:
            signals.append(np.nan)
            continue

        starter_ids = home_starters['player_id'].astype(int).tolist()

        # Get market values for these players at match date
        actual_value = 0
        for pid in starter_ids:
            pv = valuations[
                (valuations['player_id'] == pid) &
                (valuations['date'] <= match_date)
            ]
            if not pv.empty:
                actual_value += pv.sort_values('date').iloc[-1]['market_value_in_eur']

        # Best XI value: top 11 valued players from same country, same date
        # Simplified: just compare to top-valued players overall
        country_vals = valuations[
            (valuations['date'] <= match_date)
        ]
        # This is a rough proxy — proper implementation requires position matching
        top11_value = country_vals.nlargest(11, 'market_value_in_eur')['market_value_in_eur'].sum()

        if top11_value > 0:
            signals.append(actual_value / top11_value)
        else:
            signals.append(np.nan)

    return pd.Series(signals, index=matches.index, name='starting_xi_strength')


def extract_formation_change(matches: pd.DataFrame) -> pd.Series:
    """
    #50: Whether the formation changed from the previous 3 matches.
    Binary: 1 = different formation, 0 = same as last match.

    Uses home_club_formation / away_club_formation from transfermarkt games.
    """
    signals = []
    prev_formations = {}  # team → last formation

    for _, match in matches.iterrows():
        home = match.get('home_team', '')
        home_form = match.get('home_club_formation', '')

        if home and pd.notna(home_form):
            prev = prev_formations.get(home)
            signals.append(1 if prev and prev != home_form else 0)
            prev_formations[home] = home_form
        else:
            signals.append(np.nan)

    return pd.Series(signals, index=matches.index, name='formation_change')


def extract_new_manager_matches(matches: pd.DataFrame) -> pd.Series:
    """
    #51: Number of matches under current manager.
    Lower = newer manager, more uncertainty.

    Tracks manager name changes from transfermarkt games.
    """
    signals = []
    manager_since = {}  # team → (manager_name, match_count)

    for _, match in matches.iterrows():
        home = match.get('home_team', '')
        mgr = match.get('home_club_manager_name', '')

        if home and pd.notna(mgr):
            prev_mgr, count = manager_since.get(home, (mgr, 0))
            if prev_mgr == mgr:
                count += 1
            else:
                count = 1
            manager_since[home] = (mgr, count)
            signals.append(count)
        else:
            signals.append(np.nan)

    return pd.Series(signals, index=matches.index, name='new_manager_matches')


def extract_news_sentiment(matches: pd.DataFrame) -> pd.Series:
    """
    #52: Pre-match news sentiment score.
    Range [-1, +1] from SnowNLP analysis of Chinese football news.

    PLACEHOLDER — needs news scraper integration.
    """
    return pd.Series(np.nan, index=matches.index, name='team_news_sentiment')


def extract_injury_severity(matches: pd.DataFrame) -> pd.Series:
    """
    #53: Weighted injury severity.

    Σ(injured_player_importance × severity) / total_players
    where importance = international_caps / max_caps_in_squad

    PLACEHOLDER — needs injury report parsing from news.
    """
    return pd.Series(np.nan, index=matches.index, name='injury_severity')


def extract_debutants(matches: pd.DataFrame,
                        lineups: pd.DataFrame,
                        players: pd.DataFrame) -> pd.Series:
    """
    #56: Number of debutants (0 caps) in starting XI.
    Higher = more inexperienced lineup.
    """
    player_caps = players.set_index('player_id')['international_caps'].fillna(0)
    starters = lineups[lineups['type'] == 'starting_lineup'].copy()

    signals = []
    for _, match in matches.iterrows():
        game_id = match.get('game_id')
        if pd.isna(game_id):
            signals.append(np.nan)
            continue

        game_lineups = starters[starters['game_id'] == int(game_id)]
        if game_lineups.empty:
            signals.append(np.nan)
            continue

        home_starters = game_lineups[game_lineups['club_id'] == match.get('home_club_id', -1)]
        if home_starters.empty:
            signals.append(np.nan)
            continue

        starter_ids = home_starters['player_id'].astype(int).tolist()
        debutants = sum(1 for pid in starter_ids if player_caps.get(pid, 0) == 0)
        signals.append(debutants)

    return pd.Series(signals, index=matches.index, name='debutants_in_squad')


def extract_squad_height(matches: pd.DataFrame,
                           lineups: pd.DataFrame,
                           players: pd.DataFrame) -> pd.Series:
    """
    #57: Average height (cm) of starting XI.
    Higher = aerial advantage, especially for set pieces.
    """
    player_height = players.set_index('player_id')['height_in_cm']
    starters = lineups[lineups['type'] == 'starting_lineup'].copy()

    signals = []
    for _, match in matches.iterrows():
        game_id = match.get('game_id')
        if pd.isna(game_id):
            signals.append(np.nan)
            continue

        game_lineups = starters[starters['game_id'] == int(game_id)]
        if game_lineups.empty:
            signals.append(np.nan)
            continue

        home_starters = game_lineups[game_lineups['club_id'] == match.get('home_club_id', -1)]
        if home_starters.empty:
            signals.append(np.nan)
            continue

        starter_ids = home_starters['player_id'].astype(int).tolist()
        heights = [player_height.get(pid) for pid in starter_ids]
        heights = [h for h in heights if pd.notna(h)]
        signals.append(np.mean(heights) if heights else np.nan)

    return pd.Series(signals, index=matches.index, name='squad_height_avg')


def extract_extra_time_prev(matches: pd.DataFrame) -> pd.Series:
    """
    #59: Did this team play extra time in their previous match?
    Binary: 1 = yes (fatigue), 0 = no.

    Detected from StatsBomb event periods (period > 2 = extra time).
    Simplified: inferred from knockout round matches that went to penalties
    or had extra-time indicators.
    """
    # Simplest detection: if previous match score was a draw in knockout
    # (went to ET/pens), or if event data shows period 3/4.
    signals = []
    prev_had_et = {}  # team → bool

    for _, match in matches.iterrows():
        home = match.get('home_team', '')
        signals.append(1 if prev_had_et.get(home, False) else 0)

        # Detect if THIS match had ET (for checking NEXT match)
        home_score = match.get('home_score')
        away_score = match.get('away_score')
        is_knockout = match.get('is_knockout', False)

        if is_knockout and pd.notna(home_score) and pd.notna(away_score):
            had_et = (home_score == away_score)  # draw in KO → ET/pens likely
            prev_had_et[home] = had_et
            away = match.get('away_team', '')
            prev_had_et[away] = had_et

    return pd.Series(signals, index=matches.index, name='prev_match_extra_time')


def extract_travel_fatigue(matches: pd.DataFrame) -> pd.Series:
    """
    #60: Cumulative travel fatigue.

    Sum of distance traveled in last 7 days (km). Requires venue coordinates.
    Simplified: count of matches played away from home country in last 7 days.
    """
    signals = []
    recent_travel = {}  # team → list of (date, is_away)

    for _, match in matches.iterrows():
        home = match.get('home_team', '')
        away = match.get('away_team', '')
        match_date = match.get('date')
        neutral = match.get('neutral', False)

        if pd.isna(match_date):
            signals.append(np.nan)
            continue

        # Count home team's recent travels
        home_travel = recent_travel.get(home, [])
        recent_count = sum(
            1 for d, is_away in home_travel
            if (match_date - d).days <= 7 and is_away
        )
        signals.append(recent_count)

        # Record this match for future
        recent_travel.setdefault(home, []).append((match_date, neutral or False))
        recent_travel.setdefault(away, []).append((match_date, neutral or False))

    return pd.Series(signals, index=matches.index, name='travel_fatigue')


def extract_tactical_matchup(matches: pd.DataFrame) -> pd.Series:
    """
    #54: Tactical matchup score.

    Requires StatsBomb event analysis: classify each team's style
    (possession, pressing, counter-attack, low-block) and compute
    historical win-rate deviation when style A faces style B.

    PLACEHOLDER — needs StatsBomb event aggregation.
    """
    return pd.Series(np.nan, index=matches.index, name='tactical_matchup')


# ---------------------------------------------------------------------------
# Master extractor
# ---------------------------------------------------------------------------

def extract_all_signals(matches: pd.DataFrame,
                          tm_data: dict = None,
                          statsbomb_events_dir: str = None) -> pd.DataFrame:
    """
    Extract all 13 pre-match signals for a match DataFrame.

    Args:
        matches: DataFrame with at minimum [date, home_team, away_team].
                 Should also have transfermarkt game_id, home_club_id etc. if available.
        tm_data: dict from load_transfermarkt_data(), or None to auto-load.
        statsbomb_events_dir: path to StatsBomb events JSON files.

    Returns:
        matches with 13 new signal columns added.
    """
    df = matches.copy()

    if tm_data is None:
        try:
            tm_data = load_transfermarkt_data()
        except Exception:
            tm_data = {}

    lineups = tm_data.get('lineups')
    players = tm_data.get('players')
    valuations = tm_data.get('valuations')

    has_tm = lineups is not None and players is not None

    # Extract each signal (with graceful fallback)
    extractors = {
        'key_player_missing': lambda: extract_key_player_missing(df, lineups, players) if has_tm else np.nan,
        'missing_caps_pct': lambda: extract_missing_pct(df, lineups, players, 'caps') if has_tm else np.nan,
        'missing_goals_pct': lambda: extract_missing_pct(df, lineups, players, 'goals') if has_tm else np.nan,
        'starting_xi_strength': lambda: extract_starting_xi_strength(df, lineups, valuations) if has_tm and valuations is not None else np.nan,
        'formation_change': lambda: extract_formation_change(df),
        'new_manager_matches': lambda: extract_new_manager_matches(df),
        'team_news_sentiment': lambda: extract_news_sentiment(df),
        'injury_severity': lambda: extract_injury_severity(df),
        'tactical_matchup': lambda: extract_tactical_matchup(df),
        'debutants_in_squad': lambda: extract_debutants(df, lineups, players) if has_tm else np.nan,
        'squad_height_avg': lambda: extract_squad_height(df, lineups, players) if has_tm else np.nan,
        'prev_match_extra_time': lambda: extract_extra_time_prev(df),
        'travel_fatigue': lambda: extract_travel_fatigue(df),
    }

    for name, fn in extractors.items():
        try:
            result = fn()
            if isinstance(result, pd.Series):
                df[name] = result
            else:
                df[name] = np.nan
        except Exception as e:
            df[name] = np.nan

    return df


if __name__ == '__main__':
    tm = load_transmarkt_data()
    print("Transfermarkt data loaded:")
    for k, v in tm.items():
        print(f"  {k}: {len(v):,} rows")
