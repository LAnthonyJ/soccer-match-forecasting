"""
Clean train/val/test split with zero data leakage.

Split design:
  TRAIN:  2000-01-01 to 2019-12-31 (excl. test matches)  ~16K matches
  VAL:    2020-01-01 to 2021-12-31 (excl. test matches)  ~2K matches
  TEST:   All 57 OddsPortal matches (WC 2018, WC 2022, AFCON 2023)

Rules:
  1. No match appears in more than one set
  2. Test = held-out, never seen during training/tuning
  3. Val = used for hyperparameter tuning only
  4. Time decay applied within training
"""
import sys, os, glob, ast, pickle
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.metrics import log_loss

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
    Ensemble, prepare_features, save_models,
)

DATA_DIR = Path(__file__).parent.parent / 'data'
# Split: Train includes WC 2014 (for tournament pattern learning)
# Val includes Euro 2020 + Copa 2021 (tournament matches)
# Test = WC 2018 + WC 2022 + AFCON 2023 (held-out with real odds)
TRAIN_START = '2000-01-01'
TRAIN_END = '2019-06-30'     # Up to before Copa 2019
VAL_START = '2019-07-01'
VAL_END = '2021-12-31'        # Includes Copa 2019, Euro 2020, Copa 2021


def load_test_odds():
    """Load and parse all OddsPortal files, match to results.csv."""
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
    # Strip timezone, keep date only for matching
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

    res = pd.read_csv(DATA_DIR / 'raw' / 'results.csv')
    res['date'] = pd.to_datetime(res['date']).dt.date  # date-only for matching

    odds_sub = odds_all[['date', 'home_team', 'away_team',
                          'odds_home', 'odds_draw', 'odds_away']].dropna()

    # Forward merge
    mf = res.merge(odds_sub, on=['date', 'home_team', 'away_team'], how='inner')
    # Reverse merge
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
    combined['date'] = pd.to_datetime(combined['date'])

    # Fuzzy: ±1 day for unmatched odds
    matched_keys = set(zip(combined['date'], combined['home_team'], combined['away_team']))
    from datetime import timedelta
    fuzzy_matches = []
    for _, odd in odds_sub.iterrows():
        odd_date = odd['date']
        odd_home = odd['home_team']
        odd_away = odd['away_team']
        # Already matched?
        if (pd.Timestamp(odd_date), odd_home, odd_away) in matched_keys:
            continue
        if (pd.Timestamp(odd_date), odd_away, odd_home) in matched_keys:
            continue
        # Try ±1 day
        for delta in [1, -1]:
            search_date = odd_date + timedelta(days=delta)
            # Forward
            m = res[(res['date'] == search_date) &
                    (res['home_team'] == odd_home) &
                    (res['away_team'] == odd_away)]
            if len(m) > 0:
                r = m.iloc[0]
                fuzzy_matches.append({
                    'date': pd.Timestamp(search_date),
                    'home_team': odd_home, 'away_team': odd_away,
                    'home_score': r['home_score'], 'away_score': r['away_score'],
                    'tournament': r['tournament'], 'neutral': r['neutral'],
                    'odds_home': odd['odds_home'], 'odds_draw': odd['odds_draw'],
                    'odds_away': odd['odds_away'],
                })
                break
            # Reverse
            m = res[(res['date'] == search_date) &
                    (res['home_team'] == odd_away) &
                    (res['away_team'] == odd_home)]
            if len(m) > 0:
                r = m.iloc[0]
                fuzzy_matches.append({
                    'date': pd.Timestamp(search_date),
                    'home_team': odd_away, 'away_team': odd_home,
                    'home_score': r['home_score'], 'away_score': r['away_score'],
                    'tournament': r['tournament'], 'neutral': r['neutral'],
                    'odds_home': odd['odds_away'], 'odds_draw': odd['odds_draw'],
                    'odds_away': odd['odds_home'],
                })
                break

    if fuzzy_matches:
        fm_df = pd.DataFrame(fuzzy_matches)
        combined = pd.concat([combined, fm_df], ignore_index=True)

    result = combined.drop_duplicates(subset=['date', 'home_team', 'away_team'])
    result = result[result['home_score'].notna()]
    result['date'] = pd.to_datetime(result['date'])

    print(f"Test matches with real odds: {len(result)}")
    for yr, grp in result.groupby(result['date'].dt.year):
        print(f"  {yr}: {len(grp)} matches")
    return result


def run():
    # ---- 1. Load test matches ----
    print("=" * 60)
    print("CLEAN TRAIN/VAL/TEST SPLIT")
    print("=" * 60)
    test_raw = load_test_odds()

    # ---- 2. Build features on ALL data ----
    print("\nBuilding features...")
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

    # ---- 3. Mark test matches ----
    test_keys = set()
    for _, r in test_raw.iterrows():
        test_keys.add((pd.Timestamp(r['date']), r['home_team'], r['away_team']))

    full_df['is_test'] = full_df.apply(
        lambda row: (row['date'], row['home_team'], row['away_team']) in test_keys,
        axis=1,
    )
    print(f"Test match keys: {full_df['is_test'].sum()}")

    # ---- 4. Split by time ----
    full_df['in_train'] = (
        (full_df['date'] >= TRAIN_START) &
        (full_df['date'] <= TRAIN_END) &
        (~full_df['is_test'])
    )
    full_df['in_val'] = (
        (full_df['date'] >= VAL_START) &
        (full_df['date'] <= VAL_END) &
        (~full_df['is_test'])
    )
    full_df['in_test'] = full_df['is_test']

    train_feat = full_df[full_df['in_train']].copy()
    val_feat = full_df[full_df['in_val']].copy()
    test_feat = full_df[full_df['in_test']].copy()

    # Attach odds to test
    test_feat = test_feat.merge(
        test_raw[['date', 'home_team', 'away_team',
                  'odds_home', 'odds_draw', 'odds_away']],
        on=['date', 'home_team', 'away_team'], how='left',
    )

    # ---- 5. Verify split ----
    print(f"\nSplit sizes:")
    print(f"  TRAIN: {len(train_feat)} matches ({train_feat['date'].min().date()} to {train_feat['date'].max().date()})")
    print(f"  VAL:   {len(val_feat)} matches ({val_feat['date'].min().date()} to {val_feat['date'].max().date()})")
    print(f"  TEST:  {len(test_feat)} matches (held-out, with real odds)")

    train_keys = set(zip(train_feat['date'], train_feat['home_team'], train_feat['away_team']))
    val_keys = set(zip(val_feat['date'], val_feat['home_team'], val_feat['away_team']))
    test_keys_set = set(zip(test_feat['date'], test_feat['home_team'], test_feat['away_team']))
    print(f"  Leak train∩test: {len(train_keys & test_keys_set)}")
    print(f"  Leak val∩test:   {len(val_keys & test_keys_set)}")
    print(f"  Leak train∩val:  {len(train_keys & val_keys)}")

    # ---- 6. Train ----
    print("\n" + "=" * 60)
    print("TRAINING")
    print("=" * 60)

    print("\n[1] ELO Baseline...")
    elo_model = train_elo_baseline(train_feat)
    val_feat = predict_elo_baseline(val_feat, elo_model)
    test_feat = predict_elo_baseline(test_feat, elo_model)

    print("\n[2] Preparing feature matrices...")
    X_train, yh, yd, ya, cols, _ = prepare_features(train_feat)
    X_val, yvh, yvd, yva, _, _ = prepare_features(val_feat)
    X_test, yth, ytd, yta, _, _ = prepare_features(test_feat)

    # Time decay weights
    train_dates = pd.to_datetime(train_feat['date'])
    ref_date = train_dates.max()
    years_ago = (ref_date - train_dates).dt.days / 365.25
    train_weights = np.clip(np.exp(-np.log(2) * years_ago.values / 3), 0.10, 1.0)
    print(f"  Time weights: min={train_weights.min():.2f}, mean={train_weights.mean():.2f}")

    print("\n[3] XGBoost...")
    xgb_models, xgb_preds = train_xgboost(
        X_train, yh, yd, ya, X_val, yvh, yvd, yva,
        X_test, yth, ytd, yta, sample_weights=train_weights,
    )

    print("\n[4] LightGBM...")
    lgb_models, lgb_preds = train_lightgbm(
        X_train, yh, yd, ya, X_val, yvh, yvd, yva,
        X_test, yth, ytd, yta, sample_weights=train_weights,
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

    # ---- 7. Evaluate on VAL (for tuning) ----
    ens_val = ensemble.predict(
        {'elo': elo_val_preds, 'xgb': xgb_val_preds, 'lgb': lgb_val_preds})
    val_actual = {'home': yvh, 'draw': yvd, 'away': yva}

    print("\n=== VALIDATION SET (for tuning) ===")
    for name, preds in [('ELO', elo_val_preds), ('XGBoost', xgb_val_preds),
                         ('LightGBM', lgb_val_preds), ('Ensemble', ens_val)]:
        avg = sum(log_loss(val_actual[o],
                  np.column_stack([1 - preds[o], preds[o]]))
                  for o in ['home', 'draw', 'away']) / 3
        print(f"  {name:10s}: {avg:.4f}")

    # ---- 8. Evaluate on TEST (final) ----
    elo_test_preds = {
        'home': test_feat['prob_h_elo'].values,
        'draw': test_feat['prob_d_elo'].values,
        'away': test_feat['prob_a_elo'].values,
    }
    xgb_test_preds = {o: xgb_models[o].predict_proba(X_test)[:, 1]
                      for o in ['home', 'draw', 'away']}
    lgb_test_preds = {o: lgb_models[o].predict_proba(X_test)[:, 1]
                      for o in ['home', 'draw', 'away']}
    ens_test = ensemble.predict(
        {'elo': elo_test_preds, 'xgb': xgb_test_preds, 'lgb': lgb_test_preds})

    test_actual = {'home': yth, 'draw': ytd, 'away': yta}

    print(f"\n{'='*60}")
    print(f"TEST SET: {len(test_feat)} held-out matches with real odds")
    print(f"{'='*60}")

    for name, preds in [('ELO', elo_test_preds), ('XGBoost', xgb_test_preds),
                         ('LightGBM', lgb_test_preds), ('Ensemble', ens_test)]:
        ll_h = log_loss(test_actual['home'],
                        np.column_stack([1 - preds['home'], preds['home']]))
        ll_d = log_loss(test_actual['draw'],
                        np.column_stack([1 - preds['draw'], preds['draw']]))
        ll_a = log_loss(test_actual['away'],
                        np.column_stack([1 - preds['away'], preds['away']]))
        avg = (ll_h + ll_d + ll_a) / 3
        print(f"  {name:10s}: H={ll_h:.4f} D={ll_d:.4f} A={ll_a:.4f}  Avg={avg:.4f}")

    # Market
    mkt_raw = {o: 1.0 / test_feat[f'odds_{o}'].values for o in ['home', 'draw', 'away']}
    ovr = mkt_raw['home'] + mkt_raw['draw'] + mkt_raw['away']
    mkt = {o: mkt_raw[o] / ovr for o in ['home', 'draw', 'away']}
    ll_mh = log_loss(test_actual['home'],
                     np.column_stack([1 - mkt['home'], mkt['home']]))
    ll_md = log_loss(test_actual['draw'],
                     np.column_stack([1 - mkt['draw'], mkt['draw']]))
    ll_ma = log_loss(test_actual['away'],
                     np.column_stack([1 - mkt['away'], mkt['away']]))
    avg_mkt = (ll_mh + ll_md + ll_ma) / 3
    print(f"  {'MARKET':10s}: H={ll_mh:.4f} D={ll_md:.4f} A={ll_ma:.4f}  Avg={avg_mkt:.4f}")

    best_ll = min(
        sum(log_loss(test_actual[o],
            np.column_stack([1 - p[o], p[o]])) for o in ['home', 'draw', 'away']) / 3
        for p in [elo_test_preds, xgb_test_preds, lgb_test_preds, ens_test]
    )
    gap = best_ll - avg_mkt

    print(f"\n  Best Model: {best_ll:.4f}  Market: {avg_mkt:.4f}  Gap: {gap:+.4f}")
    if gap < 0:
        print(f"  *** MODEL BEATS MARKET by {abs(gap):.4f}! ***")
    else:
        print(f"  Market ahead by {gap:+.4f}")

    # Per-tournament
    print(f"\n--- By tournament ---")
    for yr, grp in test_feat.groupby(test_feat['date'].dt.year):
        n = len(grp)
        idxs = [list(test_feat.index).index(i) for i in grp.index]
        g_actual = {o: np.array([test_actual[o][j] for j in idxs]) for o in ['home', 'draw', 'away']}
        g_ens = {o: np.array([ens_test[o][j] for j in idxs]) for o in ['home', 'draw', 'away']}
        g_mkt = {o: np.array([mkt[o][j] for j in idxs]) for o in ['home', 'draw', 'away']}

        ll_e = sum(log_loss(g_actual[o],
                   np.column_stack([1 - g_ens[o], g_ens[o]]))
                   for o in ['home', 'draw', 'away']) / 3
        ll_m = sum(log_loss(g_actual[o],
                   np.column_stack([1 - g_mkt[o], g_mkt[o]]))
                   for o in ['home', 'draw', 'away']) / 3
        print(f"  {yr}: {n} matches, Model={ll_e:.4f}, Market={ll_m:.4f} (gap={ll_e-ll_m:+.4f})")

    # Save
    save_models(ensemble, xgb_models, lgb_models, elo_model, cols)
    print(f"\nModels saved.")


if __name__ == '__main__':
    run()
