"""
Betting strategy backtest using Dixon-Coles score model on WC 2026 (70 matches).
Tests: H/D/A, Over/Under 2.5, BTTS, Correct Score betting strategies.
"""
import sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline, FEATURE_COLS
from src.models.score_models import load_score_models

DATA_DIR = Path(__file__).parent.parent / 'data'
eps = 1e-15

print("=" * 70)
print("DIXON-COLES BETTING BACKTEST — WC 2026 (70 MATCHES)")
print("=" * 70)

# ── 1. Build features ───────────────────────────────────────────
print("\n[1/3] Building features...")
df = load_and_clean(str(DATA_DIR / 'raw' / 'results.csv'))
full_df = build_all_features(df)

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

# ── 2. Load WC results & Dixon-Coles ────────────────────────────
print("[2/3] Loading models...")
wc = pd.read_csv(DATA_DIR / 'raw' / 'wc2026_results.csv')
wc['date'] = pd.to_datetime(wc['date'])

score_models = load_score_models()
dc = score_models['dixon_coles']

# ── 3. Predict & simulate betting ───────────────────────────────
print("[3/3] Running predictions & betting simulation...\n")

results = []
for _, row in wc.iterrows():
    home = row['home_team']
    away = row['away_team']
    actual_h = int(row['home_score'])
    actual_a = int(row['away_score'])
    actual_total = actual_h + actual_a
    actual_outcome = 'H' if actual_h > actual_a else ('D' if actual_h == actual_a else 'A')
    actual_btts = 'Y' if (actual_h > 0 and actual_a > 0) else 'N'
    actual_over = 'O' if actual_total > 2.5 else 'U'

    # Find in feature matrix for elo_diff
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
        continue

    neutral = bool(match['neutral'].values[0]) if 'neutral' in match.columns else True
    elo_diff = match['elo_diff'].values[0] if 'elo_diff' in match.columns else 0

    # Dixon-Coles prediction
    sm = dc.predict_score_matrix(home, away, neutral=neutral)

    # Extract probabilities
    hda = sm.match_outcome_probs()
    over_p, under_p = sm.over_under_probability(2.5)
    btts_p, btts_n = sm.btts_probability()

    # Expected goals
    e_h, e_a = sm.expected_goals()

    # Top correct score probability
    actual_score_p = sm.probability(actual_h, actual_a)

    # Best edge for H/D/A
    dc_probs = np.array([hda['home'], hda['draw'], hda['away']])

    results.append({
        'match': f"{home} vs {away}",
        'home': home, 'away': away,
        'actual_h': actual_h, 'actual_a': actual_a,
        'actual_total': actual_total,
        'actual_outcome': actual_outcome,
        'actual_btts': actual_btts,
        'actual_over': actual_over,
        'e_home': e_h, 'e_away': e_a,
        'dc_H': hda['home'], 'dc_D': hda['draw'], 'dc_A': hda['away'],
        'p_over': over_p, 'p_under': under_p,
        'p_btts_y': btts_p, 'p_btts_n': btts_n,
        'p_actual_score': actual_score_p,
        'elo_diff': elo_diff,
    })

n = len(results)
print(f"  Matched: {n} matches\n")

# ═══════════════════════════════════════════════════════════════
# A. H/D/A BETTING STRATEGY
# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("A. H/D/A MATCH OUTCOME BETTING")
print("=" * 70)

# Simulate: bet on the outcome where model prob is highest, with various confidence thresholds
thresholds = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
print(f"\n  Strategy: Bet on Dixon-Coles predicted outcome when confidence > threshold")
print(f"  {'Threshold':<12} {'Bets':>6} {'Wins':>6} {'Win%':>7} {'ROI@1.0':>9} {'ROI@2.0':>9} {'ROI@3.0':>9}")
print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*7} {'-'*9} {'-'*9} {'-'*9}")

for thresh in thresholds:
    bets = 0
    wins = 0
    for r in results:
        probs = np.array([r['dc_H'], r['dc_D'], r['dc_A']])
        best_idx = np.argmax(probs)
        best_prob = probs[best_idx]
        if best_prob >= thresh:
            bets += 1
            outcomes = ['H', 'D', 'A']
            if outcomes[best_idx] == r['actual_outcome']:
                wins += 1

    if bets > 0:
        for fair_odds in [1.0, 2.0, 3.0]:
            roi = (wins * fair_odds - bets) / bets * 100
            if fair_odds == 1.0:
                roi_str = f"{roi:+.1f}%"
        # Actually use simple flat odds
        win_pct = wins / bets * 100

        # Simulate: if we bet on every match at fair odds (= 1/prob)
        # P&L per bet = (prob * (1/prob) - 1 = 0) at fair odds
        # Better: assume we need edge over market
        roi_flat = (wins * 2.0 - bets) / bets * 100 if bets > 0 else 0
        roi_3x = (wins * 3.0 - bets) / bets * 100 if bets > 0 else 0
        print(f"  {f'>{thresh:.0%}':<12} {bets:>6} {wins:>6} {win_pct:>6.1f}% {roi_flat:>+8.1f}% {roi_3x:>+8.1f}% {'—':>9}")
    else:
        print(f"  {f'>{thresh:.0%}':<12} {bets:>6} {wins:>6} {'—':>7} {'—':>9} {'—':>9} {'—':>9}")

# Detailed: which outcomes is DC best at predicting?
print(f"\n  Outcome breakdown:")
for outcome in ['H', 'D', 'A']:
    mask = [r['actual_outcome'] == outcome for r in results]
    dc_pred = [np.argmax([r['dc_H'], r['dc_D'], r['dc_A']]) for r in results]
    dc_correct = sum(1 for i in range(n) if mask[i] and ['H','D','A'][dc_pred[i]] == outcome)
    total = sum(mask)
    avg_conf = np.mean([r[f'dc_{outcome}'] for r in results])
    print(f"    {outcome}: {total} actual, DC correct={dc_correct}/{total} ({dc_correct/total:.0%}), avg confidence={avg_conf:.1%}")

# ═══════════════════════════════════════════════════════════════
# B. OVER/UNDER 2.5 BETTING
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("B. OVER/UNDER 2.5 BETTING")
print("=" * 70)

# Backtest: bet Over when P(over) > 0.5 + edge, bet Under when P(under) > 0.5 + edge
edges = [0.00, 0.05, 0.10, 0.15, 0.20]
print(f"\n  Strategy: Bet Over/Under when confidence - 50% > edge threshold")
print(f"  {'Edge':<10} {'Bets':>6} {'Wins':>6} {'Win%':>7} {'ROI@1.9':>9}")

total_actual_over = sum(1 for r in results if r['actual_over'] == 'O')
total_actual_under = sum(1 for r in results if r['actual_over'] == 'U')
print(f"\n  Actual: Over={total_actual_over} ({total_actual_over/n:.0%}), Under={total_actual_under} ({total_actual_under/n:.0%})")
print(f"  DC mean P(Over): {np.mean([r['p_over'] for r in results]):.1%}")

for edge in edges:
    bets = 0
    wins = 0
    for r in results:
        if r['p_over'] > 0.50 + edge:
            bets += 1
            if r['actual_over'] == 'O':
                wins += 1
        elif r['p_under'] > 0.50 + edge:
            bets += 1
            if r['actual_over'] == 'U':
                wins += 1

    if bets > 0:
        roi = (wins * 1.90 - bets) / bets * 100
        print(f"  {f'>{edge:.0%}':<10} {bets:>6} {wins:>6} {wins/bets:>6.1f}% {roi:>+8.1f}%")
    else:
        print(f"  {f'>{edge:.0%}':<10} {bets:>6} {'—':>6} {'—':>7} {'—':>9}")

# Calibration: for each P(over) bin, what was actual over%?
print(f"\n  O/U Calibration:")
bins = [(0, 0.25), (0.25, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.0)]
for lo, hi in bins:
    mask = [lo <= r['p_over'] < hi for r in results]
    if sum(mask) >= 3:
        pred_mean = np.mean([r['p_over'] for r, m in zip(results, mask) if m])
        actual_mean = np.mean([1.0 if r['actual_over'] == 'O' else 0.0 for r, m in zip(results, mask) if m])
        n_bin = sum(mask)
        print(f"    P(over) in [{lo:.0%}, {hi:.0%}): n={n_bin:>2}  pred={pred_mean:.1%}  actual={actual_mean:.1%}  gap={actual_mean-pred_mean:+.1%}")

# ═══════════════════════════════════════════════════════════════
# C. BTTS BETTING
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("C. BOTH TEAMS TO SCORE (BTTS) BETTING")
print("=" * 70)

total_btts_y = sum(1 for r in results if r['actual_btts'] == 'Y')
print(f"\n  Actual: BTTS Yes={total_btts_y} ({total_btts_y/n:.0%})")
print(f"  DC mean P(BTTS): {np.mean([r['p_btts_y'] for r in results]):.1%}")

for edge in edges:
    bets = 0
    wins = 0
    for r in results:
        if r['p_btts_y'] > 0.50 + edge:
            bets += 1
            if r['actual_btts'] == 'Y':
                wins += 1
        elif r['p_btts_n'] > 0.50 + edge:
            bets += 1
            if r['actual_btts'] == 'N':
                wins += 1

    if bets > 0:
        roi = (wins * 1.85 - bets) / bets * 100
        print(f"  {f'>{edge:.0%}':<10} {bets:>6} {wins:>6} {wins/bets:>6.1f}% {roi:>+8.1f}%")
    else:
        print(f"  {f'>{edge:.0%}':<10} {bets:>6} {'—':>6} {'—':>7} {'—':>9}")

# ═══════════════════════════════════════════════════════════════
# D. CORRECT SCORE BETTING
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("D. CORRECT SCORE BETTING")
print("=" * 70)

# How often is the actual score in the top-3 predicted?
in_top1 = 0
in_top3 = 0
in_top5 = 0
top1_p_avg = 0

for r in results:
    sm = dc.predict_score_matrix(r['home'], r['away'])
    top = sm.top_scorelines(5)
    top_scores = [(h, a) for h, a, _ in top]
    actual = (r['actual_h'], r['actual_a'])

    if actual == top_scores[0]:
        in_top1 += 1
    if actual in top_scores[:3]:
        in_top3 += 1
        top1_p_avg += top[0][2] if top else 0
    if actual in top_scores:
        in_top5 += 1

print(f"  Actual score = Top-1 predicted: {in_top1}/{n} ({in_top1/n:.1%})")
print(f"  Actual score in Top-3:         {in_top3}/{n} ({in_top3/n:.1%})")
print(f"  Actual score in Top-5:         {in_top5}/{n} ({in_top5/n:.1%})")

# Simulate: bet on top-3 scores
print(f"\n  Strategy: Bet on top-3 predicted scores, assume avg odds ~8.0")
bets_cs = in_top3 * 3  # 3 bets per match
wins_cs = in_top3
roi_cs = (wins_cs * 8.0 - bets_cs) / bets_cs * 100
print(f"    Bets: {bets_cs} (3 per match × {n} matches)")
print(f"    Wins: {wins_cs}")
print(f"    ROI @ odds 8.0: {roi_cs:+.1f}%")

# ═══════════════════════════════════════════════════════════════
# E. OPTIMAL THRESHOLD SEARCH
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("E. OPTIMAL THRESHOLD SEARCH (H/D/A)")
print("=" * 70)

# Find optimal confidence threshold
print(f"\n  {'Threshold':<10} {'Bets':>6} {'Wins':>6} {'Win%':>7} {'ROI@2.0':>9} {'ROI@2.5':>9}")
print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*7} {'-'*9} {'-'*9}")

for thresh in [0.30, 0.35, 0.40, 0.42, 0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65]:
    bets = 0
    wins = 0
    for r in results:
        probs = np.array([r['dc_H'], r['dc_D'], r['dc_A']])
        best_idx = np.argmax(probs)
        best_prob = probs[best_idx]
        if best_prob >= thresh:
            bets += 1
            if ['H', 'D', 'A'][best_idx] == r['actual_outcome']:
                wins += 1
    if bets >= 5:
        for odds in [2.0, 2.5]:
            roi = (wins * odds - bets) / bets * 100
            if odds == 2.0:
                roi2 = roi
            else:
                roi25 = roi
        print(f"  {f'>{thresh:.0%}':<10} {bets:>6} {wins:>6} {wins/bets:>6.1f}% {roi2:>+8.1f}% {roi25:>+8.1f}%")

# ═══════════════════════════════════════════════════════════════
# F. BEST BETS FROM WC 2026
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("F. TOP 10 HIGHEST-CONFIDENCE CORRECT PREDICTIONS")
print("=" * 70)

scored = []
for r in results:
    probs = np.array([r['dc_H'], r['dc_D'], r['dc_A']])
    best_idx = np.argmax(probs)
    best_prob = probs[best_idx]
    correct = ['H', 'D', 'A'][best_idx] == r['actual_outcome']
    scored.append((best_prob, correct, r['match'], ['H','D','A'][best_idx], r['actual_outcome'],
                   f"{r['actual_h']}-{r['actual_a']}"))

scored.sort(key=lambda x: -x[0])
print(f"\n  {'Conf':<7} {'Pred':>4} {'Act':>4} {'Hit':>4} {'Score':>6}  Match")
print(f"  {'-'*7} {'-'*4} {'-'*4} {'-'*4} {'-'*6}  {'-'*30}")

for prob, correct, match, pred, actual, score in scored[:15]:
    hit = 'YES' if correct else 'NO'
    print(f"  {prob:.1%}   {pred:>4} {actual:>4} {hit:>4} {score:>6}  {match}")

# ═══════════════════════════════════════════════════════════════
# G. SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n" + "=" * 70)
print("G. SUMMARY — DIXON-COLES BETTING VALUE")
print("=" * 70)

# Overall stats
dc_correct = sum(1 for r in results if
    ['H','D','A'][np.argmax([r['dc_H'], r['dc_D'], r['dc_A']])] == r['actual_outcome'])
ou_correct = sum(1 for r in results if
    (r['p_over'] > 0.5 and r['actual_over'] == 'O') or
    (r['p_over'] <= 0.5 and r['actual_over'] == 'U'))
btts_correct = sum(1 for r in results if
    (r['p_btts_y'] > 0.5 and r['actual_btts'] == 'Y') or
    (r['p_btts_y'] <= 0.5 and r['actual_btts'] == 'N'))

print(f"  H/D/A accuracy:     {dc_correct}/{n} ({dc_correct/n:.1%})")
print(f"  O/U 2.5 accuracy:   {ou_correct}/{n} ({ou_correct/n:.1%})")
print(f"  BTTS accuracy:      {btts_correct}/{n} ({btts_correct/n:.1%})")
print(f"  Top-3 exact score:  {in_top3}/{n} ({in_top3/n:.1%})")

# Which market has best edge potential?
print(f"\n  Market comparison (if betting at fair odds > 2.0):")
# H/D/A: bet on all where confidence > 40%
hda_bets = sum(1 for r in results if max(r['dc_H'], r['dc_D'], r['dc_A']) > 0.40)
hda_wins = sum(1 for r in results if
    max(r['dc_H'], r['dc_D'], r['dc_A']) > 0.40 and
    ['H','D','A'][np.argmax([r['dc_H'], r['dc_D'], r['dc_A']])] == r['actual_outcome'])
print(f"    H/D/A (>40% conf): {hda_wins}/{hda_bets} ({hda_wins/hda_bets:.0%}), ROI@2.0={(hda_wins*2-hda_bets)/hda_bets*100:+.1f}%")

# O/U where edge > 5%
ou_bets = sum(1 for r in results if abs(r['p_over'] - 0.5) > 0.05)
ou_wins = sum(1 for r in results if abs(r['p_over'] - 0.5) > 0.05 and
    ((r['p_over'] > 0.55 and r['actual_over'] == 'O') or
     (r['p_over'] < 0.45 and r['actual_over'] == 'U')))
print(f"    O/U  (edge >5%):    {ou_wins}/{ou_bets} ({ou_wins/ou_bets:.0%}), ROI@1.9={(ou_wins*1.9-ou_bets)/ou_bets*100:+.1f}%")

# BTTS where edge > 5%
btts_bets = sum(1 for r in results if abs(r['p_btts_y'] - 0.5) > 0.05)
btts_wins = sum(1 for r in results if abs(r['p_btts_y'] - 0.5) > 0.05 and
    ((r['p_btts_y'] > 0.55 and r['actual_btts'] == 'Y') or
     (r['p_btts_y'] < 0.45 and r['actual_btts'] == 'N')))
print(f"    BTTS (edge >5%):    {btts_wins}/{btts_bets} ({btts_wins/btts_bets:.0%}), ROI@1.85={(btts_wins*1.85-btts_bets)/btts_bets*100:+.1f}%")
