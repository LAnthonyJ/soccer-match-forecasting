"""
Generate betting red-line cards: minimum odds required for each outcome.

For each match, outputs the minimum decimal odds at which betting becomes
profitable, accounting for model uncertainty with a safety margin.

Usage:
    python -m src.betting.redline
    -> outputs/bets/redline_YYYY-MM-DD.csv
"""
import sys, os, io, pickle
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline, load_models

# Safety margin: multiply model probability by this before computing min odds.
# 0.90 = 10% safety buffer. Higher = more conservative (fewer bets, safer).
SAFETY_MARGIN = 0.90

# Minimum probability floor: don't even consider outcomes below this
MIN_PROBABILITY = 0.12


def compute_redline(probs: dict, safety: float = SAFETY_MARGIN) -> dict:
    """
    Convert model probabilities to minimum odds thresholds.

    Formula: min_odds = 1 / (p * safety)
    - p = ensemble model probability
    - safety = margin to account for estimation error

    Returns dict with 'home', 'draw', 'away' minimum odds.
    """
    redline = {}
    for outcome in ['home', 'draw', 'away']:
        p = probs[outcome]
        if p < MIN_PROBABILITY:
            redline[outcome] = None  # Don't bet
        else:
            adjusted_p = p * safety
            redline[outcome] = round(1.0 / adjusted_p, 2)
    return redline


def generate_redline_card(matches_df, ensemble_preds, elo_data):
    """Generate a full redline card for all upcoming matches."""
    cards = []
    for i, (_, row) in enumerate(matches_df.iterrows()):
        probs = {
            'home': ensemble_preds['home'][i],
            'draw': ensemble_preds['draw'][i],
            'away': ensemble_preds['away'][i],
        }
        redline = compute_redline(probs)

        cards.append({
            'date': str(row['date'])[:10],
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'elo_home': round(row['elo_home']),
            'elo_away': round(row['elo_away']),
            'elo_diff': round(row['elo_diff']),
            'prob_h': round(probs['home'], 3),
            'prob_d': round(probs['draw'], 3),
            'prob_a': round(probs['away'], 3),
            'min_odds_h': redline['home'],
            'min_odds_d': redline['draw'],
            'min_odds_a': redline['away'],
        })

    return pd.DataFrame(cards)


def print_redline_card(df: pd.DataFrame):
    """Pretty-print the redline card."""
    for _, row in df.iterrows():
        flag_h = "  " if row['min_odds_h'] else "X "
        flag_d = "  " if row['min_odds_d'] else "X "
        flag_a = "  " if row['min_odds_a'] else "X "

        print(f"\n{'='*65}")
        print(f"{row['date']} | {row['home_team']} vs {row['away_team']}")
        print(f"  ELO: {row['elo_home']} vs {row['elo_away']} (diff: {row['elo_diff']:+d})")
        print(f"  Model: H={row['prob_h']:.1%} D={row['prob_d']:.1%} A={row['prob_a']:.1%}")
        print(f"  {'─'*55}")
        print(f"  {flag_h} HOME: bet if odds > {row['min_odds_h']:<6}  "
              f"{flag_d} DRAW: bet if odds > {row['min_odds_d']:<6}  "
              f"{flag_a} AWAY: bet if odds > {row['min_odds_a']:<6}")
        print(f"  (X = skip, probability too low)")


def main():
    print("=" * 65)
    print("BETTING RED-LINE CARD")
    print(f"Safety margin: {SAFETY_MARGIN*100:.0f}% | Min probability: {MIN_PROBABILITY*100:.0f}%")
    print("=" * 65)

    # Load models
    print("\nLoading models...")
    try:
        models = load_models()
    except FileNotFoundError:
        from src.pipeline import run_training_pipeline
        run_training_pipeline()
        models = load_models()

    ensemble = models['ensemble']
    xgb_models = models['xgb_models']
    lgb_models = models['lgb_models']
    elo_model = models['elo_model']
    feature_cols = models['feature_cols']

    # Load data
    print("Loading data...")
    df = load_and_clean('data/raw/results.csv')
    full_df = build_all_features(df)

    # Get unplayed 2026 WC matches
    fixtures = full_df[(full_df['date'].dt.year == 2026) &
                       (full_df['is_world_cup'] == 1) &
                       (~full_df['is_played'])].copy()

    if len(fixtures) == 0:
        print("\nNo upcoming World Cup matches found.")
        return

    print(f"Found {len(fixtures)} upcoming matches\n")

    # Predict
    fixtures = predict_elo_baseline(fixtures, elo_model)
    X_fix = np.nan_to_num(fixtures[[c for c in feature_cols if c in fixtures.columns]].values, nan=0.0)

    elo_preds = {
        'home': fixtures['prob_h_elo'].values,
        'draw': fixtures['prob_d_elo'].values,
        'away': fixtures['prob_a_elo'].values,
    }
    xgb_preds = {
        'home': xgb_models['home'].predict_proba(X_fix)[:, 1],
        'draw': xgb_models['draw'].predict_proba(X_fix)[:, 1],
        'away': xgb_models['away'].predict_proba(X_fix)[:, 1],
    }
    lgb_preds = {
        'home': lgb_models['home'].predict_proba(X_fix)[:, 1],
        'draw': lgb_models['draw'].predict_proba(X_fix)[:, 1],
        'away': lgb_models['away'].predict_proba(X_fix)[:, 1],
    }

    all_preds = {'elo': elo_preds, 'xgb': xgb_preds, 'lgb': lgb_preds}
    ensemble_preds = ensemble.predict(all_preds)

    # Generate card
    card = generate_redline_card(fixtures, ensemble_preds, fixtures)
    print_redline_card(card)

    # Save
    today = datetime.now().strftime('%Y-%m-%d')
    output_dir = __import__('pathlib').Path(__file__).parent.parent.parent / 'outputs' / 'bets'
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f'redline_{today}.csv'
    card.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\nRedline card saved to: {out_path}")

    # Summary
    n_h = card['min_odds_h'].notna().sum()
    n_d = card['min_odds_d'].notna().sum()
    n_a = card['min_odds_a'].notna().sum()
    print(f"\nSummary: {n_h} home / {n_d} draw / {n_a} away betting opportunities")


if __name__ == '__main__':
    main()
