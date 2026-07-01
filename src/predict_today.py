"""
Generate full ensemble predictions for specific upcoming matches.
"""
import sys, os, pickle
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline
from src.models.score_models import load_score_models, elo_poisson_score_matrix

DATA_DIR = Path(__file__).parent.parent / 'data'

# ---- Load model ----
with open('outputs/models/full_model.pkl', 'rb') as f:
    m = pickle.load(f)
ensemble = m['ensemble']; xgb_m = m['xgb_models']
lgb_m = m['lgb_models']; elo_m = m['elo_model']; cols = m['feature_cols']

# ---- Build features on ALL data (not just played) ----
print("Loading & cleaning...")
df = load_and_clean(str(DATA_DIR / 'raw' / 'results.csv'))

print("Building features on ALL matches...")
# Don't filter is_played — we need features for fixtures too
full_df = build_all_features(df)

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

# ---- Matches to predict ----
TARGETS = [
    ('Uruguay', 'Spain', 5.40, 3.75, 1.66),
    ('Egypt', 'Iran', 2.47, 2.50, 3.95),
    ('New Zealand', 'Belgium', 12.50, 7.90, 1.18),
    ('Norway', 'France', 6.40, 5.10, 1.42),
    ('Senegal', 'Iraq', 1.18, 7.30, 14.50),
    ('Cape Verde', 'Saudi Arabia', 2.60, 3.40, 2.66),
]

# ---- Predict ----
print("\n" + "=" * 60)
print("FULL ENSEMBLE PREDICTIONS vs MARKET")
print("=" * 60)

results = []
for home, away, odd_h, odd_d, odd_a in TARGETS:
    m = full_df[(full_df['home_team'] == home) &
                (full_df['away_team'] == away) &
                (full_df['date'].dt.year == 2026)]
    if m.empty:
        print(f"\n{home} vs {away}: NOT FOUND in feature matrix")
        continue

    m = predict_elo_baseline(m, elo_m)
    for c in cols:
        if c not in m.columns:
            m[c] = 0
    X = np.nan_to_num(m[cols].values, nan=0.0)

    xgb_p = {o: xgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    lgb_p = {o: lgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    elo_p_arr = {
        'home': np.array([m['prob_h_elo'].values[0]]),
        'draw': np.array([m['prob_d_elo'].values[0]]),
        'away': np.array([m['prob_a_elo'].values[0]]),
    }
    xgb_p_arr = {o: np.array([xgb_p[o]]) for o in ['home', 'draw', 'away']}
    lgb_p_arr = {o: np.array([lgb_p[o]]) for o in ['home', 'draw', 'away']}
    ens = ensemble.predict({'elo': elo_p_arr, 'xgb': xgb_p_arr, 'lgb': lgb_p_arr})

    model_prob = np.array([ens['home'][0], ens['draw'][0], ens['away'][0]])
    mkt_raw = np.array([1 / odd_h, 1 / odd_d, 1 / odd_a])
    mkt_prob = mkt_raw / mkt_raw.sum()
    edge = model_prob - mkt_prob

    best_idx = np.argmax(edge)
    outcomes = ['H', 'D', 'A']

    print(f"\n{'─' * 50}")
    print(f"  {home} vs {away}")
    print(f"  Model:  H={model_prob[0]:.1%}  D={model_prob[1]:.1%}  A={model_prob[2]:.1%}")
    print(f"  Market: H={mkt_prob[0]:.1%}  D={mkt_prob[1]:.1%}  A={mkt_prob[2]:.1%}")
    print(f"  Edge:   H={edge[0]:+.1%}  D={edge[1]:+.1%}  A={edge[2]:+.1%}")
    print(f"  Odds:   {odd_h:.2f} / {odd_d:.2f} / {odd_a:.2f}")

    # Score predictions
    try:
        score_models = load_score_models()
        dc = score_models.get('dixon_coles')
        if dc:
            sm = dc.predict_score_matrix(home, away, neutral=True)
            e_h, e_a = sm.expected_goals()
            over_p, _ = sm.over_under_probability(2.5)
            btts_p, _ = sm.btts_probability()
            top3 = sm.top_scorelines(3)
            print(f"  Score:  E[goals]={e_h:.2f}-{e_a:.2f}  O2.5={over_p:.1%}  BTTS={btts_p:.1%}")
            print(f"  Top:    {' | '.join(f'{h}-{a} {p:.1%}' for h, a, p in top3)}")
    except Exception as e:
        print(f"  Score:  unavailable ({e})")

    results.append({
        'home': home, 'away': away,
        'model': model_prob, 'mkt': mkt_prob, 'edge': edge,
        'odds': (odd_h, odd_d, odd_a),
    })

# ---- Kelly Betting ----
print(f"\n{'=' * 60}")
print("KELLY CRITERION (1/4 Kelly, ¥1,000 bankroll)")
print("=" * 60)

bankroll = 1000.0
frac = 0.25
total_stake = 0
bets = []

for r in results:
    outcomes = ['H', 'D', 'A']
    # Find best outcome by edge
    best_idx = np.argmax(r['edge'])
    best = outcomes[best_idx]
    p = r['model'][best_idx]
    odds = r['odds'][best_idx]
    edge = r['edge'][best_idx]
    b_param = odds - 1

    # Full Kelly: f = (p*b - q)/b
    kelly_full = max(0, (p * b_param - (1 - p)) / b_param) if b_param > 0 else 0
    kelly_frac = kelly_full * frac
    stake = bankroll * kelly_frac
    stake = min(stake, bankroll * 0.05)  # cap 5%
    ev = p * odds - 1  # expected value

    if edge > 0.01 and kelly_full > 0 and stake >= 5:
        total_stake += stake
        bets.append({
            'match': f"{r['home']} vs {r['away']}",
            'outcome': best, 'odds': odds, 'prob': p,
            'edge': edge, 'kelly': kelly_full, 'stake': stake, 'ev': ev,
        })
        print(f"\n  {r['home']} vs {r['away']}")
        print(f"  BET ¥{stake:.0f} on {best} @ {odds:.2f}")
        print(f"  Prob={p:.1%} Edge={edge:+.1%} EV={ev:+.1%} Kelly={kelly_full:.1%}")
        print(f"  Win returns: ¥{stake * odds:.0f}")
    else:
        print(f"\n  {r['home']} vs {r['away']}: PASS")
        print(f"  Best edge: {best} {edge:+.1%} (insufficient)")

if bets:
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"  Bets placed: {len(bets)}")
    print(f"  Total staked: ¥{total_stake:.0f}")
    print(f"  If all win: ¥{sum(b['stake'] * b['odds'] for b in bets):.0f}")
    print(f"  Expected return: ¥{sum(b['stake'] * (1 + b['ev']) for b in bets):.0f}")
    print(f"  Cash reserve: ¥{bankroll - total_stake:.0f}")
else:
    print(f"\n  No positive-edge bets found.")
