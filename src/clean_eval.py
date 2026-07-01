"""
Clean evaluation: retrain from scratch with ALL test matches excluded.
Compares model vs market (Pinnacle closing odds) on held-out data.
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
    Ensemble, prepare_features,
)

# ---- Load and normalize odds ----
odds_all = []
for f in glob.glob('data/raw/oddsportal_*.csv'):
    odf = pd.read_csv(f)
    odf = odf.rename(columns={'match_date': 'date'})
    odds_all.append(odf)
odds_all = pd.concat(odds_all, ignore_index=True)
odds_all['date'] = pd.to_datetime(odds_all['date'].str.replace(' UTC', '')).dt.date

name_fix = {
    'Bosnia & Herzegovina': 'Bosnia and Herzegovina',
    'D.R. Congo': 'DR Congo',
    'Guinea Bissau': 'Guinea-Bissau',
    'USA': 'United States',
}
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

# ---- Identify test matches (merge odds with results) ----
res = pd.read_csv('data/raw/results.csv')
res['date'] = pd.to_datetime(res['date']).dt.date

odds_sub = odds_all[['date', 'home_team', 'away_team',
                      'odds_home', 'odds_draw', 'odds_away']].dropna()

# Forward merge
mf = res.merge(odds_sub, on=['date', 'home_team', 'away_team'], how='inner')
# Reverse merge (swapped home/away)
odds_rev = odds_sub.rename(columns={
    'home_team': 'away_team', 'away_team': 'home_team',
    'odds_home': 'oh_rev', 'odds_away': 'oa_rev',
})
mr = res.merge(odds_rev, on=['date', 'home_team', 'away_team'], how='inner')
if len(mr) > 0:
    mr['odds_home'] = mr['oa_rev']
    mr['odds_away'] = mr['oh_rev']

test_raw = pd.concat([mf, mr], ignore_index=True)
test_raw = test_raw.drop_duplicates(subset=['date', 'home_team', 'away_team'])
test_raw = test_raw[test_raw['home_score'].notna()]

print(f"Odds-matched matches: {len(test_raw)}")
if 'league_name' in test_raw.columns:
    print(f"Tournaments: {test_raw['league_name'].value_counts().to_dict()}")

# ---- Build features ----
print("\nBuilding features...")
df = load_and_clean('data/raw/results.csv')
all_data = df[df['is_played']].copy()
full_df = build_all_features(all_data)
full_df['date'] = pd.to_datetime(full_df['date']).dt.date

# Merge event features
evt = pd.read_csv('data/processed/results_with_event_features.csv')
evt['date'] = pd.to_datetime(evt['date']).dt.date
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

# ---- Identify test indices ----
test_keys = set()
for _, r in test_raw.iterrows():
    test_keys.add((r['date'], r['home_team'], r['away_team']))

# Use a unique string key for robust matching
full_df['match_key'] = full_df['date'].astype(str) + '||' + full_df['home_team'] + '||' + full_df['away_team']
test_keys_str = set()
for _, r in test_raw.iterrows():
    test_keys_str.add(str(r['date']) + '||' + str(r['home_team']) + '||' + str(r['away_team']))

full_df['is_test'] = full_df['match_key'].isin(test_keys_str)
test_indices = set(full_df[full_df['is_test']].index)
print(f"Test matches in features: {len(test_indices)}")

# ---- Clean split: EXCLUDE test from train/val ----
np.random.seed(42)
train_pool = full_df[
    (~full_df['is_test']) &
    (full_df['date'].apply(lambda d: d.year) < 2026)
].copy()

mask = np.random.rand(len(train_pool)) < 0.85
train_feat = train_pool[mask].copy()
val_feat = train_pool[~mask].copy()
test_feat = full_df[full_df['is_test']].copy()

# Attach odds to test
test_feat = test_feat.merge(
    test_raw[['date', 'home_team', 'away_team',
              'odds_home', 'odds_draw', 'odds_away']],
    on=['date', 'home_team', 'away_team'], how='left',
)

# Verify no leak — use match_key
train_keys = set(train_feat['match_key'])
test_keys_set = set(test_feat['match_key'])
val_keys = set(val_feat['match_key'])
leak = train_keys & test_keys_set
print(f"Train: {len(train_feat)}, Val: {len(val_feat)}, Test: {len(test_feat)}")
print(f"Leak check (by match_key): train∩test={len(leak)}, val∩test={len(val_keys & test_keys_set)}")
if leak:
    print(f"LEAKED matches: {list(leak)[:3]}")

# ---- Train ----
print("\nTraining ELO...")
elo_model = train_elo_baseline(train_feat)
val_feat = predict_elo_baseline(val_feat, elo_model)
test_feat = predict_elo_baseline(test_feat, elo_model)

X_train, yh, yd, ya, cols, _ = prepare_features(train_feat)
X_val, yvh, yvd, yva, _, _ = prepare_features(val_feat)
X_test, yth, ytd, yta, _, _ = prepare_features(test_feat)

# Time weights
train_dates = pd.to_datetime(train_feat['date'])
ref_date = train_dates.max()
years_ago = (ref_date - train_dates).dt.days / 365.25
train_weights = np.clip(np.exp(-np.log(2) * years_ago.values / 3), 0.10, 1.0)

print(f"\nTraining XGBoost ({len(cols)} features)...")
xgb_models, xgb_preds = train_xgboost(
    X_train, yh, yd, ya, X_val, yvh, yvd, yva,
    X_test, yth, ytd, yta, sample_weights=train_weights,
)

print("\nTraining LightGBM...")
lgb_models, lgb_preds = train_lightgbm(
    X_train, yh, yd, ya, X_val, yvh, yvd, yva,
    X_test, yth, ytd, yta, sample_weights=train_weights,
)

# Ensemble
elo_val = {
    'home': val_feat['prob_h_elo'].values,
    'draw': val_feat['prob_d_elo'].values,
    'away': val_feat['prob_a_elo'].values,
}
xgb_val = {o: xgb_models[o].predict_proba(X_val)[:, 1] for o in ['home', 'draw', 'away']}
lgb_val = {o: lgb_models[o].predict_proba(X_val)[:, 1] for o in ['home', 'draw', 'away']}

ensemble = Ensemble()
ensemble.fit(
    {'elo': elo_val, 'xgb': xgb_val, 'lgb': lgb_val},
    {'home': yvh, 'draw': yvd, 'away': yva},
)

# ---- Evaluate on held-out test ----
elo_test = {
    'home': test_feat['prob_h_elo'].values,
    'draw': test_feat['prob_d_elo'].values,
    'away': test_feat['prob_a_elo'].values,
}
xgb_test = {o: xgb_models[o].predict_proba(X_test)[:, 1] for o in ['home', 'draw', 'away']}
lgb_test = {o: lgb_models[o].predict_proba(X_test)[:, 1] for o in ['home', 'draw', 'away']}
ens_test = ensemble.predict({'elo': elo_test, 'xgb': xgb_test, 'lgb': lgb_test})

actual = {'home': yth, 'draw': ytd, 'away': yta}

print(f"\n{'='*60}")
print(f"FINAL: {len(test_feat)} HELD-OUT matches, Model vs Market")
print(f"{'='*60}")

best_ll = 999
for name, preds in [('ELO', elo_test), ('XGBoost', xgb_test),
                     ('LightGBM', lgb_test), ('Ensemble', ens_test)]:
    ll_h = log_loss(actual['home'], np.column_stack([1 - preds['home'], preds['home']]))
    ll_d = log_loss(actual['draw'], np.column_stack([1 - preds['draw'], preds['draw']]))
    ll_a = log_loss(actual['away'], np.column_stack([1 - preds['away'], preds['away']]))
    avg = (ll_h + ll_d + ll_a) / 3
    print(f"  {name:10s}: H={ll_h:.4f} D={ll_d:.4f} A={ll_a:.4f}  Avg={avg:.4f}")
    if name == 'Ensemble':
        best_ll = avg

# Market
mkt_raw = {o: 1.0 / test_feat[f'odds_{o}'].values for o in ['home', 'draw', 'away']}
ovr = mkt_raw['home'] + mkt_raw['draw'] + mkt_raw['away']
mkt = {o: mkt_raw[o] / ovr for o in ['home', 'draw', 'away']}
ll_mh = log_loss(actual['home'], np.column_stack([1 - mkt['home'], mkt['home']]))
ll_md = log_loss(actual['draw'], np.column_stack([1 - mkt['draw'], mkt['draw']]))
ll_ma = log_loss(actual['away'], np.column_stack([1 - mkt['away'], mkt['away']]))
avg_mkt = (ll_mh + ll_md + ll_ma) / 3
print(f"  {'MARKET':10s}: H={ll_mh:.4f} D={ll_md:.4f} A={ll_ma:.4f}  Avg={avg_mkt:.4f}")

# Final verdict
gap = best_ll - avg_mkt
print(f"\n  Model: {best_ll:.4f}  Market: {avg_mkt:.4f}  Gap: {gap:+.4f}")
if gap < 0:
    print(f"  *** MODEL BEATS MARKET by {abs(gap):.4f}! ***")
else:
    print(f"  Market ahead by {gap:+.4f}")

# Per-tournament breakdown
print(f"\n--- By tournament ---")
test_feat_2 = test_feat.copy()
test_feat_2['tournament_year'] = test_feat_2['date'].apply(lambda d: str(d.year))
for yr, grp in test_feat_2.groupby('tournament_year'):
    n = len(grp)
    g_actual = {'home': np.array([actual['home'][list(test_feat_2.index).index(i)]
                                    for i in grp.index]),
                'draw': np.array([actual['draw'][list(test_feat_2.index).index(i)]
                                    for i in grp.index]),
                'away': np.array([actual['away'][list(test_feat_2.index).index(i)]
                                    for i in grp.index])}
    g_ens = {'home': np.array([ens_test['home'][list(test_feat_2.index).index(i)]
                                for i in grp.index]),
             'draw': np.array([ens_test['draw'][list(test_feat_2.index).index(i)]
                                for i in grp.index]),
             'away': np.array([ens_test['away'][list(test_feat_2.index).index(i)]
                                for i in grp.index])}
    g_mkt = {'home': np.array([mkt['home'][list(test_feat_2.index).index(i)]
                                for i in grp.index]),
             'draw': np.array([mkt['draw'][list(test_feat_2.index).index(i)]
                                for i in grp.index]),
             'away': np.array([mkt['away'][list(test_feat_2.index).index(i)]
                                for i in grp.index])}

    ll_ens_y = sum(log_loss(g_actual[o],
                             np.column_stack([1 - g_ens[o], g_ens[o]]))
                   for o in ['home', 'draw', 'away']) / 3
    ll_mkt_y = sum(log_loss(g_actual[o],
                             np.column_stack([1 - g_mkt[o], g_mkt[o]]))
                   for o in ['home', 'draw', 'away']) / 3
    print(f"  {yr}: {n} matches, Ensemble={ll_ens_y:.4f}, Market={ll_mkt_y:.4f} "
          f"(gap={ll_ens_y - ll_mkt_y:+.4f})")

# Save clean model
from src.models.train import OUTPUT_DIR, save_models
save_models(ensemble, xgb_models, lgb_models, elo_model, cols)
print(f"\nClean model saved.")
