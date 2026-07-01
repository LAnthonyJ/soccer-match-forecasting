"""
Evaluate model confidence on WC 2026 known results.
"""
import pandas as pd
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.features.elo import elo_to_expected_goals

df = pd.read_csv('data/predictions/wc2026_predictions.csv')
done = df[df['actual'].notna()].copy()
print(f'Matches with results: {len(done)}')
print()

eps = 1e-15
act_map = {'H': 0, 'D': 1, 'A': 2}

# ═══════════════════════════════════════════════════════════════
# 1. H/D/A LOGLOSS
# ═══════════════════════════════════════════════════════════════
print('=' * 65)
print('1. MATCH OUTCOME (H/D/A) CALIBRATION')
print('=' * 65)

model_lls, mkt_lls = [], []
model_correct, mkt_correct = 0, 0

for _, r in done.iterrows():
    mp = np.array([r['model_H'], r['model_D'], r['model_A']])
    mk = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    act_idx = act_map.get(r['actual'], 0)

    model_lls.append(-np.log(np.clip(mp[act_idx], eps, 1)))
    mkt_lls.append(-np.log(np.clip(mk[act_idx], eps, 1)))

    if np.argmax(mp) == act_idx:
        model_correct += 1
    if np.argmax(mk) == act_idx:
        mkt_correct += 1

print(f'  Model LogLoss:     {np.mean(model_lls):.4f}')
print(f'  Market LogLoss:    {np.mean(mkt_lls):.4f}')
print(f'  Model - Market:    {np.mean(model_lls) - np.mean(mkt_lls):+.4f}')
print(f'  Model Accuracy:    {model_correct}/{len(done)} ({model_correct/len(done):.0%})')
print(f'  Market Accuracy:   {mkt_correct}/{len(done)} ({mkt_correct/len(done):.0%})')

# ═══════════════════════════════════════════════════════════════
# 2. CONFIDENCE BINS
# ═══════════════════════════════════════════════════════════════
print()
print('=' * 65)
print('2. CONFIDENCE RELIABILITY')
print('=' * 65)
print(f'  {"Bin":<16} {"N":>4} {"Pred%":>7} {"Win%":>7} {"Gap":>7}')

bins = [(0, 0.25), (0.25, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)]
for lo, hi in bins:
    mask = (done['model_H'] >= lo) & (done['model_H'] < hi)
    if mask.sum() > 0:
        pred_avg = done.loc[mask, 'model_H'].mean()
        actual_wins = (done.loc[mask, 'actual'] == 'H').sum()
        actual_pct = actual_wins / mask.sum()
        print(f'  H-conf {lo:.0%}-{hi:.0%}:       {mask.sum():>4}  {pred_avg:>6.1%}  {actual_pct:>6.1%}  {(actual_pct-pred_avg):>+6.1%}')

for lo, hi in bins:
    mask = (done['model_A'] >= lo) & (done['model_A'] < hi)
    if mask.sum() > 0:
        pred_avg = done.loc[mask, 'model_A'].mean()
        actual_wins = (done.loc[mask, 'actual'] == 'A').sum()
        actual_pct = actual_wins / mask.sum()
        print(f'  A-conf {lo:.0%}-{hi:.0%}:       {mask.sum():>4}  {pred_avg:>6.1%}  {actual_pct:>6.1%}  {(actual_pct-pred_avg):>+6.1%}')

for lo, hi in bins:
    mask = (done['model_D'] >= lo) & (done['model_D'] < hi)
    if mask.sum() > 0:
        pred_avg = done.loc[mask, 'model_D'].mean()
        actual_draws = (done.loc[mask, 'actual'] == 'D').sum()
        actual_pct = actual_draws / mask.sum()
        print(f'  D-conf {lo:.0%}-{hi:.0%}:       {mask.sum():>4}  {pred_avg:>6.1%}  {actual_pct:>6.1%}  {(actual_pct-pred_avg):>+6.1%}')

# ═══════════════════════════════════════════════════════════════
# 3. BETTING P&L
# ═══════════════════════════════════════════════════════════════
print()
print('=' * 65)
print('3. BETTING P&L (Edge >= 10% rule)')
print('=' * 65)

bets = done[done['bet_amount'] > 0].copy()
total_stake = bets['bet_amount'].sum()
total_return = 0
wins = 0

for _, b in bets.iterrows():
    actual_outcome = b['actual']
    bet_outcome = b['best_edge_outcome']
    won = actual_outcome == bet_outcome
    if won:
        total_return += b['bet_amount'] * b['bet_odds']
        wins += 1
    result_str = f"WIN +{b['bet_amount'] * b['bet_odds']:.0f}" if won else 'LOSE'
    print(f'  {b["match"]:<30} Bet {bet_outcome} @ {b["bet_odds"]:.2f} '
          f'Y{b["bet_amount"]:.0f} -> {result_str} (Edge={b["best_edge"]:+.1%})')

profit = total_return - total_stake
roi = profit / total_stake * 100 if total_stake > 0 else 0

if len(bets) > 0:
    print(f'\n  Bets: {len(bets)} | Wins: {wins}/{len(bets)} ({wins/len(bets):.0%})')
    print(f'  Stake: Y{total_stake:.0f} | Return: Y{total_return:.0f} | P&L: Y{profit:+.0f} ({roi:+.1f}%)')
else:
    print('  No bets placed.')

# ═══════════════════════════════════════════════════════════════
# 4. PER-MATCH SCORECARD
# ═══════════════════════════════════════════════════════════════
print()
print('=' * 65)
print('4. PER-MATCH SCORECARD')
print('=' * 65)
print(f'  {"Date":<12} {"Match":<30} {"Pred":>3} {"Act":>3} {"Ok":>2} {"Score":>6} {"Mdl%":>7} {"Mkt%":>7} {"Edge":>7}')

for _, r in done.iterrows():
    mp = np.array([r['model_H'], r['model_D'], r['model_A']])
    mk = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    edge = mp - mk
    act_idx = act_map.get(r['actual'], 0)
    pred_outcome = ['H', 'D', 'A'][np.argmax(mp)]
    correct = 'Y' if pred_outcome == r['actual'] else 'N'
    score = f"{int(r['home_score'])}-{int(r['away_score'])}"
    print(f'  {r["date"]} {r["match"]:<30} {pred_outcome:>3} {r["actual"]:>3} {correct:>2} {score:>6} {mp[act_idx]:>6.1%} {mk[act_idx]:>6.1%} {edge.max():>+6.1%}')

# ═══════════════════════════════════════════════════════════════
# 5. GOAL PREDICTION (ELO Poisson)
# ═══════════════════════════════════════════════════════════════
print()
print('=' * 65)
print('5. GOAL PREDICTION (ELO Poisson baseline)')
print('=' * 65)
print(f'  {"Match":<30} {"Score":>6} {"E[H]":>6} {"E[A]":>6} {"E[T]":>6} {"MAE":>5}')

mae_h, mae_a, mae_t = [], [], []
for _, r in done.iterrows():
    elo_est = (r['model_H'] - r['model_A']) * 200
    lh, la = elo_to_expected_goals(elo_est, neutral=True)
    et = lh + la
    hg, ag = int(r['home_score']), int(r['away_score'])
    m_h = abs(lh - hg)
    m_a = abs(la - ag)
    m_t = abs(et - (hg + ag))
    mae_h.append(m_h)
    mae_a.append(m_a)
    mae_t.append(m_t)
    print(f'  {r["match"]:<30} {f"{hg}-{ag}":>6} {lh:>6.2f} {la:>6.2f} {et:>6.2f} {m_t:>5.2f}')

print(f'\n  MAE Home: {np.mean(mae_h):.2f}  MAE Away: {np.mean(mae_a):.2f}  MAE Total: {np.mean(mae_t):.2f}')

# ═══════════════════════════════════════════════════════════════
# 6. DIRECTIONAL EDGE ANALYSIS
# ═══════════════════════════════════════════════════════════════
print()
print('=' * 65)
print('6. EDGE DIRECTION ANALYSIS')
print('=' * 65)

model_beats_mkt = 0
mkt_beats_model = 0
for _, r in done.iterrows():
    mp = np.array([r['model_H'], r['model_D'], r['model_A']])
    mk = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    act_idx = act_map.get(r['actual'], 0)
    if mp[act_idx] > mk[act_idx]:
        model_beats_mkt += 1
    else:
        mkt_beats_model += 1

print(f'  Model > Market on actual outcome: {model_beats_mkt}/{len(done)} matches')
print(f'  Market > Model on actual outcome: {mkt_beats_model}/{len(done)} matches')

# Check edge signs
pos_edge_correct = 0
neg_edge_correct = 0
pos_edge_total = 0
neg_edge_total = 0
for _, r in done.iterrows():
    mp = np.array([r['model_H'], r['model_D'], r['model_A']])
    mk = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    edge = mp - mk
    best_edge_idx = np.argmax(edge)
    if edge[best_edge_idx] > 0.05:
        pos_edge_total += 1
        if best_edge_idx == act_map.get(r['actual'], 0):
            pos_edge_correct += 1
    elif edge[best_edge_idx] < 0:
        neg_edge_total += 1
        if best_edge_idx == act_map.get(r['actual'], 0):
            neg_edge_correct += 1

if pos_edge_total > 0:
    print(f'  Edge > 5% signals: {pos_edge_correct}/{pos_edge_total} correct ({pos_edge_correct/pos_edge_total:.0%})')
if neg_edge_total > 0:
    print(f'  Edge < 0 signals:  {neg_edge_correct}/{neg_edge_total} correct ({neg_edge_correct/neg_edge_total:.0%})')

print()
print('=' * 65)
print('SUMMARY')
print(f'  Model LogLoss: {np.mean(model_lls):.4f} vs Market {np.mean(mkt_lls):.4f}')
print(f'  Total P&L: Y{profit:+.0f} ({roi:+.1f}%) on {len(bets)} bets')
print(f'  Model correct: {model_correct}/{len(done)} ({model_correct/len(done):.0%})')
