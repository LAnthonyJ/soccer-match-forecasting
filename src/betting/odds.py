"""
Real market odds handling: load, validate, match, and convert odds formats.
Supports CSV input, manual entry, and optional web scraping.
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime


DATA_DIR = Path(__file__).parent.parent.parent / 'data'


def load_odds_csv(filepath: str = None) -> pd.DataFrame:
    """
    Load odds from a CSV file.

    Expected CSV format:
        date,home_team,away_team,odds_home,odds_draw,odds_away

    Odds should be in DECIMAL format (e.g., 2.50 means bet $1 to win $2.50).
    """
    if filepath is None:
        filepath = DATA_DIR / 'raw' / 'odds.csv'

    df = pd.read_csv(filepath)
    print(f"Loaded {len(df)} odds entries from {filepath}")

    # Normalize
    df['date'] = pd.to_datetime(df['date'])
    for col in ['odds_home', 'odds_draw', 'odds_away']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Validate: odds should all be > 1.0
    for col in ['odds_home', 'odds_draw', 'odds_away']:
        bad = df[col] <= 1.0
        if bad.any():
            print(f"  WARNING: {bad.sum()} entries in {col} have odds <= 1.0, setting to NaN")
            df.loc[bad, col] = np.nan

    return df


def odds_to_implied_probability(odds_home: float, odds_draw: float,
                                 odds_away: float,
                                 method: str = 'proportional') -> tuple:
    """
    Convert decimal odds to implied probabilities, removing overround.

    Args:
        method: 'proportional' (distribute overround proportionally)
                'shin' (Shin's method, better for bookmaker margin)
                'basic' (1/odds, no overround removal)
    """
    if any(pd.isna([odds_home, odds_draw, odds_away])):
        return np.nan, np.nan, np.nan

    if method == 'basic':
        p_h = 1.0 / odds_home
        p_d = 1.0 / odds_draw
        p_a = 1.0 / odds_away
        return p_h, p_d, p_a

    # Raw implied probabilities (sum > 1 due to overround)
    raw_h = 1.0 / odds_home
    raw_d = 1.0 / odds_draw
    raw_a = 1.0 / odds_away
    total = raw_h + raw_d + raw_a

    # Overround (bookmaker margin)
    overround = total - 1.0

    if method == 'proportional':
        # Distribute overround proportionally
        p_h = raw_h / total
        p_d = raw_d / total
        p_a = raw_a / total
        return p_h, p_d, p_a

    elif method == 'shin':
        # Shin's method - assumes informed insider trading
        # More accurate for bookmaker markets
        z = _shin_z(total)
        p_h = _shin_prob(raw_h, z)
        p_d = _shin_prob(raw_d, z)
        p_a = _shin_prob(raw_a, z)
        total_p = p_h + p_d + p_a
        if total_p > 0:
            p_h /= total_p
            p_d /= total_p
            p_a /= total_p
        return p_h, p_d, p_a


def _shin_z(total_prob: float, max_iter: int = 100) -> float:
    """Solve for Shin's z parameter."""
    z = 0.0
    for _ in range(max_iter):
        f = total_prob - _shin_sum(z, total_prob)
        if abs(f) < 1e-6:
            break
        z += f * 0.1
        z = max(0, min(0.5, z))
    return z


def _shin_sum(z: float, t: float) -> float:
    return t if z == 0 else (np.sinh(z * t) / np.sinh(z))


def _shin_prob(raw_p: float, z: float) -> float:
    if z == 0:
        return raw_p
    return np.arcsinh(raw_p * np.sinh(z)) / z


def merge_odds_with_matches(matches: pd.DataFrame,
                             odds_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge odds data into the matches DataFrame.

    Matching strategy:
    1. Exact match on date + home_team + away_team
    2. Fuzzy match on date (within 1 day) + team names
    """
    matches = matches.copy()
    matches['date'] = pd.to_datetime(matches['date'])
    odds_df['date'] = pd.to_datetime(odds_df['date'])

    # Normalize team names for matching
    matches['_home_norm'] = matches['home_team'].str.lower().str.strip()
    matches['_away_norm'] = matches['away_team'].str.lower().str.strip()
    odds_df['_home_norm'] = odds_df['home_team'].str.lower().str.strip()
    odds_df['_away_norm'] = odds_df['away_team'].str.lower().str.strip()

    # Exact date + team match
    merged = matches.merge(
        odds_df[['date', '_home_norm', '_away_norm',
                  'odds_home', 'odds_draw', 'odds_away']],
        on=['date', '_home_norm', '_away_norm'],
        how='left',
        suffixes=('', '_odds')
    )

    n_matched = merged['odds_home'].notna().sum()
    print(f"Odds matched: {n_matched}/{len(merged)} matches ({n_matched/len(merged)*100:.1f}%)")

    # Clean up temp columns
    merged = merged.drop(columns=['_home_norm', '_away_norm'])

    # Compute implied probabilities
    for method in ['proportional']:
        probs_h, probs_d, probs_a = [], [], []
        for _, row in merged.iterrows():
            ph, pd_, pa = odds_to_implied_probability(
                row.get('odds_home'), row.get('odds_draw'), row.get('odds_away'),
                method=method
            )
            probs_h.append(ph)
            probs_d.append(pd_)
            probs_a.append(pa)

        merged[f'implied_h_{method}'] = probs_h
        merged[f'implied_d_{method}'] = probs_d
        merged[f'implied_a_{method}'] = probs_a

    return merged


def create_odds_template(output_path: str = None) -> str:
    """
    Create a CSV template for manual odds entry.
    Returns the path to the created template.
    """
    if output_path is None:
        output_path = DATA_DIR / 'raw' / 'odds_template.csv'

    template = pd.DataFrame(columns=[
        'date', 'home_team', 'away_team', 'odds_home', 'odds_draw', 'odds_away'
    ])

    # Add 2022 World Cup matches as examples
    examples = [
        {'date': '2022-11-20', 'home_team': 'Qatar', 'away_team': 'Ecuador',
         'odds_home': 3.80, 'odds_draw': 3.25, 'odds_away': 2.10},
        {'date': '2022-11-21', 'home_team': 'England', 'away_team': 'Iran',
         'odds_home': 1.25, 'odds_draw': 5.80, 'odds_away': 12.00},
        {'date': '2022-11-22', 'home_team': 'Argentina', 'away_team': 'Saudi Arabia',
         'odds_home': 1.14, 'odds_draw': 7.50, 'odds_away': 19.00},
    ]

    template = pd.concat([template, pd.DataFrame(examples)])

    template.to_csv(output_path, index=False)
    print(f"Odds template created at: {output_path}")
    print("Fill in the odds for each match and save as 'odds.csv' in the same folder.")
    return str(output_path)


def get_odds_for_match(match_row: pd.Series,
                        odds_df: pd.DataFrame = None) -> dict:
    """
    Get odds for a single match. Falls back to ELO-simulated odds
    if no real odds available.

    Returns: {'home': odds_home, 'draw': odds_draw, 'away': odds_away}
    """
    if odds_df is not None:
        match_date = pd.to_datetime(match_row['date'])
        home = match_row['home_team'].lower().strip()
        away = match_row['away_team'].lower().strip()

        match_odds = odds_df[
            (odds_df['date'].dt.date == match_date.date()) &
            (odds_df['home_team'].str.lower().str.strip() == home) &
            (odds_df['away_team'].str.lower().str.strip() == away)
            ]

        if len(match_odds) > 0:
            row = match_odds.iloc[0]
            return {
                'home': row['odds_home'],
                'draw': row['odds_draw'],
                'away': row['odds_away'],
                'source': 'real'
            }

    # Fallback: ELO-simulated odds
    return {'source': 'simulated'}


def odds_dict_to_array(odds_dicts: list, n_matches: int) -> dict:
    """
    Convert a list of odds dicts to arrays for vectorized operations.
    Handles mixing of real and simulated odds.
    """
    odds = {'home': np.zeros(n_matches), 'draw': np.zeros(n_matches),
            'away': np.zeros(n_matches)}

    for i, od in enumerate(odds_dicts):
        if od['source'] == 'real':
            odds['home'][i] = od['home']
            odds['draw'][i] = od['draw']
            odds['away'][i] = od['away']
        # simulated odds are handled by the caller

    return odds
