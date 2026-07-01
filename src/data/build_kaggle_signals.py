"""
Build pre-match signals from Kaggle datasets:
- goalscorers.csv: 47K goals with player names, dates, teams
- fifa_players.csv: 18K players with FIFA game ratings

Strategy:
1. For each match, collect goal scorers → "players who appeared"
2. Cross-reference with FIFA ratings → compute team quality signals
3. Detect missing key players by comparing "who scored" vs "top rated"
4. Build 6 pre-match signals at scale (thousands of matches)
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
RAW_DIR = DATA_DIR / 'raw'


def load_all():
    res = pd.read_csv(RAW_DIR / 'results.csv')
    res['date'] = pd.to_datetime(res['date'])

    gs = pd.read_csv(RAW_DIR / 'kaggle-results' / 'goalscorers.csv')
    gs['date'] = pd.to_datetime(gs['date'])

    fp = pd.read_csv(RAW_DIR / 'kaggle-players' / 'fifa_players.csv')
    # Standardize national team names
    fp['nt_name'] = fp['nationality'].str.strip()

    return res, gs, fp


def build_player_index(fp):
    """Build lookup: country → list of {name, rating, position} sorted by rating."""
    idx = {}
    for country, group in fp.groupby('nt_name'):
        players = []
        for _, p in group.iterrows():
            players.append({
                'name': str(p['name']),
                'rating': p['overall_rating'],
                'position': str(p.get('positions', '')),
                'national_rating': p.get('national_rating', np.nan),
            })
        players.sort(key=lambda x: x['rating'], reverse=True)
        idx[country] = players
    print(f"  Player index: {len(idx)} countries")
    return idx


def build_match_scorers(gs):
    """
    Build lookup: (date, home_team, away_team) → {home_scorers: [...], away_scorers: [...]}

    Also builds per-team scorer history for detecting who is "expected" to score.
    """
    ms = {}
    for _, g in gs.iterrows():
        key = (g['date'], g['home_team'], g['away_team'])
        if key not in ms:
            ms[key] = {'home_scorers': set(), 'away_scorers': set()}

        if g['team'] == g['home_team']:
            ms[key]['home_scorers'].add(str(g['scorer']))
        elif g['team'] == g['away_team']:
            ms[key]['away_scorers'].add(str(g['scorer']))

    print(f"  Match scorers: {len(ms)} matches")
    return ms


def compute_signals_for_match(row, match_scorers, player_idx):
    """Compute all 6 signals for one match."""
    signals = {}
    date = row['date']
    home = row['home_team']
    away = row['away_team']

    # Find goal scorers
    key = (date, home, away)
    ms = match_scorers.get(key, {'home_scorers': set(), 'away_scorers': set()})

    def team_signals(scorers, team_name, prefix, idx):
        if team_name not in idx:
            return

        country_players = idx[team_name]
        if not country_players:
            return

        top_rating = country_players[0]['rating']
        top11 = country_players[:11]

        # Match scorers to player index by name
        matched = []
        for s in scorers:
            for p in country_players:
                # Fuzzy: last name match
                if (s.lower() in p['name'].lower() or
                    p['name'].lower() in s.lower()):
                    matched.append(p)
                    break

        scorer_ratings = [p['rating'] for p in matched]
        n_matched = len(matched)

        # #46: Key player missing — top-3 rated didn't score/appear
        if n_matched > 0 and len(country_players) >= 3:
            top3_names = {p['name'] for p in country_players[:3]}
            scorer_names = {p['name'] for p in matched}
            missing = sum(1 for n in top3_names if n not in scorer_names)
            # Weight: if team scored 0, can't tell if top player was missing
            if len(scorers) > 0:
                signals[f'{prefix}_key_missing'] = missing / 3.0
            else:
                # No goals = top players might still have played, just didn't score
                signals[f'{prefix}_key_missing'] = np.nan
        else:
            signals[f'{prefix}_key_missing'] = np.nan

        # #49: XI strength — avg rating of scorers / avg rating of top11
        if n_matched >= 2:
            avg_scorer_rating = np.mean(scorer_ratings)
            avg_top11 = np.mean([p['rating'] for p in top11])
            signals[f'{prefix}_xi_strength'] = avg_scorer_rating / avg_top11 if avg_top11 > 0 else np.nan
        else:
            signals[f'{prefix}_xi_strength'] = np.nan

        # #56: Debutants — players with low rating appearing
        if n_matched >= 2:
            newbies = sum(1 for p in matched if p['rating'] < 70)
            signals[f'{prefix}_debutants'] = newbies
        else:
            signals[f'{prefix}_debutants'] = np.nan

        # #57: Height (from FIFA data, not scorers — use country average)
        # Not available from scorers data

        # #59: Extra time — use the result to detect ET
        # Not available from scorers

    team_signals(ms['home_scorers'], home, 'home', player_idx)
    team_signals(ms['away_scorers'], away, 'away', player_idx)

    return signals


def build_all_signals():
    """Main: build signals for all matches that have goalscorer data."""
    print("Loading data...")
    res, gs, fp = load_all()

    print("\nBuilding player index...")
    player_idx = build_player_index(fp)

    print("\nBuilding match scorers lookup...")
    match_scorers = build_match_scorers(gs)

    print("\nComputing signals...")
    signal_list = []
    for _, row in res.iterrows():
        signals = compute_signals_for_match(row, match_scorers, player_idx)
        if signals:
            signals['date'] = row['date']
            signals['home_team'] = row['home_team']
            signals['away_team'] = row['away_team']
            signal_list.append(signals)

    signals_df = pd.DataFrame(signal_list)
    print(f"\nComputed signals for {len(signals_df)} matches")

    # Coverage
    for col in sorted(signals_df.columns):
        if col not in ['date', 'home_team', 'away_team']:
            n = signals_df[col].notna().sum()
            print(f"  {col}: {n}/{len(signals_df)}")

    # Merge with results
    merged = res.copy()
    signal_cols = [c for c in signals_df.columns
                   if c not in ['date', 'home_team', 'away_team']]
    for col in signal_cols:
        merged[col] = np.nan

    matched = 0
    for _, sig_row in signals_df.iterrows():
        mask = (
            (merged['date'] == sig_row['date']) &
            (merged['home_team'] == sig_row['home_team']) &
            (merged['away_team'] == sig_row['away_team'])
        )
        if mask.any():
            for col in signal_cols:
                if pd.notna(sig_row.get(col)):
                    merged.loc[mask, col] = sig_row[col]
            matched += 1

    print(f"\nMerged: {matched} matches with signals")

    # Save
    out_path = DATA_DIR / 'processed' / 'results_with_signals_v3.csv'
    merged.to_csv(out_path, index=False)
    print(f"Saved to {out_path}")
    n_total = merged['home_key_missing'].notna().sum()
    print(f"Total rows with signals: {n_total}")

    return merged


if __name__ == '__main__':
    build_all_signals()
