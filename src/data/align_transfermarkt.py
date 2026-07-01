"""
Align transfermarkt national team data with results.csv matches.

Outputs:
  1. Team name mapping (results.csv → transfermarkt)
  2. Aligned matches with signal columns computed from TM data
  3. Merge-ready DataFrame
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
RAW_DIR = DATA_DIR / 'raw'
TM_DIR = RAW_DIR / 'transfermarkt-data'


def load_all():
    """Load all relevant datasets."""
    res = pd.read_csv(RAW_DIR / 'results.csv')
    res['date'] = pd.to_datetime(res['date'])

    tm_nt = pd.read_csv(TM_DIR / 'national_teams.csv')
    tm_games = pd.read_csv(TM_DIR / 'games.csv')
    tm_lineups = pd.read_csv(TM_DIR / 'game_lineups.csv')
    tm_players = pd.read_csv(TM_DIR / 'players.csv')
    tm_vals = pd.read_csv(TM_DIR / 'player_valuations.csv')
    tm_vals['date'] = pd.to_datetime(tm_vals['date'])
    tm_comps = pd.read_csv(TM_DIR / 'competitions.csv')

    return res, tm_nt, tm_games, tm_lineups, tm_players, tm_vals, tm_comps


def filter_nt_games(tm_games, tm_comps):
    """Filter transfermarkt games to national team matches only."""
    nt_comp_ids = set(tm_comps[
        tm_comps['type'] == 'national_team_competition'
    ]['competition_id'])
    nt = tm_games[tm_games['competition_id'].isin(nt_comp_ids)].copy()
    nt['date'] = pd.to_datetime(nt['date'])
    return nt


def build_name_mapping(res_teams, tm_names):
    """
    Build team name mapping between results.csv and transfermarkt.
    Most names are identical; handle known differences.
    """
    mapping = {}
    tm_set = set(tm_names)

    # Direct matches
    for t in res_teams:
        if t in tm_set:
            mapping[t] = t

    # Known differences (results → transfermarkt)
    known = {
        'United States': 'United States',
        'USA': 'United States',
        'Korea Republic': 'South Korea',
        'Korea DPR': 'North Korea',
        'IR Iran': 'Iran',
        "Côte d'Ivoire": 'Ivory Coast',
        'Czech Republic': 'Czech Republic',
        'Czechia': 'Czech Republic',
        'Northern Ireland': 'Northern Ireland',
        'Republic of Ireland': 'Ireland',
        'Bosnia and Herzegovina': 'Bosnia-Herzegovina',
        'Trinidad and Tobago': 'Trinidad and Tobago',
        'Cape Verde': 'Cape Verde',
        'Burkina Faso': 'Burkina Faso',
        'United Arab Emirates': 'United Arab Emirates',
        'Saudi Arabia': 'Saudi Arabia',
        'Congo': 'Congo',
        'DR Congo': 'DR Congo',
        'Central African Republic': 'Central African Republic',
        'Papua New Guinea': 'Papua New Guinea',
        'Equatorial Guinea': 'Equatorial Guinea',
        'Guinea-Bissau': 'Guinea-Bissau',
        'Sierra Leone': 'Sierra Leone',
        'South Sudan': 'South Sudan',
        'South Africa': 'South Africa',
        'São Tomé and Príncipe': 'Sao Tome and Principe',
        'New Caledonia': 'New Caledonia',
        'Solomon Islands': 'Solomon Islands',
        'Antigua and Barbuda': 'Antigua and Barbuda',
        'Saint Kitts and Nevis': 'Saint Kitts and Nevis',
        'Saint Vincent and the Grenadines': 'Saint Vincent and the Grenadines',
        'Saint Lucia': 'Saint Lucia',
    }
    for res_name, tm_name in known.items():
        if tm_name in tm_set:
            mapping[res_name] = tm_name

    print(f"  Name mapping: {len(mapping)} teams mapped")
    return mapping


def align_matches(res, nt_games, name_map):
    """
    Align results.csv matches with transfermarkt NT games by date + teams.
    Returns DataFrame with match_id from both sources.
    """
    res = res.copy()
    res['home_tm'] = res['home_team'].map(name_map)
    res['away_tm'] = res['away_team'].map(name_map)

    matches = []
    for _, rg in res.iterrows():
        if pd.isna(rg['home_tm']) or pd.isna(rg['away_tm']):
            continue

        # Find matching TM games
        tm_match = nt_games[
            (nt_games['date'] == rg['date']) &
            (nt_games['home_club_name'] == rg['home_tm']) &
            (nt_games['away_club_name'] == rg['away_tm'])
        ]
        if len(tm_match) == 1:
            tm_row = tm_match.iloc[0]
            matches.append({
                'res_idx': rg.name,
                'date': rg['date'],
                'home_team': rg['home_team'],
                'away_team': rg['away_team'],
                'tm_game_id': tm_row['game_id'],
                'home_score': rg['home_score'],
                'away_score': rg['away_score'],
                'tournament': rg['tournament'],
                'neutral': rg['neutral'],
                'home_formation': tm_row.get('home_club_formation'),
                'away_formation': tm_row.get('away_club_formation'),
                'home_manager': tm_row.get('home_club_manager_name'),
                'away_manager': tm_row.get('away_club_manager_name'),
            })

    result = pd.DataFrame(matches)
    print(f"  Aligned: {len(result)} matches")
    if len(result) > 0:
        print(f"  Date range: {result['date'].min().date()} ~ {result['date'].max().date()}")
    return result


def extract_signals_for_aligned(aligned, tm_lineups, tm_players, tm_vals, tm_games):
    """
    For each aligned match, compute the 12 pre-match signals
    from transfermarkt lineup + player data.
    """
    player_caps = tm_players.set_index('player_id')['international_caps'].fillna(0)
    player_goals = tm_players.set_index('player_id')['international_goals'].fillna(0)
    player_height = tm_players.set_index('player_id')['height_in_cm']
    player_market = tm_players.set_index('player_id')['market_value_in_eur'].fillna(0)

    # Build player_id → country mapping
    pid_to_country = tm_players.set_index('player_id')['country_of_citizenship'].to_dict()

    starters = tm_lineups[tm_lineups['type'] == 'starting_lineup'].copy()

    # Build game_id → {home_club_id, away_club_id} lookup
    game_club_ids = {}
    for _, g in tm_games.iterrows():
        game_club_ids[g['game_id']] = (g.get('home_club_id'), g.get('away_club_id'))

    signals = []
    for _, match in aligned.iterrows():
        gid = match['tm_game_id']
        home = match['home_team']
        away = match['away_team']
        match_date = match['date']

        sig = {'tm_game_id': gid, 'home_team': home, 'away_team': away}

        game_lu = starters[starters['game_id'] == gid]
        if game_lu.empty:
            signals.append(sig)
            continue

        # Split by club_id (home = first club, away = second)
        club_ids = game_club_ids.get(gid, (None, None))
        home_cid = club_ids[0]
        away_cid = club_ids[1]

        if home_cid is not None:
            home_lu = game_lu[game_lu['club_id'] == home_cid]
        else:
            home_lu = game_lu.iloc[:11]
        if away_cid is not None:
            away_lu = game_lu[game_lu['club_id'] == away_cid]
        else:
            away_lu = game_lu.iloc[11:22] if len(game_lu) >= 22 else pd.DataFrame()

        def compute_team_signals(lu_df, team_name, prefix):
            pids = lu_df['player_id'].astype(int).tolist() if not lu_df.empty else []

            # #56: Debutants
            sig[f'{prefix}_debutants'] = sum(1 for pid in pids
                                              if player_caps.get(pid, 0) == 0)

            # #57: Height
            hts = [player_height.get(pid) for pid in pids]
            hts = [h for h in hts if pd.notna(h)]
            sig[f'{prefix}_height_avg'] = np.mean(hts) if hts else np.nan

            if not pids:
                return

            # Get all players from this country
            country_pids = [pid for pid, c in pid_to_country.items()
                            if c == team_name]
            if not country_pids:
                return

            cp_mask = tm_players['player_id'].isin(country_pids)
            cp_df = tm_players[cp_mask]

            # #46: Top-3 goal scorers missing
            top3 = cp_df.nlargest(3, 'international_goals')['player_id'].tolist()
            missing = sum(1 for pid in top3 if pid not in pids)
            sig[f'{prefix}_key_missing'] = missing / 3.0

            # #47, #48: Missing caps/goals %
            total_caps = cp_df['international_caps'].fillna(0).sum()
            total_goals = cp_df['international_goals'].fillna(0).sum()
            xi_caps = sum(player_caps.get(pid, 0) for pid in pids)
            xi_goals = sum(player_goals.get(pid, 0) for pid in pids)
            sig[f'{prefix}_missing_caps'] = 1.0 - (xi_caps / total_caps) if total_caps > 0 else np.nan
            sig[f'{prefix}_missing_goals'] = 1.0 - (xi_goals / total_goals) if total_goals > 0 else np.nan

            # #49: XI market value vs best possible
            actual_val = sum(_get_latest_val(pid, match_date, tm_vals) for pid in pids)
            top11_pids = cp_df.nlargest(11, 'market_value_in_eur')['player_id'].tolist()
            best_val = sum(_get_latest_val(pid, match_date, tm_vals) for pid in top11_pids)
            sig[f'{prefix}_xi_strength'] = actual_val / best_val if best_val > 0 else np.nan

        compute_team_signals(home_lu, home, 'home')
        compute_team_signals(away_lu, away, 'away')

        signals.append(sig)

    return pd.DataFrame(signals)


def _get_latest_val(pid, date, tm_vals):
    pv = tm_vals[(tm_vals['player_id'] == pid) & (tm_vals['date'] <= date)]
    if pv.empty:
        return 0
    return pv.sort_values('date').iloc[-1]['market_value_in_eur']


def merge_signals_to_results(results_df, aligned, signals_df):
    """
    Merge computed signals back into the full results DataFrame.
    Creates columns for both home and away teams' signals.
    """
    df = results_df.copy()

    # Signal columns to create (home and away variants)
    signal_base = ['key_missing', 'missing_caps', 'missing_goals',
                   'xi_strength', 'debutants', 'height_avg']
    for base in signal_base:
        for side in ['home', 'away']:
            col = f'{side}_{base}'
            if col not in df.columns:
                df[col] = np.nan

    if 'res_idx' not in aligned.columns:
        return df

    for _, row in aligned.iterrows():
        idx = row['res_idx']
        sig = signals_df[signals_df['tm_game_id'] == row['tm_game_id']]
        if sig.empty:
            continue
        s = sig.iloc[0]

        for side in ['home', 'away']:
            for base in signal_base:
                src = f'{side}_{base}'
                dst = f'{side}_{base}'
                if src in s and pd.notna(s[src]):
                    df.at[idx, dst] = s[src]

    n = df['home_key_missing'].notna().sum()
    print(f"  Merged signals for {n} rows")
    return df


def run_alignment():
    """Main: align data and compute signals."""
    print("Loading data...")
    res, tm_nt, tm_games, tm_lineups, tm_players, tm_vals, tm_comps = load_all()

    print("\nFiltering NT games...")
    nt_games = filter_nt_games(tm_games, tm_comps)
    print(f"  NT games: {len(nt_games)}")

    print("\nBuilding name mapping...")
    res_teams = sorted(pd.concat([res['home_team'], res['away_team']]).unique())
    tm_names = sorted(tm_nt['name'].unique())
    name_map = build_name_mapping(res_teams, tm_names)

    print("\nAligning matches...")
    aligned = align_matches(res, nt_games, name_map)

    if aligned.empty:
        print("ERROR: No matches aligned! Check team name mapping.")
        return None, None, None

    print("\nExtracting signals...")
    signals = extract_signals_for_aligned(aligned, tm_lineups, tm_players, tm_vals, tm_games)

    # Count valid signals
    for col in signals.columns:
        if col not in ['tm_game_id', 'home_team', 'away_team', 'formation_home']:
            n = signals[col].notna().sum()
            if n > 0:
                print(f"  {col}: {n}/{len(signals)} valid")

    print("\nMerging back to results.csv...")
    enriched = merge_signals_to_results(res, aligned, signals)

    # Save
    out_path = DATA_DIR / 'processed' / 'results_with_signals.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(out_path, index=False)
    print(f"\nSaved enriched data to {out_path}")
    print(f"  Rows with signals: {enriched['key_player_missing'].notna().sum()}")

    return enriched, aligned, signals


if __name__ == '__main__':
    run_alignment()
