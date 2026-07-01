"""Run production prediction for Japan vs Brazil — full pipeline with market fusion."""
import json
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from src.models.score_models import load_score_models
from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.market_fusion import normalize_three_way_odds, blend_with_market, confidence_from_inputs

print("Loading model...")
models = load_score_models()
dc_model = models["dixon_coles"]
print(f"  {len(dc_model.teams_)} teams, gamma={dc_model.gamma:.3f}, rho={dc_model.rho:.3f}")

# Team params
for team in ["Japan", "Brazil"]:
    a = dc_model.alpha.get(team, float("nan"))
    b = dc_model.beta.get(team, float("nan"))
    print(f"  {team:10s}  α={a:+.3f}  β={b:+.3f}")

# Build engine
engine = ProductionPredictionEngine(
    base_dc_model=dc_model,
    model_version="dc-v1",
    data_version="production",
    goal_multiplier=1.0,
)

# ── Representative market odds (Japan vs Brazil, neutral knockout) ──
# Typical bookmaker 1X2 decimal odds:
home_odds = 6.00   # Japan
draw_odds = 4.00   # Draw
away_odds = 1.55   # Brazil

print(f"\nInput odds (decimal):  H={home_odds:.2f}  D={draw_odds:.2f}  A={away_odds:.2f}")
print(f"  Implied probs (raw): H={1/home_odds*100:.1f}%  D={1/draw_odds*100:.1f}%  A={1/away_odds*100:.1f}%")

# ── Step 1: De-vig market odds ──
market_probs = normalize_three_way_odds(home_odds, draw_odds, away_odds)
print(f"  After de-vig:         H={market_probs['home']*100:.1f}%  D={market_probs['draw']*100:.1f}%  A={market_probs['away']*100:.1f}%")

# ── Step 2: Compute model score matrix ──
raw_matrix = engine.dynamic_service.predict_score_matrix("Japan", "Brazil", neutral=True)
score_matrix = engine.goal_calibrator.apply(raw_matrix)
model_probs = score_matrix.match_outcome_probs()
print(f"  Model probs:          H={model_probs['home']*100:.1f}%  D={model_probs['draw']*100:.1f}%  A={model_probs['away']*100:.1f}%")

# ── Step 3: Confidence from input quality ──
# For this hypothetical match: no lineups, no injuries, 3+ bookmakers assumed
quality = PredictionInputQuality(
    team_sample_home=45,
    team_sample_away=55,
    has_lineups=False,
    has_injuries=False,
    odds_snapshots=5,
    odds_volatility=0.20,
)
confidence = confidence_from_inputs(
    team_sample_home=quality.team_sample_home,
    team_sample_away=quality.team_sample_away,
    has_lineups=quality.has_lineups,
    has_injuries=quality.has_injuries,
    odds_snapshots=quality.odds_snapshots,
    odds_volatility=quality.odds_volatility,
)
print(f"\n  Confidence: {confidence:.3f}  (market weight: {1-confidence:.3f})")

# ── Step 4: Blend model + market ──
final_probs = blend_with_market(model_probs, market_probs, confidence)
print(f"  Fused probs:          H={final_probs['home']*100:.1f}%  D={final_probs['draw']*100:.1f}%  A={final_probs['away']*100:.1f}%")

# ── Step 5: Re-score with calibrated matrix (fused probs override the predictive dist) ──
# Use the engine's full predict() for the snapshot
snapshot = engine.predict(
    match_id="japan-vs-brazil-knockout",
    home_team="Japan",
    away_team="Brazil",
    odds=(home_odds, draw_odds, away_odds),
    quality=quality,
    neutral=True,
)

# ── Output ──
print()
print("=" * 64)
print("  Japan vs Brazil — World Cup Knockout (Neutral Venue)")
print("  Full Production Pipeline: Dixon-Coles + Market Fusion")
print("=" * 64)

probs = snapshot.final_probs
home_xg, away_xg = score_matrix.expected_goals()

print(f"""
  ┌─────────────────────────────────────────────────────────────┐
  │  Result (1X2)       Model     Market    → Fused             │
  │  ─────────────────  ────────  ────────    ────────          │
  │  Japan Win           {model_probs['home']*100:5.1f}%    {market_probs['home']*100:5.1f}%      {probs['home']*100:5.1f}%            │
  │  Draw                {model_probs['draw']*100:5.1f}%    {market_probs['draw']*100:5.1f}%      {probs['draw']*100:5.1f}%            │
  │  Brazil Win          {model_probs['away']*100:5.1f}%    {market_probs['away']*100:5.1f}%      {probs['away']*100:5.1f}%            │
  └─────────────────────────────────────────────────────────────┘
""")

print(f"  Expected Goals:        Japan {home_xg:.2f}  ·  Brazil {away_xg:.2f}  ·  Total {home_xg+away_xg:.2f}")
print(f"  Model Confidence:      {confidence*100:.0f}%  (weight on model vs market)")
print()

print("  Most Likely Scorelines (from raw DC matrix):")
for h, a, prob in score_matrix.top_scorelines(10):
    bar = "█" * int(prob * 400)
    print(f"    {h}-{a}  {prob*100:5.2f}%  {bar}")

print()
print("  Goal Markets:")
for thresh in [0.5, 1.5, 2.5, 3.5]:
    over, under = score_matrix.over_under_probability(thresh)
    print(f"    Over {thresh}: {over*100:5.1f}%  (Under: {under*100:5.1f}%)")
btts, no_btts = score_matrix.btts_probability()
print(f"    BTTS:        {btts*100:5.1f}%  (No BTTS: {no_btts*100:5.1f}%)")

print(f"\n  ═══  API Snapshot (vip tier) ═══")
print(json.dumps(snapshot.to_public_dict(tier="vip"), indent=2, ensure_ascii=False))
