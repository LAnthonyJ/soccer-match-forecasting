"""
Dixon-Coles betting backtest on 12 WC 2026 matches with real Pinnacle odds.
Compares DC model edge vs market, simulates real-money betting.
"""
import sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.score_models import load_score_models

DATA_DIR = Path(__file__).parent.parent / 'data'

print("=" * 65)
print("DIXON-COLES vs PINNACLE — 12 MATCHES WITH REAL ODDS")
print("=" * 65)

# ── Load data & features ─────────────────────────────────────────
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

# Load Dixon-Coles
score_models = load_score_models()
dc = score_models['dixon_coles']

# Load predictions CSV
pred_df = pd.read_csv(DATA_DIR / 'predictions' / 'wc2026_predictions.csv')
done = pred_df[pred_df['actual'].notna()].copy()

# ── Run Dixon-Coles on each match ───────────────────────────────
print("\nRunning Dixon-Coles predictions...\n")
results = []

TEAM_NAME_FIX = {'Czechia': 'Czech Republic', 'Curacao': 'Curaçao'}

for _, row in done.iterrows():
    home = row['match'].split(' vs ')[0]
    away = row['match'].split(' vs ')[1]
    home = TEAM_NAME_FIX.get(home, home)
    away = TEAM_NAME_FIX.get(away, away)
    actual_h = int(row['home_score'])
    actual_a = int(row['away_score'])
    actual_outcome = row['actual']

    # Market odds & probs
    odds_h = row['odds_H']
    odds_d = row['odds_D']
    odds_a = row['odds_A']
    mkt_raw = np.array([1/odds_h, 1/odds_d, 1/odds_a])
    mkt_probs = mkt_raw / mkt_raw.sum()

    reversed_search = False
    # Find in feature matrix
    match = full_df[(full_df['home_team'] == home) &
                    (full_df['away_team'] == away)]
    if len(match) > 1:
        match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        match = full_df[(full_df['home_team'] == away) &
                        (full_df['away_team'] == home)]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
        if not match.empty:
            reversed_search = True
    if match.empty:
        match = full_df[(full_df['home_team'].str.contains(home[:4], na=False)) &
                        (full_df['away_team'].str.contains(away[:4], na=False))]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        match = full_df[(full_df['home_team'].str.contains(away[:4], na=False)) &
                        (full_df['away_team'].str.contains(home[:4], na=False))]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
        if not match.empty:
            reversed_search = True
    if match.empty:
        print(f"  SKIP: {home} vs {away}")
        continue

    # WC = always neutral venue
    neutral = True

    # Dixon-Coles prediction
    sm = dc.predict_score_matrix(home, away, neutral=neutral)

    # H/D/A from DC
    hda = sm.match_outcome_probs()
    dc_probs = np.array([hda['home'], hda['draw'], hda['away']])
    dc_edge = dc_probs - mkt_probs

    # O/U & BTTS from DC
    over_p, under_p = sm.over_under_probability(2.5)
    btts_p, _ = sm.btts_probability()
    e_h, e_a = sm.expected_goals()
    top3 = sm.top_scorelines(3)

    # Actual
    actual_total = actual_h + actual_a
    actual_over = 'O' if actual_total > 2.5 else 'U'
    actual_btts = 'Y' if (actual_h > 0 and actual_a > 0) else 'N'
    actual_score = f"{actual_h}-{actual_a}"
    actual_p = sm.probability(actual_h, actual_a)

    results.append({
        'match': f"{home} vs {away}",
        'actual': actual_outcome, 'score': actual_score,
        'actual_h': actual_h, 'actual_a': actual_a,
        'actual_over': actual_over, 'actual_btts': actual_btts,
        'odds': (odds_h, odds_d, odds_a),
        'mkt_H': mkt_probs[0], 'mkt_D': mkt_probs[1], 'mkt_A': mkt_probs[2],
        'dc_H': dc_probs[0], 'dc_D': dc_probs[1], 'dc_A': dc_probs[2],
        'dc_edge_H': dc_edge[0], 'dc_edge_D': dc_edge[1], 'dc_edge_A': dc_edge[2],
        'over_p': over_p, 'under_p': under_p,
        'btts_p': btts_p, 'e_h': e_h, 'e_a': e_a,
        'actual_p': actual_p,
        'top3': top3,
        'old_best_edge': row['best_edge'],
        'old_best_outcome': row['best_edge_outcome'],
        'old_bet_amount': row['bet_amount'],
        'old_bet_odds': row['bet_odds'],
    })

n = len(results)
print(f"  Matched: {n}/12")

# ═══════════════════════════════════════════════════════════════
# PER-MATCH DETAIL
# ═══════════════════════════════════════════════════════════════
print("=" * 65)
print("A. PER-MATCH DIXON-COLES vs PINNACLE")
print("=" * 65)

act_map = {'H': 0, 'D': 1, 'A': 2}
eps = 1e-15

# H/D/A
dc_ll = 0; mkt_ll = 0; dc_correct = 0; mkt_correct = 0

for r in results:
    dc_p = np.array([r['dc_H'], r['dc_D'], r['dc_A']])
    mk_p = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    edge = dc_p - mk_p
    act_idx = act_map[r['actual']]

    dc_ll += -np.log(np.clip(dc_p[act_idx], eps, 1))
    mkt_ll += -np.log(np.clip(mk_p[act_idx], eps, 1))
    if np.argmax(dc_p) == act_idx: dc_correct += 1
    if np.argmax(mk_p) == act_idx: mkt_correct += 1

    best_edge_idx = np.argmax(edge)
    best_edge_val = edge.max()
    dc_pred = ['H','D','A'][np.argmax(dc_p)]
    mkt_pred = ['H','D','A'][np.argmax(mk_p)]
    edge_outcome = ['H','D','A'][best_edge_idx]

    print(f"\n  {r['match']}")
    print(f"    Actual: {r['actual']} ({r['score']})  |  DC: {dc_pred}  Mkt: {mkt_pred}")
    print(f"    DC:    H={dc_p[0]:.1%}  D={dc_p[1]:.1%}  A={dc_p[2]:.1%}")
    print(f"    Mkt:   H={mk_p[0]:.1%}  D={mk_p[1]:.1%}  A={mk_p[2]:.1%}")
    print(f"    Edge:  H={edge[0]:+.1%}  D={edge[1]:+.1%}  A={edge[2]:+.1%}  → Best={edge_outcome} ({best_edge_val:+.1%})")
    print(f"    Score: E[goals]={r['e_h']:.2f}-{r['e_a']:.2f}  P(actual={r['score']})={r['actual_p']:.3%}")
    print(f"    O/U:   P(Over)={r['over_p']:.1%}  Actual={r['actual_over']}")
    print(f"    BTTS:  P(Yes)={r['btts_p']:.1%}  Actual={r['actual_btts']}")
    top_str = ' | '.join(f"{h}-{a} {p:.1%}" for h,a,p in r['top3'])
    print(f"    Top-3: {top_str}")

# ═══════════════════════════════════════════════════════════════
# B. COMPARISON TABLE
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("B. MODEL COMPARISON")
print("=" * 65)

print(f"\n  {'Metric':<25} {'DC Score':>10} {'Mkt (Pinnacle)':>15} {'Old HDA Ens':>13}")
print(f"  {'-'*25} {'-'*10} {'-'*15} {'-'*13}")
print(f"  {'H/D/A LogLoss':<25} {dc_ll/n:>10.4f} {mkt_ll/n:>15.4f} {np.mean([-np.log(np.clip(r['dc_H'] if r['actual']=='H' else r['dc_D'] if r['actual']=='D' else r['dc_A'],eps,1)) for r in results]):>13}")  # placeholder, use old CSVs model probs

# Match-by-match H/D/A edge vs market
edge_wins = sum(1 for r in results if np.max([r['dc_edge_H'], r['dc_edge_D'], r['dc_edge_A']]) > 0.05)
dc_beats_mkt = sum(1 for r in results if
    np.array([r['dc_H'], r['dc_D'], r['dc_A']])[act_map[r['actual']]] >
    np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])[act_map[r['actual']]])

print(f"  {'DC > Mkt on actual outcome':<25} {dc_beats_mkt:>10}/{n}")
print(f"  {'DC accuracy':<25} {dc_correct:>10}/{n} ({dc_correct/n:.0%})")
print(f"  {'Market accuracy':<25} {mkt_correct:>10}/{n} ({mkt_correct/n:.0%})")

# ═══════════════════════════════════════════════════════════════
# C. BETTING SIMULATION (Edge >= 10%)
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("C. BETTING SIMULATION — Edge ≥ 10% RULE")
print("=" * 65)

bankroll = 1000.0
kelly_frac = 0.25
cap = 0.05

# H/D/A Betting with DC
print("\n  ── H/D/A Market ──")
hda_bets = []
for r in results:
    dc_p = np.array([r['dc_H'], r['dc_D'], r['dc_A']])
    mk_p = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
    edge = dc_p - mk_p
    best_idx = np.argmax(edge)
    best_edge = edge[best_idx]
    best_outcome = ['H','D','A'][best_idx]
    odds = r['odds'][best_idx]
    prob = dc_p[best_idx]

    if best_edge >= 0.10:
        b_param = odds - 1
        kelly_full = max(0, (prob * b_param - (1 - prob)) / b_param) if b_param > 0 else 0
        kelly_frac_val = kelly_full * kelly_frac
        stake = min(bankroll * kelly_frac_val, bankroll * cap)
        won = best_outcome == r['actual']
        ret = stake * odds if won else 0
        hda_bets.append({
            'match': r['match'], 'bet': best_outcome, 'odds': odds,
            'stake': stake, 'edge': best_edge, 'won': won, 'return': ret,
            'prob': prob, 'mkt_prob': mk_p[best_idx],
        })

if hda_bets:
    for b in hda_bets:
        result_str = f"WIN +Y{b['return']:.0f}" if b['won'] else 'LOSE'
        print(f"  {b['match']:<30} Bet {b['bet']} @ {b['odds']:.2f} "
              f"Y{b['stake']:.0f} -> {result_str} "
              f"(DC={b['prob']:.1%} Mkt={b['mkt_prob']:.1%} Edge={b['edge']:+.1%})")

    total_stake = sum(b['stake'] for b in hda_bets)
    total_return = sum(b['return'] for b in hda_bets)
    wins = sum(1 for b in hda_bets if b['won'])
    print(f"\n  Total: {wins}/{len(hda_bets)} wins | Stake Y{total_stake:.0f} | Return Y{total_return:.0f} | P&L Y{total_return-total_stake:+.0f} ({(total_return-total_stake)/total_stake*100:+.1f}%)")
else:
    print("  No H/D/A bets (edge < 10% on all matches)")

# Compare: what did the OLD XGB+LGB ensemble bet?
print("\n  ── OLD HDA Ensemble (XGB+LGB) Bets ──")
old_bets_total = 0
old_return = 0
old_wins = 0
for _, r in done.iterrows():
    if r['bet_amount'] > 0:
        old_bets_total += r['bet_amount']
        won = r['best_edge_outcome'] == r['actual']
        if won:
            old_return += r['bet_amount'] * r['bet_odds']
            old_wins += 1
        result_str = f"WIN +Y{r['bet_amount']*r['bet_odds']:.0f}" if won else 'LOSE'
        print(f"  {r['match']:<30} Bet {r['best_edge_outcome']} @ {r['bet_odds']:.2f} "
              f"Y{r['bet_amount']:.0f} → {result_str} (Edge={r['best_edge']:+.1%})")

if old_bets_total > 0:
    print(f"\n  Total: {old_wins}/{len([1 for _,r in done.iterrows() if r['bet_amount']>0])} wins | "
          f"Stake Y{old_bets_total:.0f} | Return Y{old_return:.0f} | "
          f"P&L Y{old_return-old_bets_total:+.0f} ({(old_return-old_bets_total)/old_bets_total*100:+.1f}%)")

# ═══════════════════════════════════════════════════════════════
# D. O/U & BTTS Edge simulation
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("D. OVER/UNDER 2.5 & BTTS — Model Prediction Accuracy")
print("=" * 65)

ou_correct = sum(1 for r in results if
    (r['over_p'] > 0.5 and r['actual_over'] == 'O') or
    (r['over_p'] <= 0.5 and r['actual_over'] == 'U'))
btts_correct = sum(1 for r in results if
    (r['btts_p'] > 0.5 and r['actual_btts'] == 'Y') or
    (r['btts_p'] <= 0.5 and r['actual_btts'] == 'N'))

print(f"\n  Match           Score  P(Over)  Act  P(BTTS)  Act")
print(f"  {'-'*45}")
for r in results:
    ou_ok = (r['over_p'] > 0.5 and r['actual_over'] == 'O') or (r['over_p'] <= 0.5 and r['actual_over'] == 'U')
    bt_ok = (r['btts_p'] > 0.5 and r['actual_btts'] == 'Y') or (r['btts_p'] <= 0.5 and r['actual_btts'] == 'N')
    print(f"  {r['match']:<30} {r['score']:>5}  {r['over_p']:.1%}   {r['actual_over']}  {'✓' if ou_ok else '✗'}  "
          f"  {r['btts_p']:.1%}   {r['actual_btts']}  {'✓' if bt_ok else '✗'}")

print(f"\n  O/U accuracy:  {ou_correct}/{n} ({ou_correct/n:.0%})")
print(f"  BTTS accuracy: {btts_correct}/{n} ({btts_correct/n:.0%})")

# ═══════════════════════════════════════════════════════════════
# E. SUMMARY
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("E. FINAL SUMMARY")
print("=" * 65)

dc_hda_ll = dc_ll / n
mkt_hda_ll = mkt_ll / n

print(f"\n  H/D/A LogLoss:   DC={dc_hda_ll:.4f}  Pinnacle={mkt_hda_ll:.4f}  Diff={dc_hda_ll-mkt_hda_ll:+.4f}")
print(f"  H/D/A Accuracy:  DC={dc_correct}/{n} ({dc_correct/n:.0%})  Market={mkt_correct}/{n} ({mkt_correct/n:.0%})")
print(f"  DC > Mkt on correct outcome: {dc_beats_mkt}/{n}")

if hda_bets:
    dc_pl = total_return - total_stake
    print(f"\n  DC H/D/A Bets (Edge≥10%): {len(hda_bets)} bets, P&L=Y{dc_pl:+.0f} ({dc_pl/total_stake*100:+.1f}%)")

old_pl = old_return - old_bets_total
old_n = len([1 for _,r in done.iterrows() if r['bet_amount']>0])
if old_n > 0:
    print(f"  OLD Ens H/D/A Bets:        {old_n} bets, P&L=Y{old_pl:+.0f} ({old_pl/old_bets_total*100:+.1f}%)")
    print(f"\n  >> DC improved P&L by Y{dc_pl - old_pl:+.0f} vs old ensemble" if hda_bets else "")

# Top score accuracy
in_top3 = sum(1 for r in results if (r['actual_h'], r['actual_a']) in [(h,a) for h,a,_ in r['top3']])
in_top1 = sum(1 for r in results if (r['actual_h'], r['actual_a']) == r['top3'][0][:2])
print(f"\n  Exact score in Top-1: {in_top1}/{n} ({in_top1/n:.0%})")
print(f"  Exact score in Top-3: {in_top3}/{n} ({in_top3/n:.0%})")
