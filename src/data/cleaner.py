"""
Data cleaning and preprocessing for international football data.
"""
import pandas as pd
import numpy as np


MIN_YEAR = 2000  # Only use data from 2000 onwards (modern football era)
TEAM_NAME_FIXES = {
    # Standardize common name variations
    'United States': 'USA',
    'Korea Republic': 'South Korea',
    'Korea DPR': 'North Korea',
    'IR Iran': 'Iran',
    'Côte d\'Ivoire': 'Ivory Coast',
    'Czech Republic': 'Czechia',
}


def load_and_clean(raw_path: str) -> pd.DataFrame:
    """
    Load raw results.csv and clean/normalize the data.
    """
    df = pd.read_csv(raw_path)
    print(f"Raw data: {len(df)} rows")

    # Parse dates
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])

    # Standardize team names
    for old, new in TEAM_NAME_FIXES.items():
        df['home_team'] = df['home_team'].replace(old, new)
        df['away_team'] = df['away_team'].replace(old, new)

    # Filter to reasonable date range
    df = df[df['date'].dt.year >= MIN_YEAR].copy()

    # Ensure scores are numeric
    df['home_score'] = pd.to_numeric(df['home_score'], errors='coerce')
    df['away_score'] = pd.to_numeric(df['away_score'], errors='coerce')

    # Filter out matches with missing scores (unplayed) for training
    # but keep them for prediction pipeline
    df['is_played'] = df['home_score'].notna() & df['away_score'].notna()

    # Remove obvious data errors (scores > 30 are likely errors)
    bad_scores = (df['home_score'] > 30) | (df['away_score'] > 30)
    if bad_scores.any():
        print(f"Removing {bad_scores.sum()} rows with improbable scores (>30)")
        df = df[~bad_scores]

    print(f"Cleaned data: {len(df)} rows ({df['is_played'].sum()} played, "
          f"{len(df) - df['is_played'].sum()} unplayed)")
    print(f"Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    print(f"Teams: {df['home_team'].nunique()}")

    return df


def split_train_test_by_date(df: pd.DataFrame,
                              test_start: str = '2022-11-01',
                              test_end: str = '2022-12-31') -> tuple:
    """
    Split data into train and test sets by date.
    Test set = 2022 World Cup by default.
    """
    played = df[df['is_played']].copy()

    train = played[played['date'] < test_start].copy()
    test = played[(played['date'] >= test_start) & (played['date'] <= test_end)].copy()

    print(f"Train: {len(train)} matches ({train['date'].min().date()} to {train['date'].max().date()})")
    print(f"Test: {len(test)} matches ({test['date'].min().date()} to {test['date'].max().date()})")

    return train, test


def get_upcoming_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    """Extract upcoming (unplayed) World Cup 2026 fixtures."""
    fixtures = df[
        (~df['is_played']) &
        (df['tournament'] == 'FIFA World Cup')
    ].copy()
    return fixtures.sort_values('date')


def get_world_cup_2022(df: pd.DataFrame) -> pd.DataFrame:
    """Extract 2022 World Cup matches for testing."""
    wc2022 = df[
        (df['is_played']) &
        (df['tournament'] == 'FIFA World Cup') &
        (df['date'].dt.year == 2022)
    ].copy()
    return wc2022.sort_values('date')


def get_world_cup_matches(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Extract World Cup matches for a specific year."""
    return df[
        (df['is_played']) &
        (df['tournament'] == 'FIFA World Cup') &
        (df['date'].dt.year == year)
    ].copy().sort_values('date')
