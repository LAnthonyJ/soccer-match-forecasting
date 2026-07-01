"""
Build filtered training dataset for 15 target leagues only.
"""
import pandas as pd, numpy as np, pickle, glob, warnings
warnings.filterwarnings('ignore')
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'raw'
OUTPUT_DIR = Path(__file__).parent.parent.parent / 'data' / 'processed'

# Target league IDs (from OddsPortal)
with open(DATA_DIR / 'target_liga_ids.pkl', 'rb') as f:
    TARGET_IDS = pickle.load(f)

# Add missing China IDs manually
CHINA_IDS = {1094, 1100, 1099, 1129, 1102, 1108, 1134, 1115, 1120}
TARGET_IDS.update(CHINA_IDS)

# football-data.co.uk target codes
TARGET_FD = {'E0', 'SP1', 'D1', 'I1', 'F1', 'N1', 'B1', 'P1'}

# bets_soccer target files
TARGET_BS = {
    'argentina', 'belgica', 'brasileirao', 'bundesliga', 'eredivisie',
    'italia_seriaA', 'laliga', 'liga_portugal', 'ligue1', 'mls', 'premier_league'
}

def implied(h, d, a):
    s = 1/h + 1/d + 1/a
    return np.array([1/h/s, 1/d/s, 1/a/s])

def load_oddsportal_filtered():
    """Load OddsPortal data, filtered to target leagues only."""
    print("\n=== OddsPortal (filtered) ===")
    df = pd.read_csv(DATA_DIR / 'oddsportal' / 'matches.csv')
    df = df[df['liga_id'].isin(TARGET_IDS)].copy()
    print(f"  {len(df)} matches in target leagues")

    df = df.rename(columns={'home': 'home_team', 'away': 'away_team',
                             'score_h': 'home_score', 'score_a': 'away_score'})
    df['date'] = pd.to_datetime(df['timestamp'], unit='s')
    df['source'] = 'oddsportal'

    has_all = df['p_o1'].notna() & df['b_o1'].notna() & df['m_o1'].notna()
    df_cv = df[has_all].copy()

    spreads = []
    for _, row in df_cv.iterrows():
        p = implied(row['p_o1'], row['p_oX'], row['p_o2'])
        b = implied(row['b_o1'], row['b_oX'], row['b_o2'])
        m = implied(row['m_o1'], row['m_oX'], row['m_o2'])
        spreads.append(max(abs(p-b).max(), abs(p-m).max(), abs(b-m).max()))

    df_cv['bookmaker_spread'] = spreads
    df_cv['quality_tier'] = pd.cut(df_cv['bookmaker_spread'],
                                     bins=[0, 0.01, 0.02, 0.03, 1.0],
                                     labels=['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw'])
    df_cv['odds_home_gt'] = (df_cv['p_o1'] + df_cv['b_o1'] + df_cv['m_o1']) / 3
    df_cv['odds_draw_gt'] = (df_cv['p_oX'] + df_cv['b_oX'] + df_cv['m_oX']) / 3
    df_cv['odds_away_gt'] = (df_cv['p_o2'] + df_cv['b_o2'] + df_cv['m_o2']) / 3

    probs = np.array([implied(r['odds_home_gt'], r['odds_draw_gt'], r['odds_away_gt'])
                       for _, r in df_cv.iterrows()])
    df_cv['prob_h_gt'] = probs[:, 0]
    df_cv['prob_d_gt'] = probs[:, 1]
    df_cv['prob_a_gt'] = probs[:, 2]

    cols = ['date', 'home_team', 'away_team', 'home_score', 'away_score',
            'odds_home_gt', 'odds_draw_gt', 'odds_away_gt',
            'prob_h_gt', 'prob_d_gt', 'prob_a_gt',
            'bookmaker_spread', 'quality_tier', 'source']
    df_out = df_cv[cols].copy()
    df_out['league'] = 'oddsportal_target'

    for tier in ['T1_gold', 'T2_silver', 'T3_bronze']:
        n = (df_out['quality_tier'] == tier).sum()
        print(f"  {tier}: {n} matches")
    return df_out


def load_fd_filtered():
    """Load football-data.co.uk, filtered to target leagues."""
    print("\n=== football-data.co.uk (target leagues) ===")
    files = sorted(glob.glob(str(DATA_DIR / 'fd_*.csv')))
    all_data = []
    for f in files:
        try:
            df = pd.read_csv(f, encoding='latin-1')
            if 'Div' not in df.columns: continue
            df = df[df['Div'].isin(TARGET_FD)]
            if len(df) == 0: continue

            ps_cols = ['PSCH', 'PSCD', 'PSCA']
            b365_cols = ['B365CH', 'B365CD', 'B365CA']
            if not all(c in df.columns for c in ps_cols + b365_cols): continue
            valid = df.dropna(subset=ps_cols + b365_cols)
            if len(valid) < 10: continue

            spreads = []
            for _, row in valid.iterrows():
                p = implied(row['PSCH'], row['PSCD'], row['PSCA'])
                b = implied(row['B365CH'], row['B365CD'], row['B365CA'])
                spreads.append(max(abs(p - b)))
            valid = valid.copy()
            valid['bookmaker_spread'] = spreads
            valid['odds_home_gt'] = (valid['PSCH'] + valid['B365CH']) / 2
            valid['odds_draw_gt'] = (valid['PSCD'] + valid['B365CD']) / 2
            valid['odds_away_gt'] = (valid['PSCA'] + valid['B365CA']) / 2
            valid['date'] = pd.to_datetime(valid['Date'], dayfirst=True)
            valid['home_team'] = valid['HomeTeam']
            valid['away_team'] = valid['AwayTeam']
            valid['home_score'] = valid['FTHG']
            valid['away_score'] = valid['FTAG']
            valid['source'] = 'fd_target'
            valid['quality_tier'] = pd.cut(valid['bookmaker_spread'],
                                            bins=[0, 0.01, 0.02, 0.03, 1.0],
                                            labels=['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw'])
            valid['league'] = valid['Div']
            probs = np.array([implied(r['odds_home_gt'], r['odds_draw_gt'], r['odds_away_gt'])
                               for _, r in valid.iterrows()])
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


def load_bs_filtered():
    """Load bets_soccer, filtered to target leagues."""
    print("\n=== bets_soccer (target leagues) ===")
    files = sorted(glob.glob(str(DATA_DIR / 'bets_soccer' / 'odds_*.csv')))
    all_data = []
    for f in files:
        league_name = f.split('\\')[-1].replace('odds_', '').replace('.csv', '')
        league_name = league_name.replace('_2015_2024', '').replace('_2015_2025', '')
        if league_name not in TARGET_BS:
            continue

        df = pd.read_csv(f)
        col_map = {'time_casa': 'home_team', 'time_fora': 'away_team',
                   'gols_casa': 'home_score', 'gols_fora': 'away_score',
                   'odd_casa': 'odds_home_gt', 'odd_empate': 'odds_draw_gt',
                   'odd_fora': 'odds_away_gt'}
        df = df.rename(columns=col_map)
        df = df.dropna(subset=['odds_home_gt', 'odds_draw_gt', 'odds_away_gt'])
        df['league'] = league_name
        df['source'] = 'bets_soccer_target'
        df['bookmaker_spread'] = 0.0
        df['date'] = pd.to_datetime('2019-01-01')
        probs = np.array([implied(r['odds_home_gt'], r['odds_draw_gt'], r['odds_away_gt'])
                           for _, r in df.iterrows()])
        df['prob_h_gt'] = probs[:, 0]
        df['prob_d_gt'] = probs[:, 1]
        df['prob_a_gt'] = probs[:, 2]
        df['quality_tier'] = 'T4_raw'

        cols = ['date', 'home_team', 'away_team', 'home_score', 'away_score',
                'odds_home_gt', 'odds_draw_gt', 'odds_away_gt',
                'prob_h_gt', 'prob_d_gt', 'prob_a_gt',
                'bookmaker_spread', 'quality_tier', 'source', 'league']
        all_data.append(df[cols])

    result = pd.concat(all_data, ignore_index=True)
    print(f"  Total: {len(result)} matches (T4 - no cross-validation)")
    return result


def build():
    print("=" * 60)
    print("BUILDING TARGET-LEAGUE TRAINING DATASET")
    print("=" * 60)

    df_op = load_oddsportal_filtered()
    df_fd = load_fd_filtered()
    df_bs = load_bs_filtered()

    df_all = pd.concat([df_op, df_fd, df_bs], ignore_index=True)
    df_all['result'] = df_all.apply(
        lambda r: 'H' if r['home_score'] > r['away_score']
        else ('D' if r['home_score'] == r['away_score'] else 'A'), axis=1)

    print(f"\n{'='*60}")
    print(f"TARGET-LEAGUE DATASET: {len(df_all)} total matches")
    print(f"{'='*60}")
    for tier in ['T1_gold', 'T2_silver', 'T3_bronze', 'T4_raw']:
        n = (df_all['quality_tier'] == tier).sum()
        print(f"  {tier}: {n} matches")

    train_set = df_all[df_all['quality_tier'].isin(['T1_gold', 'T2_silver'])].copy()
    print(f"\n  Training set (T1+T2): {len(train_set)} matches")
    print(f"  Date range: {train_set['date'].min()} to {train_set['date'].max()}")
    print(f"  Sources: {train_set['source'].value_counts().to_dict()}")

    output_path = OUTPUT_DIR / 'target_league_training.parquet'
    df_all.to_parquet(output_path, index=False)
    print(f"\nSaved to {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    return df_all

if __name__ == '__main__':
    build()
