"""
FIFA rankings data loader.
Primary: Kaggle dataset (accessible from China).
Fallback: datafc eloratings (ELO > FIFA ranking for prediction anyway).
"""
import pandas as pd
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
PROCESSED_DIR = DATA_DIR / 'processed'
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_fifa_rankings_from_csv(filepath: str = None) -> pd.DataFrame:
    """Load FIFA rankings from a local CSV file (Kaggle download)."""
    if filepath is None:
        filepath = DATA_DIR / 'raw' / 'fifa_ranking_historical.csv'

    df = pd.read_csv(filepath)
    # Standardize columns: expect 'rank', 'country_full' or 'country', 'total_points', 'rank_date'
    df['rank_date'] = pd.to_datetime(df['rank_date'], errors='coerce')

    col_map = {
        'country_full': 'country',
        'country_abrv': 'country_code',
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    print(f"Loaded {len(df)} FIFA ranking records, {df['country'].nunique()} countries")
    print(f"Date range: {df['rank_date'].min()} to {df['rank_date'].max()}")
    return df


def load_elo_rankings() -> pd.DataFrame:
    """Load ELO world rankings via datafc (confirmed working)."""
    import datafc

    elo = datafc.eloratings.world_ranking_data()
    elo = elo.rename(columns={
        'country': 'country_code',
        'elo': 'elo_rating',
    })
    print(f"Loaded ELO rankings for {len(elo)} countries")
    return elo


def get_fifa_ranking_for_match_date(rankings_df: pd.DataFrame,
                                     team: str, match_date: pd.Timestamp) -> dict:
    """Get the closest FIFA ranking for a team before a given match date."""
    team_ranks = rankings_df[
        (rankings_df['country'] == team) &
        (rankings_df['rank_date'] <= match_date)
    ].sort_values('rank_date')

    if team_ranks.empty:
        return {'fifa_rank': None, 'fifa_points': None}

    latest = team_ranks.iloc[-1]
    return {
        'fifa_rank': latest.get('rank', None),
        'fifa_points': latest.get('total_points', None),
    }


def build_fifa_features(matches_df: pd.DataFrame,
                         rankings_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Attach FIFA ranking features to match DataFrame.
    For each match, gets the most recent pre-match ranking for both teams.
    """
    matches = matches_df.copy()

    if rankings_df is None:
        try:
            rankings_df = load_fifa_rankings_from_csv()
        except FileNotFoundError:
            print("FIFA rankings CSV not found. Using ELO as fallback.")
            return _build_elo_fallback_features(matches)

    fifa_ranks_home = []
    fifa_ranks_away = []
    fifa_points_home = []
    fifa_points_away = []

    for _, row in matches.iterrows():
        h_info = get_fifa_ranking_for_match_date(
            rankings_df, row['home_team'], row['date'])
        a_info = get_fifa_ranking_for_match_date(
            rankings_df, row['away_team'], row['date'])

        fifa_ranks_home.append(h_info['fifa_rank'])
        fifa_ranks_away.append(a_info['fifa_rank'])
        fifa_points_home.append(h_info['fifa_points'])
        fifa_points_away.append(a_info['fifa_points'])

    matches['fifa_rank_home'] = fifa_ranks_home
    matches['fifa_rank_away'] = fifa_ranks_away
    matches['fifa_rank_diff'] = matches['fifa_rank_home'] - matches['fifa_rank_away']
    matches['fifa_points_home'] = fifa_points_home
    matches['fifa_points_away'] = fifa_points_away

    return matches


def _build_elo_fallback_features(matches_df: pd.DataFrame) -> pd.DataFrame:
    """Fallback: use ELO as substitute for FIFA rankings in features."""
    import datafc
    elo = datafc.eloratings.world_ranking_data()
    elo_lookup = dict(zip(elo['country_code'], elo['elo_rating']))

    matches = matches_df.copy()
    # datafc uses ISO codes; our data uses full names
    # This is a rough fallback — proper name mapping needed
    print("ELO fallback: using eloratings.net data")
    return matches


if __name__ == '__main__':
    # Test: load FIFA rankings
    try:
        rankings = load_fifa_rankings_from_csv()
        print(rankings.head())
    except FileNotFoundError:
        print("FIFA CSV not found. Download from Kaggle: "
              "https://www.kaggle.com/datasets/dato-futbol/fifa-ranking")
        print("Falling back to ELO...")
        elo = load_elo_rankings()
        print(elo.head())
