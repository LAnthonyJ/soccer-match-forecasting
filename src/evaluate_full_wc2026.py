"""
Evaluate all score & match outcome models on complete WC 2026 group stage (72 matches).
"""
import sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline, FEATURE_COLS, Ensemble
from src.models.score_models import (
    load_score_models, ScoreMatrix, elo_poisson_score_matrix,
)
from src.models.score_evaluation import (
    compute_rps_vectorized, compute_logloss_exact_scores,
    compute_mae_expected_goals,
)

DATA_DIR = Path(__file__).parent.parent / 'data'
eps = 1e-15
act_map = {'H': 0, 'D': 1, 'A': 2}

print("=" * 70)
print("FULL WC 2026 MODEL EVALUATION (72 GROUP STAGE MATCHES)")
print("=" * 70)

# ── 1. Load WC 2026 results ───────────────────────────────────────
print("\n[1/5] Loading WC 2026 results...")
wc = pd.read_csv(DATA_DIR / 'raw' / 'wc2026_results.csv')
wc['date'] = pd.to_datetime(wc['date'])
print(f"  WC 2026 matches: {len(wc)}")

# ── 2. Build features on ALL data ─────────────────────────────────
print("\n[2/5] Building features (this takes ~30s)...")
df = load_and_clean(str(DATA_DIR / 'raw' / 'results.csv'))
full_df = build_all_features(df)

# Merge event features
evt_path = DATA_DIR / 'processed' / 'results_with_event_features.csv'
if evt_path.exists():
    evt = pd.read_csv(evt_path)
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

# ── 3. Load models ────────────────────────────────────────────────
print("\n[3/5] Loading models...")
hda_model = pickle.load(open('outputs/models/full_model.pkl', 'rb'))
ensemble = hda_model['ensemble']
xgb_m = hda_model['xgb_models']
lgb_m = hda_model['lgb_models']
elo_m = hda_model['elo_model']
cols = hda_model['feature_cols']

score_models = load_score_models()
dc = score_models['dixon_coles']
ml = score_models['ml_poisson']
xg = score_models['xg_poisson']
ens = score_models['score_ensemble']

# ── 4. Run predictions ────────────────────────────────────────────
print("\n[4/5] Running predictions on all 72 matches...")

results = []
for _, row in wc.iterrows():
    home = row['home_team']
    away = row['away_team']
    actual_h = int(row['home_score'])
    actual_a = int(row['away_score'])

    # Find in feature df
    match = full_df[(full_df['home_team'] == home) &
                    (full_df['away_team'] == away)]
    if len(match) > 1:
        match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        match = full_df[(full_df['home_team'].str.contains(home[:4], na=False)) &
                        (full_df['away_team'].str.contains(away[:4], na=False))]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        # Try reverse
        match = full_df[(full_df['home_team'] == away) &
                        (full_df['away_team'] == home)]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)

    if match.empty:
        continue

    neutral = bool(match['neutral'].values[0]) if 'neutral' in match.columns else True
    elo_diff_val = match['elo_diff'].values[0] if 'elo_diff' in match.columns else 0

    # ── H/D/A Ensemble prediction ─────────────────────────────────
    match_feat = predict_elo_baseline(match, elo_m)
    for c in cols:
        if c not in match_feat.columns:
            match_feat[c] = 0.0
    X = np.nan_to_num(match_feat[cols].values, nan=0.0)

    xgb_p = {o: xgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    lgb_p = {o: lgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    elo_p_arr = {
        'home': np.array([match_feat['prob_h_elo'].values[0]]),
        'draw': np.array([match_feat['prob_d_elo'].values[0]]),
        'away': np.array([match_feat['prob_a_elo'].values[0]]),
    }
    xgb_arr = {o: np.array([xgb_p[o]]) for o in ['home', 'draw', 'away']}
    lgb_arr = {o: np.array([lgb_p[o]]) for o in ['home', 'draw', 'away']}
    ens_hda = ensemble.predict({'elo': elo_p_arr, 'xgb': xgb_arr, 'lgb': lgb_arr})
    hda_probs = {
        'home': float(ens_hda['home'][0]),
        'draw': float(ens_hda['draw'][0]),
        'away': float(ens_hda['away'][0]),
    }

    # ── Score predictions ─────────────────────────────────────────
    preds = {}
    preds['elo'] = elo_poisson_score_matrix(
        elo_diff_val if not np.isnan(elo_diff_val) else 0, neutral)
    preds['dixon_coles'] = dc.predict_score_matrix(home, away, neutral=neutral)

    ml_cols = ml.feature_cols if ml.feature_cols else cols
    for c in ml_cols:
        if c not in match_feat.columns:
            match_feat[c] = 0.0
    X_ml = np.nan_to_num(match_feat[ml_cols].values, nan=0.0, posinf=0.0, neginf=0.0).flatten()
    preds['ml_poisson'] = ml.predict_score_matrix(X_ml)

    if xg.is_available(home, away):
        preds['xg_poisson'] = xg.predict_score_matrix(home, away)
    else:
        preds['xg_poisson'] = preds['elo']

    preds['ensemble'] = ens.predict([preds['dixon_coles'], preds['ml_poisson'], preds['xg_poisson']])

    actual_outcome = 'H' if actual_h > actual_a else ('D' if actual_h == actual_a else 'A')
    results.append({
        'match': f"{home} vs {away}",
        'home': home, 'away': away,
        'actual_h': actual_h, 'actual_a': actual_a,
        'actual_outcome': actual_outcome,
        'preds': preds,
        'hda_probs': hda_probs,
    })

matched = len(results)
print(f"  Matched: {matched}/{len(wc)} matches")
skipped = len(wc) - matched

# ── 5. Evaluate ───────────────────────────────────────────────────
print("\n[5/5] Evaluating...")
print()

# ═══════════════════════════════════════════════════════════════
# A. SCORE PREDICTION
# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("A. SCORE PREDICTION METRICS")
print("=" * 70)
model_names = ['elo', 'dixon_coles', 'ml_poisson', 'xg_poisson', 'ensemble']
print(f"  {'Model':<16} {'RPS':>8} {'LogLoss(s)':>11} {'MAE_H':>7} {'MAE_A':>7} {'MAE_T':>7}")
print(f"  {'-'*16} {'-'*8} {'-'*11} {'-'*7} {'-'*7} {'-'*7}")

best_rps = 999
best_rps_name = ""
for mn in model_names:
    mats = np.array([r['preds'][mn].matrix for r in results])
    actuals = np.array([[r['actual_h'], r['actual_a']] for r in results])
    rps = compute_rps_vectorized(mats, actuals)
    ll_s = compute_logloss_exact_scores(mats, actuals)
    mae = compute_mae_expected_goals(mats, actuals)
    print(f"  {mn:<16} {rps:>8.4f} {ll_s:>11.4f} {mae['mae_home']:>7.3f} {mae['mae_away']:>7.3f} {mae['mae_total']:>7.3f}")
    if rps < best_rps:
        best_rps = rps
        best_rps_name = mn

# ═══════════════════════════════════════════════════════════════
# B. H/D/A from score distributions vs HDA Ensemble
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("B. MATCH OUTCOME (H/D/A)")
print("=" * 70)
print(f"  {'Model':<20} {'LogLoss':>9} {'Accuracy':>9} {'Brier':>7}")

for mn in model_names:
    ll_total = 0
    correct = 0
    brier = 0
    for r in results:
        sm = r['preds'][mn]
        hda = sm.match_outcome_probs()
        mp = np.array([hda['home'], hda['draw'], hda['away']])
        act_idx = act_map[r['actual_outcome']]
        ll_total += -np.log(np.clip(mp[act_idx], eps, 1))
        if np.argmax(mp) == act_idx:
            correct += 1
        target = np.zeros(3)
        target[act_idx] = 1
        brier += np.mean((mp - target)**2)
    print(f"  {mn:<20} {ll_total/matched:>9.4f} {correct:>5}/{matched} ({correct/matched:.0%}) {brier/matched:>7.4f}")

# HDA Ensemble
hda_ll = 0
hda_correct = 0
hda_brier = 0
for r in results:
    mp = np.array([r['hda_probs']['home'], r['hda_probs']['draw'], r['hda_probs']['away']])
    act_idx = act_map[r['actual_outcome']]
    hda_ll += -np.log(np.clip(mp[act_idx], eps, 1))
    if np.argmax(mp) == act_idx:
        hda_correct += 1
    target = np.zeros(3)
    target[act_idx] = 1
    hda_brier += np.mean((mp - target)**2)
print(f"  {'HDA Ensemble (XGB+LGB)':<20} {hda_ll/matched:>9.4f} {hda_correct:>5}/{matched} ({hda_correct/matched:.0%}) {hda_brier/matched:>7.4f}")

# ═══════════════════════════════════════════════════════════════
# C. OVER/UNDER & BTTS
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("C. OVER/UNDER 2.5 & BTTS")
print("=" * 70)
print(f"  {'Model':<16} {'O/U Acc':>8} {'O/U Brier':>10} {'BTTS Acc':>9} {'BTTS Brier':>11}")

for mn in model_names:
    ou_ok = 0
    ou_brier = 0
    btts_ok = 0
    btts_brier = 0
    for r in results:
        sm = r['preds'][mn]
        over_p, _ = sm.over_under_probability(2.5)
        btts_p, _ = sm.btts_probability()
        actual_over = 1.0 if r['actual_h'] + r['actual_a'] > 2.5 else 0.0
        actual_btts = 1.0 if (r['actual_h'] > 0 and r['actual_a'] > 0) else 0.0
        ou_brier += (over_p - actual_over)**2
        btts_brier += (btts_p - actual_btts)**2
        if (over_p > 0.5) == (actual_over > 0.5):
            ou_ok += 1
        if (btts_p > 0.5) == (actual_btts > 0.5):
            btts_ok += 1
    print(f"  {mn:<16} {ou_ok:>4}/{matched} ({ou_ok/matched:.0%}) {ou_brier/matched:>10.4f} {btts_ok:>4}/{matched} ({btts_ok/matched:.0%}) {btts_brier/matched:>11.4f}")

# ═══════════════════════════════════════════════════════════════
# D. PER-GROUP BREAKDOWN
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("D. BEST SCORE MODEL PER GROUP (RPS)")
print("=" * 70)

for group in ['A','B','C','D','E','F','G','H','I','J','K','L']:
    grp_matches = wc[wc['group'] == group]
    grp_results = []
    for _, g_row in grp_matches.iterrows():
        for r in results:
            if r['home'] == g_row['home_team'] and r['away'] == g_row['away_team']:
                grp_results.append(r)
                break

    if len(grp_results) >= 4:
        best_mn = min(model_names, key=lambda mn: compute_rps_vectorized(
            np.array([r['preds'][mn].matrix for r in grp_results]),
            np.array([[r['actual_h'], r['actual_a']] for r in grp_results])))
        rps_dc = compute_rps_vectorized(
            np.array([r['preds']['dixon_coles'].matrix for r in grp_results]),
            np.array([[r['actual_h'], r['actual_a']] for r in grp_results]))
        rps_ml = compute_rps_vectorized(
            np.array([r['preds']['ml_poisson'].matrix for r in grp_results]),
            np.array([[r['actual_h'], r['actual_a']] for r in grp_results]))
        rps_ens = compute_rps_vectorized(
            np.array([r['preds']['ensemble'].matrix for r in grp_results]),
            np.array([[r['actual_h'], r['actual_a']] for r in grp_results]))
        print(f"  Group {group} ({len(grp_results)}m): Best={best_mn} | DC={rps_dc:.4f} ML={rps_ml:.4f} ENS={rps_ens:.4f}")

# ═══════════════════════════════════════════════════════════════
# E. SUMMARY
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("E. SUMMARY")
print("=" * 70)
print(f"  Total matches evaluated: {matched} (skipped: {skipped})")
print(f"  Best score model:       {best_rps_name} (RPS={best_rps:.4f})")
print(f"  HDA Ensemble accuracy:  {hda_correct}/{matched} ({hda_correct/matched:.0%})")
print(f"  HDA Ensemble LogLoss:   {hda_ll/matched:.4f}")

# Compare score-model HDA vs dedicated HDA model
dc_ll = 0
dc_correct = 0
for r in results:
    sm = r['preds']['dixon_coles']
    hda = sm.match_outcome_probs()
    mp = np.array([hda['home'], hda['draw'], hda['away']])
    act_idx = act_map[r['actual_outcome']]
    dc_ll += -np.log(np.clip(mp[act_idx], eps, 1))
    if np.argmax(mp) == act_idx:
        dc_correct += 1
print(f"  Dixon-Coles HDA Acc:    {dc_correct}/{matched} ({dc_correct/matched:.0%}) (LL: {dc_ll/matched:.4f})")
