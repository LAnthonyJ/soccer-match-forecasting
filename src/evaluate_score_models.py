"""
Evaluate score prediction models on ALL completed WC 2026 matches.
Tests: Score LogLoss, RPS, H/D/A LogLoss from score distributions.
"""
import sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline, FEATURE_COLS
from src.models.score_models import (
    load_score_models, ScoreMatrix, elo_poisson_score_matrix,
)
from src.models.score_evaluation import (
    compute_rps_vectorized, compute_logloss_exact_scores,
    compute_mae_expected_goals, calibration_curve_ou,
)

DATA_DIR = Path(__file__).parent.parent / 'data'

print("=" * 70)
print("SCORE PREDICTION MODEL EVALUATION ON WC 2026 (12 MATCHES)")
print("=" * 70)

# ── Load data & build features ──────────────────────────────────────
print("\n[1/4] Loading data and building features...")
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

# Load existing predictions
pred_df = pd.read_csv(DATA_DIR / 'predictions' / 'wc2026_predictions.csv')
done = pred_df[pred_df['actual'].notna()].copy()
print(f"  Completed matches: {len(done)}")

# ── Load score models ──────────────────────────────────────────────
print("\n[2/4] Loading score models...")
score_models = load_score_models()
dc = score_models['dixon_coles']
ml = score_models['ml_poisson']
xg = score_models['xg_poisson']
ens = score_models['score_ensemble']
print(f"  Dixon-Coles: {len(dc.teams_)} teams")
print(f"  ML-Poisson: calibrated={ml.cal_home is not None}")
print(f"  Ensemble: {dict(zip(ens.model_names_, [f'{w:.3f}' for w in ens.weights]))}")

# ── Predict scores for each match ──────────────────────────────────
print("\n[3/4] Running score predictions...")

act_map = {'H': 0, 'D': 1, 'A': 2}
results = []

for _, row in done.iterrows():
    home = row['match'].split(' vs ')[0]
    away = row['match'].split(' vs ')[1]
    actual_h = int(row['home_score'])
    actual_a = int(row['away_score'])
    actual_outcome = row['actual']

    # Find match in feature df
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
        print(f"  SKIP {home} vs {away}: not found in feature matrix")
        continue

    neutral = bool(match['neutral'].values[0]) if 'neutral' in match.columns else True
    elo_diff = match['elo_diff'].values[0] if 'elo_diff' in match.columns else 0

    # Prepare features for ML-Poisson (use model's own feature list)
    ml_cols = ml.feature_cols if ml.feature_cols else FEATURE_COLS
    for c in ml_cols:
        if c not in match.columns:
            match[c] = 0.0
    X = np.nan_to_num(match[ml_cols].values, nan=0.0, posinf=0.0, neginf=0.0).flatten()

    # ── Model predictions ──────────────────────────────────────────
    preds = {}

    # ELO Poisson
    preds['elo'] = elo_poisson_score_matrix(
        elo_diff if not np.isnan(elo_diff) else 0, neutral)

    # Dixon-Coles
    preds['dixon_coles'] = dc.predict_score_matrix(home, away, neutral=neutral)

    # ML-Poisson
    preds['ml_poisson'] = ml.predict_score_matrix(X)

    # xG-Poisson (fallback to ELO if no data)
    if xg.is_available(home, away):
        preds['xg_poisson'] = xg.predict_score_matrix(home, away)
    else:
        preds['xg_poisson'] = preds['elo']

    # Ensemble
    ens_input = [preds['dixon_coles'], preds['ml_poisson'], preds['xg_poisson']]
    preds['ensemble'] = ens.predict(ens_input)

    # Existing H/D/A ensemble predictions (from CSV)
    hda_model = np.array([row['model_H'], row['model_D'], row['model_A']])
    hda_market = np.array([row['mkt_H'], row['mkt_D'], row['mkt_A']])

    results.append({
        'match': f"{home} vs {away}",
        'home': home, 'away': away,
        'actual_h': actual_h, 'actual_a': actual_a,
        'actual_outcome': actual_outcome,
        'preds': preds,
        'hda_model': hda_model,
        'hda_market': hda_market,
    })

print(f"  Predicted {len(results)} matches")

# ── Evaluate ────────────────────────────────────────────────────────
print("\n[4/4] Evaluating...")
print()

n = len(results)
eps = 1e-15

# ═══════════════════════════════════════════════════════════════
# SCORE PREDICTION METRICS
# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("A. SCORE PREDICTION — RPS & LogLoss")
print("=" * 70)
print(f"  {'Model':<16} {'RPS':>8} {'LogLoss(score)':>15} {'MAE Home':>9} {'MAE Away':>9} {'MAE Total':>10}")
print(f"  {'-'*16} {'-'*8} {'-'*15} {'-'*9} {'-'*9} {'-'*10}")

model_names = ['elo', 'dixon_coles', 'ml_poisson', 'xg_poisson', 'ensemble']

for model_name in model_names:
    matrices = np.array([r['preds'][model_name].matrix for r in results])
    actual_scores = np.array([[r['actual_h'], r['actual_a']] for r in results])

    rps = compute_rps_vectorized(matrices, actual_scores)
    ll_score = compute_logloss_exact_scores(matrices, actual_scores)
    mae = compute_mae_expected_goals(matrices, actual_scores)

    print(f"  {model_name:<16} {rps:>8.4f} {ll_score:>15.4f} {mae['mae_home']:>9.3f} {mae['mae_away']:>9.3f} {mae['mae_total']:>10.3f}")

# ═══════════════════════════════════════════════════════════════
# H/D/A LOGLOSS from score distributions
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("B. MATCH OUTCOME (H/D/A) — derived from score distributions")
print("=" * 70)
print(f"  {'Model':<16} {'H/D/A LogLoss':>14} {'Accuracy':>9} {'Brier':>8}")
print(f"  {'-'*16} {'-'*14} {'-'*9} {'-'*8}")

for model_name in model_names:
    ll_total = 0
    correct = 0
    brier_total = 0
    for r in results:
        sm = r['preds'][model_name]
        hda_probs = sm.match_outcome_probs()
        mp = np.array([hda_probs['home'], hda_probs['draw'], hda_probs['away']])
        act_idx = act_map[r['actual_outcome']]
        ll_total += -np.log(np.clip(mp[act_idx], eps, 1))
        if np.argmax(mp) == act_idx:
            correct += 1
        target = np.zeros(3)
        target[act_idx] = 1
        brier_total += np.mean((mp - target) ** 2)
    print(f"  {model_name:<16} {ll_total/n:>14.4f} {correct:>6}/{n} ({correct/n:.0%}) {brier_total/n:>8.4f}")

# H/D/A ensemble (from CSV)
print()
hda_ll = 0
hda_correct = 0
hda_brier = 0
for r in results:
    mp = r['hda_model']
    act_idx = act_map[r['actual_outcome']]
    hda_ll += -np.log(np.clip(mp[act_idx], eps, 1))
    if np.argmax(mp) == act_idx:
        hda_correct += 1
    target = np.zeros(3)
    target[act_idx] = 1
    hda_brier += np.mean((mp - target) ** 2)
print(f"  {'HDA Ensemble (CSV)':<16} {hda_ll/n:>14.4f} {hda_correct:>6}/{n} ({hda_correct/n:.0%}) {hda_brier/n:>8.4f}")

# Market
mkt_ll = 0
mkt_correct = 0
mkt_brier = 0
for r in results:
    mk = r['hda_market']
    act_idx = act_map[r['actual_outcome']]
    mkt_ll += -np.log(np.clip(mk[act_idx], eps, 1))
    if np.argmax(mk) == act_idx:
        mkt_correct += 1
    target = np.zeros(3)
    target[act_idx] = 1
    mkt_brier += np.mean((mk - target) ** 2)
print(f"  {'Market (Pinnacle)':<16} {mkt_ll/n:>14.4f} {mkt_correct:>6}/{n} ({mkt_correct/n:.0%}) {mkt_brier/n:>8.4f}")

# ═══════════════════════════════════════════════════════════════
# OVER/UNDER 2.5 & BTTS
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("C. OVER/UNDER 2.5 & BTTS — derived from score distributions")
print("=" * 70)
print(f"  {'Model':<16} {'O/U Brier':>10} {'O/U Acc':>8} {'BTTS Brier':>11} {'BTTS Acc':>9}")
print(f"  {'-'*16} {'-'*10} {'-'*8} {'-'*11} {'-'*9}")

for model_name in model_names:
    ou_brier = 0
    ou_correct = 0
    btts_brier = 0
    btts_correct = 0
    for r in results:
        sm = r['preds'][model_name]
        over_p, _ = sm.over_under_probability(2.5)
        btts_p, _ = sm.btts_probability()

        actual_over = 1.0 if r['actual_h'] + r['actual_a'] > 2.5 else 0.0
        actual_btts = 1.0 if (r['actual_h'] > 0 and r['actual_a'] > 0) else 0.0

        ou_brier += (over_p - actual_over) ** 2
        btts_brier += (btts_p - actual_btts) ** 2
        if (over_p > 0.5) == (actual_over > 0.5):
            ou_correct += 1
        if (btts_p > 0.5) == (actual_btts > 0.5):
            btts_correct += 1

    print(f"  {model_name:<16} {ou_brier/n:>10.4f} {ou_correct:>5}/{n} ({ou_correct/n:.0%}) {btts_brier/n:>11.4f} {btts_correct:>5}/{n} ({btts_correct/n:.0%})")

# ═══════════════════════════════════════════════════════════════
# PER-MATCH SCORE DETAIL
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("D. PER-MATCH SCORE PREDICTIONS (ENSEMBLE)")
print("=" * 70)

for r in results:
    sm = r['preds']['ensemble']
    e_h, e_a = sm.expected_goals()
    over_p, _ = sm.over_under_probability(2.5)
    btts_p, _ = sm.btts_probability()
    hda = sm.match_outcome_probs()
    top3 = sm.top_scorelines(3)
    actual_score = f"{r['actual_h']}-{r['actual_a']}"
    actual_p = sm.probability(r['actual_h'], r['actual_a'])

    print(f"\n  {r['match']} (actual: {actual_score} P={actual_p:.3%})")
    print(f"    E[goals]={e_h:.2f}-{e_a:.2f}  O2.5={over_p:.1%}  BTTS={btts_p:.1%}")
    print(f"    HDA: H={hda['home']:.1%} D={hda['draw']:.1%} A={hda['away']:.1%}")
    print(f"    Top scores: {' | '.join(f'{h}-{a} {p:.1%}' for h, a, p in top3)}")

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("E. SUMMARY — BEST MODEL PER METRIC")
print("=" * 70)

# Recompute for summary
best_rps = min((compute_rps_vectorized(
    np.array([r['preds'][m].matrix for r in results]),
    np.array([[r['actual_h'], r['actual_a']] for r in results])), m)
    for m in model_names)
best_rps_name = min(model_names, key=lambda m: compute_rps_vectorized(
    np.array([r['preds'][m].matrix for r in results]),
    np.array([[r['actual_h'], r['actual_a']] for r in results])))

print(f"  Best RPS:           {best_rps_name} ({best_rps[0]:.4f})")
print(f"  Best H/D/A LogLoss: HDA Ensemble CSV ({hda_ll/n:.4f})" if hda_ll/n < mkt_ll/n else f"  Best H/D/A LogLoss: Market ({mkt_ll/n:.4f})")
print(f"  Market H/D/A LL:    {mkt_ll/n:.4f}")

# Count matches where ensemble HDA beats market
ens_beats_mkt = 0
mkt_beats_ens = 0
for r in results:
    sm = r['preds']['ensemble']
    hda = sm.match_outcome_probs()
    mp = np.array([hda['home'], hda['draw'], hda['away']])
    mk = r['hda_market']
    act_idx = act_map[r['actual_outcome']]
    if mp[act_idx] > mk[act_idx]:
        ens_beats_mkt += 1
    else:
        mkt_beats_ens += 1
print(f"  Ens > Mkt on actual: {ens_beats_mkt}/{n} ({ens_beats_mkt/n:.0%})")
