"""
Extract pre-match signals from StatsBomb lineup and event data.
Merges with results.csv to create signal-enriched training data.

StatsBomb covers: WC 2018 (64), WC 2022 (64), Euro 2020 (51), Euro 2024 (51), Copa 2024 (32)
Total: 262 matches with lineups + events.
"""
import json
import os
import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
SB_DIR = DATA_DIR / 'raw' / 'statsbomb-open-data'
TM_DIR = DATA_DIR / 'raw' / 'transfermarkt-data'


def load_transfermarkt_players():
    """Load transfermarkt players for cross-referencing."""
    players = pd.read_csv(TM_DIR / 'players.csv')
    valuations = pd.read_csv(TM_DIR / 'player_valuations.csv')
    valuations['date'] = pd.to_datetime(valuations['date'])
    return players, valuations


def load_results():
    """Load results.csv matches."""
    df = pd.read_csv(DATA_DIR / 'raw' / 'results.csv')
    df['date'] = pd.to_datetime(df['date'])
    return df


def extract_lineup_from_statsbomb(match_id: int) -> dict:
    """
    Extract starting XI + subs from a StatsBomb lineup file.
    Returns {team_name: {'starters': [player_names], 'subs': [player_names], 'formation': str}}
    """
    lu_file = SB_DIR / 'lineups' / f'{match_id}.json'
    if not lu_file.exists():
        return {}

    with open(lu_file) as f:
        data = json.load(f)

    result = {}
    for team in data:
        name = team.get('team_name', '')
        lineup = team.get('lineup', [])

        starters = []
        subs = []
        for p in lineup:
            pname = p.get('player_name', '')
            # Cards indicate if they started: starting XI typically have jersey 1-11
            # Better: check if they have a starting position
            positions = p.get('positions', [])
            is_starter = any(
                pos.get('from') == '00:00' or pos.get('from', '') < '01:00'
                for pos in positions
            ) if positions else (p.get('jersey_number', 99) <= 11)

            if is_starter:
                starters.append(pname)
            else:
                subs.append(pname)

        result[name] = {
            'starters': starters,
            'subs': subs,
            'formation': team.get('formation', ''),
        }
    return result


def fuzzy_match_player(sb_name: str, tm_players: pd.DataFrame,
                        country: str = None) -> dict:
    """
    Match a StatsBomb player name to transfermarkt player record.
    Returns {'player_id': int, 'caps': int, 'goals': int, 'market_value': int}
    """
    # Normalize names
    name_parts = sb_name.lower().replace('.', '').split()
    last_name = name_parts[-1] if name_parts else ''

    # Search in TM players
    matches = tm_players[
        tm_players['name'].str.lower().str.contains(last_name, na=False)
    ]
    if country:
        matches = matches[
            matches['country_of_citizenship'].str.lower() == country.lower()
        ]

    if matches.empty and country:
        # Try without country filter
        matches = tm_players[
            tm_players['name'].str.lower().str.contains(last_name, na=False)
        ]

    if matches.empty:
        return {}

    p = matches.iloc[0]
    return {
        'player_id': int(p['player_id']),
        'caps': p.get('international_caps', 0) or 0,
        'goals': p.get('international_goals', 0) or 0,
        'market_value': p.get('market_value_in_eur', 0) or 0,
        'height': p.get('height_in_cm', np.nan),
    }


def compute_signals_for_match(home_team: str, away_team: str,
                                home_starters: list, away_starters: list,
                                tm_players: pd.DataFrame,
                                match_date: pd.Timestamp) -> dict:
    """Compute pre-match signals from extracted lineups."""
    signals = {}

    def team_signals(starters, team_name, prefix, sig_dict):
        player_data = []
        for name in starters:
            info = fuzzy_match_player(name, tm_players, team_name)
            if info:
                player_data.append(info)

        n = len(player_data)
        if n == 0:
            return

        # #56: Debutants (0 caps)
        sig_dict[f'{prefix}_debutants'] = sum(1 for p in player_data if p['caps'] == 0)

        # #57: Height avg
        heights = [p['height'] for p in player_data if pd.notna(p['height'])]
        sig_dict[f'{prefix}_height_avg'] = np.mean(heights) if heights else np.nan

        # #47, #48: Missing caps/goals %
        all_country = tm_players[
            tm_players['country_of_citizenship'].str.lower() == team_name.lower()
        ]
        total_caps = all_country['international_caps'].fillna(0).sum()
        xi_caps = sum(p['caps'] for p in player_data)
        sig_dict[f'{prefix}_missing_caps'] = 1.0 - (xi_caps / total_caps) if total_caps > 0 else np.nan
        sig_dict[f'{prefix}_missing_goals'] = 1.0 - (
            sum(p['goals'] for p in player_data) /
            all_country['international_goals'].fillna(0).sum()
        ) if all_country['international_goals'].fillna(0).sum() > 0 else np.nan

        # #46: Key player missing
        top3 = all_country.nlargest(3, 'international_goals')['player_id'].tolist()
        xi_ids = {p['player_id'] for p in player_data}
        missing = sum(1 for pid in top3 if pid not in xi_ids)
        sig_dict[f'{prefix}_key_missing'] = missing / 3.0

        # #49: XI strength
        xi_val = sum(p['market_value'] for p in player_data)
        top11_val = all_country.nlargest(11, 'market_value_in_eur')[
            'market_value_in_eur'].fillna(0).sum()
        sig_dict[f'{prefix}_xi_strength'] = xi_val / top11_val if top11_val > 0 else np.nan

    team_signals(home_starters, home_team, 'home', signals)
    team_signals(away_starters, away_team, 'away', signals)

    return signals


def extract_all_signals():
    """Main: extract signals from all StatsBomb matches and merge with results."""
    print("Loading data...")
    res = load_results()
    tm_players, tm_vals = load_transfermarkt_players()

    # Get all StatsBomb match IDs from competition files
    import glob
    all_matches = {}
    for f in glob.glob(str(SB_DIR / 'matches' / '*' / '*.json')):
        with open(f) as jf:
            for m in json.load(jf):
                all_matches[m['match_id']] = {
                    'date': pd.to_datetime(m['match_date']),
                    'home_team': m['home_team']['home_team_name'],
                    'away_team': m['away_team']['away_team_name'],
                }

    print(f"StatsBomb matches: {len(all_matches)}")
    print(f"StatsBomb lineups available: {len(os.listdir(SB_DIR / 'lineups'))}")

    # Extract signals for each match
    signals_list = []
    for match_id, info in all_matches.items():
        lu = extract_lineup_from_statsbomb(match_id)
        if not lu:
            continue

        home_info = lu.get(info['home_team'], {})
        away_info = lu.get(info['away_team'], {})

        if not home_info.get('starters') or not away_info.get('starters'):
            continue

        sig = compute_signals_for_match(
            info['home_team'], info['away_team'],
            home_info['starters'], away_info['starters'],
            tm_players, info['date'],
        )

        sig['date'] = info['date']
        sig['home_team'] = info['home_team']
        sig['away_team'] = info['away_team']
        sig['sb_match_id'] = match_id
        signals_list.append(sig)

    signals_df = pd.DataFrame(signals_list)
    print(f"\nExtracted signals for {len(signals_df)} matches")

    # Show coverage
    for col in signals_df.columns:
        if col not in ['date', 'home_team', 'away_team', 'sb_match_id']:
            n = signals_df[col].notna().sum()
            if n > 0:
                print(f"  {col}: {n}/{len(signals_df)}")

    # Merge with results
    merged = res.copy()
    signal_cols = [c for c in signals_df.columns
                   if c not in ['date', 'home_team', 'away_team', 'sb_match_id']]
    for col in signal_cols:
        if col not in merged.columns:
            merged[col] = np.nan

    matches_matched = 0
    for _, sig_row in signals_df.iterrows():
        mask = (
            (merged['date'] == sig_row['date']) &
            (merged['home_team'].str.lower() == str(sig_row['home_team']).lower()) &
            (merged['away_team'].str.lower() == str(sig_row['away_team']).lower())
        )
        if mask.any():
            for col in signal_cols:
                if pd.notna(sig_row.get(col)):
                    merged.loc[mask, col] = sig_row[col]
            matches_matched += 1

    print(f"\nMerged into results: {matches_matched}/{len(signals_df)} matches")

    # Save
    out_path = DATA_DIR / 'processed' / 'results_with_signals_v2.csv'
    merged.to_csv(out_path, index=False)
    print(f"Saved to {out_path}")
    print(f"Total rows with signals: {merged['home_key_missing'].notna().sum()}")

    return merged


if __name__ == '__main__':
    extract_all_signals()
