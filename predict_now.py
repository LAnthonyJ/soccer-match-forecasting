"""Quick prediction for South Africa vs Canada WC 2026."""
import json
import os
import sys
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, '.')
if not os.environ.get("SPORTMONKS_API_KEY"):
    raise RuntimeError("SPORTMONKS_API_KEY must be set in the environment")

from src.data.sportmonks_client import get_fixture
from src.data.sportmonks_adapter import SportmonksAdapter
from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot
from src.features.lineup_signals import build_lineup_signals
from src.models.lineup_adjustment import build_lineup_lambda_adjustment, LineupAdjustmentConfig
from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.score_models import load_score_models

FIXTURE_ID = 19606960

# 1. Fetch
adapter = SportmonksAdapter()
fixt = get_fixture(FIXTURE_ID, includes='participants;scores;league')
match = adapter.parse_match(fixt)
odds = adapter.average_odds(FIXTURE_ID)

# Team IDs
hid = aid = None
for p in fixt.get('participants', []):
    meta = p.get('meta', {})
    if isinstance(meta, dict):
        if meta.get('location') == 'home':
            hid = p['id']
        elif meta.get('location') == 'away':
            aid = p['id']

print(f'Match: {match.home_team} vs {match.away_team}')
print(f'Tournament: {match.tournament} | Kickoff: {match.kickoff_utc}')
print(f'Odds: H={odds[0]:.2f} D={odds[1]:.2f} A={odds[2]:.2f}')
print(f'Team IDs: home={hid} away={aid}')

# 2. Prematch snapshot
snap = build_sportmonks_prematch_snapshot(
    match, fixture_id=FIXTURE_ID, home_team_id=hid, away_team_id=aid,
)
print(f'Data: quality={snap.data_quality} lineups={len(snap.lineups)} strengths={len(snap.player_strengths)}')

# 3. Lineup signals
lineups_by_team = {}
for p in snap.lineups:
    lineups_by_team.setdefault(p.team, []).append(p)
signals = build_lineup_signals(
    match,
    lineups=lineups_by_team or None,
    injuries=snap.injuries or None,
    player_strengths=snap.player_strengths or None,
    lineup_type=snap.lineup_type if snap.has_lineups() else 'none',
)
for side in ('home', 'away'):
    s = signals[side]
    print(f'  {side}: quality={s.data_quality} missing={len(s.missing_players)}')

# 4. Lambda adjustment
adj = build_lineup_lambda_adjustment(signals['home'], signals['away'], LineupAdjustmentConfig())
print(f'Adjustment: H_atk={adj.home_attack_delta:+.3f} H_def={adj.home_defense_delta:+.3f} A_atk={adj.away_attack_delta:+.3f} A_def={adj.away_defense_delta:+.3f}')

# 5. Predict
score_models = load_score_models()
dc = score_models['dixon_coles']
engine = ProductionPredictionEngine(
    base_dc_model=dc, model_version='dynamic-dc-v1',
    data_version='live', goal_multiplier=1.10,
)
snapshot = engine.predict(
    match_id=match.match_id, home_team=match.home_team, away_team=match.away_team,
    odds=odds,
    quality=PredictionInputQuality(
        team_sample_home=max(len(snap.player_strengths) // 2, 1),
        team_sample_away=max(len(snap.player_strengths) // 2, 1),
        has_lineups=snap.has_lineups(),
        has_injuries=snap.has_injuries(),
        odds_snapshots=21, odds_volatility=0.15,
    ),
    neutral=match.neutral,
    lineup_adjustment=adj if adj.has_signal() else None,
    prematch_snapshot=snap,
)

# 6. Print
vip = snapshot.to_public_dict(tier='vip')
print()
print('=' * 55)
print(f'  {match.home_team} vs {match.away_team}')
print(f'  Model: {vip["model_version"]} | Confidence: {vip["confidence"]:.0%}')
print('=' * 55)
print(f'  Home:  {vip["probabilities"]["home"]:.1%}')
print(f'  Draw:  {vip["probabilities"]["draw"]:.1%}')
print(f'  Away:  {vip["probabilities"]["away"]:.1%}')
if vip.get('market_probs'):
    mp = vip['market_probs']
    print(f'  ───────────────────')
    print(f'  Mkt H: {mp["home"]:.1%}  Mkt D: {mp["draw"]:.1%}  Mkt A: {mp["away"]:.1%}')
    e = vip.get('market_edge', {})
    if e:
        print(f'  Edge:  H={e["home"]:+.1%}  D={e["draw"]:+.1%}  A={e["away"]:+.1%}')
print(f'  ───────────────────')
print(f'  xG:       {vip["expected_goals"]["home"]:.2f} - {vip["expected_goals"]["away"]:.2f}')
print(f'  Over 2.5: {vip["over25"]:.1%}    BTTS: {vip["btts"]:.1%}')
print(f'  Top scorelines:')
for h, a, p in vip['top_scorelines'][:8]:
    bar = chr(0x2588) * int(p * 30)
    print(f'    {h}-{a}: {p:.1%} {bar}')
print('=' * 55)
