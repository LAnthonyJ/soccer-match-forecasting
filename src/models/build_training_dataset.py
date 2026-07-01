"""
Build unified training dataset from all odds data sources.
Filters for cross-validated high-quality matches only.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import glob
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'raw'
OUTPUT_DIR = Path(__file__).parent.parent.parent / 'data' / 'processed'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def implied_probs(h, d, a):
    """Convert decimal odds to normalized implied probabilities."""
    s = 1/h + 1/d + 1/a
    return np.array([1/h/s, 1/d/s, 1/a/s])


def load_oddsportal() -> pd.DataFrame:
    """Load OddsPortal dataset: 377K matches, Pinnacle + bet365 + Marathon."""
    print("\n=== OddsPortal (377K matches) ===")
    df = pd.read_csv(DATA_DIR / 'oddsportal' / 'matches.csv')

    # Standardize columns
    df = df.rename(columns={
        'home': 'home_team', 'away': 'away_team',
        'score_h': 'home_score', 'score_a': 'away_score',
    })
    df['date'] = pd.to_datetime(df['timestamp'], unit='s')
    df['source'] = 'oddsportal'

    # 3-way cross-validation filter
    has_all = df['p_o1'].notna() & df['b_o1'].notna() & df['m_o1'].notna()
    df_cv = df[has_all].copy()

    # Compute pairwise spreads
    spreads = []
    for _, row in df_cv.iterrows():
        p = implied_probs(row['p_o1'], row['p_oX'], row['p_o2'])
        b = implied_probs(row['b_o1'], row['b_oX'], row['b_o2'])
        m = implied_probs(row['m_o1'], row['m_oX'], row['m_o2'])
        spread = max(abs(p-b).max(), abs(p-m).max(), abs(b-m).max())
        spreads.append(spread)

    df_cv['bookmaker_spread'] = spreads
    df_cv['quality_tier'] = pd.cut(df_cv['bookmaker_spread'],
                                     bins=[0, 0.01, 0.02, 0.03, 1.0],
                                     labels=['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw'])

    # Use Pinnacle as ground truth, averaged with others for stability
    df_cv['odds_home_gt'] = (df_cv['p_o1'] + df_cv['b_o1'] + df_cv['m_o1']) / 3
    df_cv['odds_draw_gt'] = (df_cv['p_oX'] + df_cv['b_oX'] + df_cv['m_oX']) / 3
    df_cv['odds_away_gt'] = (df_cv['p_o2'] + df_cv['b_o2'] + df_cv['m_o2']) / 3

    # Implied probability from ground truth
    probs = []
    for _, row in df_cv.iterrows():
        probs.append(implied_probs(
            row['odds_home_gt'], row['odds_draw_gt'], row['odds_away_gt']
        ))
    probs = np.array(probs)
    df_cv['prob_h_gt'] = probs[:, 0]
    df_cv['prob_d_gt'] = probs[:, 1]
    df_cv['prob_a_gt'] = probs[:, 2]

    # Keep only needed columns
    cols = ['date', 'home_team', 'away_team', 'home_score', 'away_score',
            'odds_home_gt', 'odds_draw_gt', 'odds_away_gt',
            'prob_h_gt', 'prob_d_gt', 'prob_a_gt',
            'bookmaker_spread', 'quality_tier', 'source']
    df_out = df_cv[cols].copy()
    df_out['league'] = 'unknown'

    # Print stats
    for tier in ['T1_gold', 'T2_silver', 'T3_bronze']:
        n = (df_out['quality_tier'] == tier).sum()
        print(f"  {tier}: {n} matches")

    return df_out


def load_football_data() -> pd.DataFrame:
    """Load football-data.co.uk: 18K matches, Pinnacle closing + bet365 closing."""
    print("\n=== football-data.co.uk (Pinnacle closing) ===")
    files = sorted(glob.glob(str(DATA_DIR / 'fd_*.csv')))
    all_data = []

    for f in files:
        try:
            df = pd.read_csv(f, encoding='latin-1')
            # Check for Pinnacle closing + bet365 closing
            ps_cols = ['PSCH', 'PSCD', 'PSCA']
            b365_cols = ['B365CH', 'B365CD', 'B365CA']
            if not all(c in df.columns for c in ps_cols + b365_cols):
                continue

            valid = df.dropna(subset=ps_cols + b365_cols)
            if len(valid) < 10:
                continue

            # Compute spreads
            spreads = []
            for _, row in valid.iterrows():
                p = implied_probs(row['PSCH'], row['PSCD'], row['PSCA'])
                b = implied_probs(row['B365CH'], row['B365CD'], row['B365CA'])
                spreads.append(max(abs(p - b)))

            valid = valid.copy()
            valid['bookmaker_spread'] = spreads
            valid['odds_home_gt'] = (valid['PSCH'] + valid['B365CH']) / 2
            valid['odds_draw_gt'] = (valid['PSCD'] + valid['B365CD']) / 2
            valid['odds_away_gt'] = (valid['PSCA'] + valid['B365CA']) / 2

            # Convert date
            valid['date'] = pd.to_datetime(valid['Date'], dayfirst=True)
            valid['home_team'] = valid['HomeTeam']
            valid['away_team'] = valid['AwayTeam']
            valid['home_score'] = valid['FTHG']
            valid['away_score'] = valid['FTAG']
            valid['source'] = 'fd_co_uk'
            valid['quality_tier'] = pd.cut(valid['bookmaker_spread'],
                                            bins=[0, 0.01, 0.02, 0.03, 1.0],
                                            labels=['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw'])
            valid['league'] = valid.get('Div', 'unknown')

            probs = []
            for _, row in valid.iterrows():
                probs.append(implied_probs(
                    row['odds_home_gt'], row['odds_draw_gt'], row['odds_away_gt']
                ))
            probs = np.array(probs)
            valid['prob_h_gt'] = probs[:, 0]
            valid['prob_d_gt'] = probs[:, 1]
            valid['prob_a_gt'] = probs[:, 2]

            cols = ['date', 'home_team', 'away_team', 'home_score', 'away_score',
                    'odds_home_gt', 'odds_draw_gt', 'odds_away_gt',
                    'prob_h_gt', 'prob_d_gt', 'prob_a_gt',
                    'bookmaker_spread', 'quality_tier', 'source', 'league']
            all_data.append(valid[cols])
        except Exception as e:
            pass

    result = pd.concat(all_data, ignore_index=True)
    print(f"  Total: {len(result)} matches")
    for tier in ['T1_gold', 'T2_silver', 'T3_bronze']:
        n = (result['quality_tier'] == tier).sum()
        print(f"  {tier}: {n} matches")
    return result


def load_bets_soccer() -> pd.DataFrame:
    """Load bets_soccer: 47K matches from OddsPortal across 15 leagues."""
    print("\n=== bets_soccer (47K OddsPortal) ===")
    files = sorted(glob.glob(str(DATA_DIR / 'bets_soccer' / 'odds_*.csv')))
    all_data = []

    for f in files:
        try:
            df = pd.read_csv(f)
            # Map Portuguese columns to English
            col_map = {
                'time_casa': 'home_team', 'time_fora': 'away_team',
                'gols_casa': 'home_score', 'gols_fora': 'away_score',
                'odd_casa': 'odds_home_gt', 'odd_empate': 'odds_draw_gt',
                'odd_fora': 'odds_away_gt',
            }
            df = df.rename(columns=col_map)
            df = df.dropna(subset=['odds_home_gt', 'odds_draw_gt', 'odds_away_gt'])

            # Extract league from filename
            league = f.split('/')[-1].replace('odds_', '').replace('.csv', '').replace('_2015_2024', '').replace('_2015_2025', '')
            df['league'] = league
            df['source'] = 'bets_soccer'
            df['bookmaker_spread'] = 0.0  # Single source, no cross-val

            # Parse season to get approximate date
            # We'll use mid-season date as proxy
            df['date'] = pd.to_datetime('2019-01-01')  # Approximate

            # Ground truth probabilities
            probs = []
            for _, row in df.iterrows():
                probs.append(implied_probs(
                    row['odds_home_gt'], row['odds_draw_gt'], row['odds_away_gt']
                ))
            probs = np.array(probs)
            df['prob_h_gt'] = probs[:, 0]
            df['prob_d_gt'] = probs[:, 1]
            df['prob_a_gt'] = probs[:, 2]
            df['quality_tier'] = 'T4_raw'  # No cross-validation available

            cols = ['date', 'home_team', 'away_team', 'home_score', 'away_score',
                    'odds_home_gt', 'odds_draw_gt', 'odds_away_gt',
                    'prob_h_gt', 'prob_d_gt', 'prob_a_gt',
                    'bookmaker_spread', 'quality_tier', 'source', 'league']
            all_data.append(df[cols])
        except Exception as e:
            print(f"  Error in {f}: {e}")

    result = pd.concat(all_data, ignore_index=True)
    print(f"  Total: {len(result)} matches (T4 - no cross-validation)")
    return result


def build_unified_dataset():
    """Main function: build and save the unified training dataset."""
    print("=" * 60)
    print("BUILDING UNIFIED ODDS TRAINING DATASET")
    print("=" * 60)

    # Load all sources
    df_op = load_oddsportal()
    df_fd = load_football_data()
    df_bs = load_bets_soccer()

    # Combine
    df_all = pd.concat([df_op, df_fd, df_bs], ignore_index=True)

    # Add result column
    df_all['result'] = df_all.apply(
        lambda r: 'H' if r['home_score'] > r['away_score']
        else ('D' if r['home_score'] == r['away_score'] else 'A'), axis=1
    )

    # Summary
    print(f"\n{'='*60}")
    print(f"UNIFIED DATASET: {len(df_all)} total matches")
    print(f"{'='*60}")
    for tier in ['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw']:
        n = (df_all['quality_tier'] == tier).sum()
        print(f"  {tier}: {n} matches")

    print(f"\n  Date range: {df_all['date'].min()} to {df_all['date'].max()}")
    print(f"  Unique teams: {df_all['home_team'].nunique()}")
    print(f"  Sources: {df_all['source'].value_counts().to_dict()}")

    # Save
    output_path = OUTPUT_DIR / 'unified_odds_training.parquet'
    df_all.to_parquet(output_path, index=False)
    print(f"\nSaved to {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")

    return df_all


if __name__ == '__main__':
    build_unified_dataset()
