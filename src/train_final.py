"""
Final training with correct strategy:
  - ALL data 2000-2026, random 85/15 train/val split
  - Strong time decay: half-life=2 years → recent matches dominate
  - 199 OddsPortal matches distributed into train+val
  - Market comparison: val portion of OddsPortal matches
"""
import sys, os, glob, ast, pickle
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.metrics import log_loss

def weighted_log_loss(y_true, y_proba, sample_weight=None):
    """Time-weighted log loss. Higher weight = more important."""
    if sample_weight is None:
        return log_loss(y_true, y_proba)
    # sklearn doesn't support sample_weight in log_loss, so manual compute
    eps = 1e-15
    y_proba = np.clip(y_proba, eps, 1 - eps)
    losses = -(y_true * np.log(y_proba[:, 1]) + (1 - y_true) * np.log(y_proba[:, 0]))
    return np.average(losses, weights=sample_weight)

def compute_val_weights(val_dates, ref_date, half_life=2):
    """Time decay weights for validation — same formula as training."""
    years_ago = (ref_date - pd.to_datetime(val_dates)).dt.days / 365.25
    w = np.exp(-np.log(2) * years_ago.values / half_life)
    return np.clip(w, 0.10, 1.0)

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import (
    train_elo_baseline, predict_elo_baseline,
    train_xgboost, train_lightgbm,
    Ensemble, prepare_features, save_models, load_models,
)

DATA_DIR = Path(__file__).parent.parent / 'data'


def load_odds():
    name_fix = {
        'Bosnia & Herzegovina': 'Bosnia and Herzegovina',
        'D.R. Congo': 'DR Congo', 'Guinea Bissau': 'Guinea-Bissau',
        'USA': 'United States',
    }
    odds_all = []
    for f in glob.glob(str(DATA_DIR / 'raw' / 'oddsportal_*.csv')):
        odf = pd.read_csv(f)
        odf = odf.rename(columns={'match_date': 'date'})
        odds_all.append(odf)
    odds_all = pd.concat(odds_all, ignore_index=True)
    odds_all['date'] = pd.to_datetime(odds_all['date'].str.replace(' UTC', '')).dt.date
    odds_all['home_team'] = odds_all['home_team'].replace(name_fix)
    odds_all['away_team'] = odds_all['away_team'].replace(name_fix)

    def parse_odds(row):
        try:
            mkts = ast.literal_eval(row['1x2_market'])
            return pd.Series({
                'odds_home': np.mean([float(m['1']) for m in mkts]),
                'odds_draw': np.mean([float(m['X']) for m in mkts]),
                'odds_away': np.mean([float(m['2']) for m in mkts]),
            })
        except Exception:
            return pd.Series({'odds_home': np.nan, 'odds_draw': np.nan, 'odds_away': np.nan})
    odds_all = pd.concat([odds_all, odds_all.apply(parse_odds, axis=1)], axis=1)
    return odds_all


def match_odds_to_results(odds_all):
    """Fuzzy-merge odds with results.csv."""
    res = pd.read_csv(DATA_DIR / 'raw' / 'results.csv')
    res['date'] = pd.to_datetime(res['date']).dt.date

    odds_sub = odds_all[['date', 'home_team', 'away_team',
                          'odds_home', 'odds_draw', 'odds_away']].dropna()

    # Forward
    mf = res.merge(odds_sub, on=['date', 'home_team', 'away_team'], how='inner')
    # Reverse
    odds_rev = odds_sub.rename(columns={
        'home_team': 'away_team', 'away_team': 'home_team',
        'odds_home': 'oh_rev', 'odds_away': 'oa_rev',
    })
    mr = res.merge(odds_rev, on=['date', 'home_team', 'away_team'], how='inner')
    if len(mr) > 0:
        mr['odds_home'] = mr['oa_rev']
        mr['odds_away'] = mr['oh_rev']

    combined = pd.concat([mf, mr], ignore_index=True)
    combined = combined.drop_duplicates(subset=['date', 'home_team', 'away_team'])
    combined = combined[combined['home_score'].notna()]

    # ±1 day fuzzy
    from datetime import timedelta
    matched_keys = set(zip(combined['date'], combined['home_team'], combined['away_team']))
    fuzzy = []
    for _, odd in odds_sub.iterrows():
        odd_date = odd['date']
        if (odd_date, odd['home_team'], odd['away_team']) in matched_keys:
            continue
        if (odd_date, odd['away_team'], odd['home_team']) in matched_keys:
            continue
        for delta in [1, -1]:
            sd = odd_date + timedelta(days=delta)
            m = res[(res['date'] == sd) & (res['home_team'] == odd['home_team']) &
                    (res['away_team'] == odd['away_team'])]
            if len(m) > 0:
                r = m.iloc[0]
                fuzzy.append({
                    'date': sd, 'home_team': odd['home_team'], 'away_team': odd['away_team'],
                    'home_score': r['home_score'], 'away_score': r['away_score'],
                    'tournament': r['tournament'], 'neutral': r['neutral'],
                    'odds_home': odd['odds_home'], 'odds_draw': odd['odds_draw'],
                    'odds_away': odd['odds_away'],
                })
                break
            m = res[(res['date'] == sd) & (res['home_team'] == odd['away_team']) &
                    (res['away_team'] == odd['home_team'])]
            if len(m) > 0:
                r = m.iloc[0]
                fuzzy.append({
                    'date': sd, 'home_team': odd['away_team'], 'away_team': odd['home_team'],
                    'home_score': r['home_score'], 'away_score': r['away_score'],
                    'tournament': r['tournament'], 'neutral': r['neutral'],
                    'odds_home': odd['odds_away'], 'odds_draw': odd['odds_draw'],
                    'odds_away': odd['odds_home'],
                })
                break

    if fuzzy:
        combined = pd.concat([combined, pd.DataFrame(fuzzy)], ignore_index=True)

    result = combined.drop_duplicates(subset=['date', 'home_team', 'away_team'])
    result = result[result['home_score'].notna()]
    result['date'] = pd.to_datetime(result['date'])
    return result


def run():
    print("=" * 60)
    print("FINAL TRAINING — All eras, time decay, random split")
    print("=" * 60)

    # 1. Load odds, identify real-odds matches
    odds_all = load_odds()
    odds_matches = match_odds_to_results(odds_all)
    print(f"\nOddsPortal matches found: {len(odds_matches)}")

    # 2. Build features on ALL data
    print("\nBuilding features on ALL data...")
    df = load_and_clean(str(DATA_DIR / 'raw' / 'results.csv'))
    all_data = df[df['is_played']].copy()
    full_df = build_all_features(all_data)

    # Merge event features
    evt = pd.read_csv(DATA_DIR / 'processed' / 'results_with_event_features.csv')
    evt['date'] = pd.to_datetime(evt['date'])
    evt_cols = [c for c in evt.columns if c.startswith('event_')]
    for col in evt_cols:
        if col not in full_df.columns:
            full_df[col] = np.nan
    for _, er in evt.iterrows():
        m = ((full_df['date'] == er['date']) &
             (full_df['home_team'] == er['home_team']) &
             (full_df['away_team'] == er['away_team']))
        if m.any():
            for col in evt_cols:
                if pd.notna(er.get(col)):
                    full_df.loc[m, col] = er[col]

    # 3. Mark which rows have real odds
    odds_keys = set()
    for _, r in odds_matches.iterrows():
        odds_keys.add((pd.Timestamp(r['date']), r['home_team'], r['away_team']))
    full_df['has_odds'] = full_df.apply(
        lambda row: (row['date'], row['home_team'], row['away_team']) in odds_keys,
        axis=1,
    )
    n_odds = full_df['has_odds'].sum()
    print(f"Rows with real odds in feature matrix: {n_odds}")

    # Merge tactical matchup v1
    matchup_path = DATA_DIR / 'processed' / 'results_with_matchup.csv'
    if matchup_path.exists():
        mup = pd.read_csv(matchup_path)
        mup['date'] = pd.to_datetime(mup['date'])
        for col in ['tactical_matchup_adv', 'tactical_matchup_n']:
            if col not in full_df.columns: full_df[col] = np.nan
        for _, mr in mup.iterrows():
            m = ((full_df['date'] == mr['date']) &
                 (full_df['home_team'] == mr['home_team']) &
                 (full_df['away_team'] == mr['away_team']))
            if m.any():
                for col in ['tactical_matchup_adv', 'tactical_matchup_n']:
                    if pd.notna(mr.get(col)): full_df.loc[m, col] = mr[col]
        print(f"Tactical matchup v1: {full_df['tactical_matchup_adv'].notna().sum()} matches")

    # Merge style matchup v2 (continuous differences)
    matchup2_path = DATA_DIR / 'processed' / 'results_with_matchup_v2.csv'
    if matchup2_path.exists():
        mup2 = pd.read_csv(matchup2_path)
        mup2['date'] = pd.to_datetime(mup2['date'])
        m2_cols = ['matchup_ppda_diff', 'matchup_pass_diff', 'matchup_speed_diff',
                   'matchup_defline_diff', 'matchup_pressure_diff', 'matchup_progpass_diff']
        for col in m2_cols:
            if col not in full_df.columns: full_df[col] = np.nan
        for _, mr in mup2.iterrows():
            m = ((full_df['date'] == mr['date']) &
                 (full_df['home_team'] == mr['home_team']) &
                 (full_df['away_team'] == mr['away_team']))
            if m.any():
                for col in m2_cols:
                    if pd.notna(mr.get(col)): full_df.loc[m, col] = mr[col]
        print(f"Style matchup v2: {full_df['matchup_ppda_diff'].notna().sum()} matches")

    # 4. Random 85/15 split on ALL data
    np.random.seed(42)
    mask = np.random.rand(len(full_df)) < 0.85
    train_feat = full_df[mask].copy()
    val_feat = full_df[~mask].copy()

    # Attach odds
    odds_lookup = odds_matches[['date', 'home_team', 'away_team',
                                 'odds_home', 'odds_draw', 'odds_away']]
    for side, side_df in [('train', train_feat), ('val', val_feat)]:
        side_df_merged = side_df.merge(odds_lookup, on=['date', 'home_team', 'away_team'],
                                        how='left')
        for col in ['odds_home', 'odds_draw', 'odds_away']:
            side_df[col] = side_df_merged[col].values

    n_odds_train = train_feat['odds_home'].notna().sum()
    n_odds_val = val_feat['odds_home'].notna().sum()

    print(f"\nSplit: Train={len(train_feat)} ({n_odds_train} with odds), "
          f"Val={len(val_feat)} ({n_odds_val} with odds)")
    print(f"Train date range: {train_feat['date'].min().date()} to {train_feat['date'].max().date()}")
    print(f"Val date range:   {val_feat['date'].min().date()} to {val_feat['date'].max().date()}")

    # Verify no overlap
    train_keys = set(zip(train_feat['date'], train_feat['home_team'], train_feat['away_team']))
    val_keys = set(zip(val_feat['date'], val_feat['home_team'], val_feat['away_team']))
    print(f"Leak: {len(train_keys & val_keys)}")

    # 5. Train
    print("\n" + "=" * 60)
    print("TRAINING")
    print("=" * 60)

    print("\n[1] ELO...")
    elo_model = train_elo_baseline(train_feat)
    val_feat = predict_elo_baseline(val_feat, elo_model)

    print("\n[2] Feature matrices...")
    X_train, yh, yd, ya, cols, _ = prepare_features(train_feat)
    X_val, yvh, yvd, yva, _, _ = prepare_features(val_feat)

    # Time decay: half-life=2 years, floor=0.10
    train_dates = pd.to_datetime(train_feat['date'])
    ref_date = train_dates.max()
    years_ago = (ref_date - train_dates).dt.days / 365.25
    train_weights = np.clip(np.exp(-np.log(2) * years_ago.values / 2), 0.10, 1.0)
    print(f"  Time weights: min={train_weights.min():.2f}, mean={train_weights.mean():.2f}, "
          f"2020+ mean={train_weights[train_dates.dt.year >= 2020].mean():.2f}")

    # Tournament importance weighting — large spread
    BASE_TOURNAMENT = {
        'FIFA World Cup': 5.0,
        'UEFA Euro': 3.5, 'Copa América': 3.5,
        'African Cup of Nations': 3.5, 'AFC Asian Cup': 3.5,
        'FIFA World Cup qualification': 2.5,
        'UEFA Euro qualification': 2.0, 'African Cup of Nations qualification': 2.0,
        'AFC Asian Cup qualification': 2.0, 'CONCACAF Gold Cup qualification': 2.0,
        'Friendly': 1.0,
        'UEFA Nations League': 1.5, 'CONCACAF Nations League': 1.5,
    }
    tournament_mult = train_feat['tournament'].map(BASE_TOURNAMENT).fillna(1.0).values
    train_weights = train_weights * tournament_mult
    print(f"  Tournament weights: WC=5.0x, continental=3.5x, WCQ=2.5x, "
          f"continental_qual=2.0x, nations=1.5x, friendly=1.0x")

    print("\n[3] XGBoost...")
    xgb_models, _xgb_test = train_xgboost(
        X_train, yh, yd, ya, X_val, yvh, yvd, yva,
        X_val, yvh, yvd, yva,  # placeholder — we evaluate manually
        sample_weights=train_weights,
    )

    print("\n[4] LightGBM...")
    lgb_models, _lgb_test = train_lightgbm(
        X_train, yh, yd, ya, X_val, yvh, yvd, yva,
        X_val, yvh, yvd, yva,
        sample_weights=train_weights,
    )

    print("\n[5] Ensemble...")
    elo_val_preds = {
        'home': val_feat['prob_h_elo'].values,
        'draw': val_feat['prob_d_elo'].values,
        'away': val_feat['prob_a_elo'].values,
    }
    xgb_val_preds = {o: xgb_models[o].predict_proba(X_val)[:, 1]
                     for o in ['home', 'draw', 'away']}
    lgb_val_preds = {o: lgb_models[o].predict_proba(X_val)[:, 1]
                     for o in ['home', 'draw', 'away']}

    ensemble = Ensemble()
    ensemble.fit(
        {'elo': elo_val_preds, 'xgb': xgb_val_preds, 'lgb': lgb_val_preds},
        {'home': yvh, 'draw': yvd, 'away': yva},
    )

    # 6. Evaluate on VAL (all val data)
    ens_val = ensemble.predict(
        {'elo': elo_val_preds, 'xgb': xgb_val_preds, 'lgb': lgb_val_preds})
    val_actual = {'home': yvh, 'draw': yvd, 'away': yva}

    # Time weights for validation + tournament importance
    val_dates = pd.to_datetime(val_feat['date'])
    val_ref = val_dates.max()
    val_weights = compute_val_weights(val_dates, val_ref, half_life=2)
    val_tournament_mult = val_feat['tournament'].map(BASE_TOURNAMENT).fillna(1.0).values
    val_weights = val_weights * val_tournament_mult
    print(f"  Val combined weights: min={val_weights.min():.2f}, mean={val_weights.mean():.2f}, "
          f"2022+ mean={val_weights[val_dates.dt.year >= 2022].mean():.2f}")

    print("\n=== VALIDATION (time-weighted) ===")
    for name, preds in [('ELO', elo_val_preds), ('XGBoost', xgb_val_preds),
                         ('LightGBM', lgb_val_preds), ('Ensemble', ens_val)]:
        avg = sum(weighted_log_loss(val_actual[o],
                  np.column_stack([1 - preds[o], preds[o]]),
                  sample_weight=val_weights)
                  for o in ['home', 'draw', 'away']) / 3
        print(f"  {name:10s}: {avg:.4f} (weighted)")

    # 7. Market comparison on val OddsPortal subset
    odds_val_mask = val_feat['odds_home'].notna().values
    n_val_odds = odds_val_mask.sum()
    print(f"\n{'='*60}")
    print(f"MARKET COMPARISON: {n_val_odds} val matches with real odds")
    print(f"{'='*60}")

    if n_val_odds > 0:
        val_odds_actual = {
            'home': yvh[odds_val_mask],
            'draw': yvd[odds_val_mask],
            'away': yva[odds_val_mask],
        }
        val_odds_feat = val_feat[odds_val_mask]
        val_odds_weights = val_weights[odds_val_mask]

        print(f"\n{'='*60}")
        print(f"MARKET COMPARISON: {n_val_odds} val matches (time-weighted)")
        print(f"{'='*60}")

        best_ll = 999
        for name, preds in [('ELO', elo_val_preds), ('XGBoost', xgb_val_preds),
                             ('LightGBM', lgb_val_preds), ('Ensemble', ens_val)]:
            p_sub = {o: preds[o][odds_val_mask] for o in ['home', 'draw', 'away']}
            avg = sum(weighted_log_loss(val_odds_actual[o],
                      np.column_stack([1 - p_sub[o], p_sub[o]]),
                      sample_weight=val_odds_weights)
                      for o in ['home', 'draw', 'away']) / 3
            print(f"  {name:10s}: {avg:.4f}")
            best_ll = min(best_ll, avg)

        mkt_raw = {o: 1.0 / val_odds_feat[f'odds_{o}'].values
                   for o in ['home', 'draw', 'away']}
        ovr = mkt_raw['home'] + mkt_raw['draw'] + mkt_raw['away']
        mkt = {o: mkt_raw[o] / ovr for o in ['home', 'draw', 'away']}
        avg_mkt = sum(weighted_log_loss(val_odds_actual[o],
                      np.column_stack([1 - mkt[o], mkt[o]]),
                      sample_weight=val_odds_weights)
                      for o in ['home', 'draw', 'away']) / 3
        print(f"  {'MARKET':10s}: {avg_mkt:.4f}")

        gap = best_ll - avg_mkt
        print(f"\n  Model: {best_ll:.4f}  Market: {avg_mkt:.4f}  Gap: {gap:+.4f}")
        if gap < 0:
            print(f"  MODEL BEATS MARKET by {abs(gap):.4f}!")
        else:
            print(f"  Market ahead by {gap:+.4f}")

        # Per year
        print(f"\n--- By year ---")
        val_odds_feat['_year'] = pd.to_datetime(val_odds_feat['date']).dt.year
        for yr, grp in val_odds_feat.groupby('_year'):
            if len(grp) < 3:
                continue
            grp_idx = grp.index
            idxs = [list(val_odds_feat.index).index(i) for i in grp_idx]
            g_act = {o: np.array([val_odds_actual[o][j] for j in idxs])
                     for o in ['home', 'draw', 'away']}
            g_ens = {o: np.array([ens_val[o][odds_val_mask][j] for j in idxs])
                     for o in ['home', 'draw', 'away']}
            g_mkt = {o: np.array([mkt[o][j] for j in idxs])
                     for o in ['home', 'draw', 'away']}
            ll_e = sum(log_loss(g_act[o],
                       np.column_stack([1 - g_ens[o], g_ens[o]]))
                       for o in ['home', 'draw', 'away']) / 3
            ll_m = sum(log_loss(g_act[o],
                       np.column_stack([1 - g_mkt[o], g_mkt[o]]))
                       for o in ['home', 'draw', 'away']) / 3
            print(f"  {yr}: {len(grp)} matches, Model={ll_e:.4f}, "
                  f"Market={ll_m:.4f} (gap={ll_e - ll_m:+.4f})")

    # 8. Save
    save_models(ensemble, xgb_models, lgb_models, elo_model, cols)
    print(f"\nModels saved.")


if __name__ == '__main__':
    run()
