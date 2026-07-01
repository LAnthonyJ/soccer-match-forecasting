"""
Full backtest: 2022 World Cup with trained odds model + strategy optimization.
"""
import sys, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))
os.environ['PYTHONIOENCODING'] = 'utf-8'

import pandas as pd
import numpy as np
from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.features.elo import compute_elo_history, get_pre_match_elo
from src.models.train import predict_elo_baseline, load_models
from src.betting.strategy import (
    generate_bets, simulate_results, optimize_strategy, StrategyConfig
)

# Load our trained odds model
import pickle
ODDS_MODEL_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'outputs', 'models', 'odds_model.pkl')


def load_odds_model():
    with open(ODDS_MODEL_PATH, 'rb') as f:
        return pickle.load(f)


def predict_odds_from_model(matches_df):
    """Use trained model to predict Pinnacle-level odds for all matches."""
    model_data = load_odds_model()
    models = model_data['models']
    feature_cols = model_data['feature_cols']

    df = matches_df.copy()
    df['elo_diff_sq'] = df['elo_diff'] ** 2
    df['elo_diff_abs'] = np.abs(df['elo_diff'])
    df['elo_home_sq'] = df['elo_home'] ** 2
    df['elo_away_sq'] = df['elo_away'] ** 2
    df['elo_ratio'] = df['elo_home'] / (df['elo_away'] + 1)

    X = np.nan_to_num(df[feature_cols].values, nan=1500)
    probs = {}
    for outcome in ['home', 'draw', 'away']:
        probs[outcome] = np.clip(models[outcome].predict(X), 0.02, 0.95)
    total = probs['home'] + probs['draw'] + probs['away']
    for outcome in ['home', 'draw', 'away']:
        probs[outcome] /= total

    odds = {}
    for outcome in ['home', 'draw', 'away']:
        inflated = np.clip(probs[outcome] * 1.05, 0.03, 0.97)
        odds[outcome] = 1.0 / inflated
    return odds


def main():
    print("=" * 60)
    print("BACKTEST: 2022 World Cup with Trained Odds Model")
    print("=" * 60)

    # 1. Load data
    print("\n[1/4] Preparing data...")
    df = load_and_clean('data/raw/results.csv')
    full_df = build_all_features(df)
    wc_df = full_df[(full_df['date'].dt.year == 2022) &
                    (full_df['is_world_cup'] == 1) &
                    (full_df['is_played'])].copy()
    print(f"2022 WC: {len(wc_df)} matches")

    # 2. Predict outcomes
    print("\n[2/4] Running match predictions...")
    models = load_models()
    ensemble = models['ensemble']
    xgb_models = models['xgb_models']
    lgb_models = models['lgb_models']
    elo_model = models['elo_model']
    feature_cols = models['feature_cols']

    wc_df = predict_elo_baseline(wc_df, elo_model)
    X_wc = np.nan_to_num(wc_df[[c for c in feature_cols if c in wc_df.columns]].values, nan=0.0)

    elo_preds = {
        'home': wc_df['prob_h_elo'].values,
        'draw': wc_df['prob_d_elo'].values,
        'away': wc_df['prob_a_elo'].values,
    }
    xgb_preds = {
        'home': xgb_models['home'].predict_proba(X_wc)[:, 1],
        'draw': xgb_models['draw'].predict_proba(X_wc)[:, 1],
        'away': xgb_models['away'].predict_proba(X_wc)[:, 1],
    }
    lgb_preds = {
        'home': lgb_models['home'].predict_proba(X_wc)[:, 1],
        'draw': lgb_models['draw'].predict_proba(X_wc)[:, 1],
        'away': lgb_models['away'].predict_proba(X_wc)[:, 1],
    }

    all_preds = {'elo': elo_preds, 'xgb': xgb_preds, 'lgb': lgb_preds}
    ensemble_preds = ensemble.predict(all_preds)

    # 3. Get odds from trained model
    print("\n[3/4] Generating odds from trained model...")
    odds = predict_odds_from_model(wc_df)
    print(f"  Using trained LightGBM odds model (MAE~0.05 vs Pinnacle)")

    # 4. Optimize strategy
    print("\n[4/4] Optimizing betting strategy...")
    outcomes = []
    for _, row in wc_df.iterrows():
        if row['home_score'] > row['away_score']:
            outcomes.append('H')
        elif row['home_score'] == row['away_score']:
            outcomes.append('D')
        else:
            outcomes.append('A')

    opt_result = optimize_strategy(ensemble_preds, odds, all_preds, outcomes)

    # 5. Show final recommendation
    print("\n" + "=" * 60)
    print("RECOMMENDED STRATEGY PARAMETERS")
    print("=" * 60)
    best = opt_result['best_config']
    print(f"  Kelly fraction: {best.kelly_fraction:.2f}")
    print(f"  Min EV threshold: {best.min_ev*100:.0f}%")
    print(f"  Max model disagreement: {best.max_model_std:.3f}")
    print(f"  Max total exposure: {best.max_total_exposure*100:.0f}%")
    print(f"  Expected ROI: {opt_result['best_result']['roi']*100:.1f}%")
    print(f"  Expected Sharpe: {opt_result['best_result']['sharpe']:.2f}")
    print(f"  Max drawdown: {opt_result['best_result']['max_drawdown']*100:.1f}%")
    print(f"  Bets placed: {opt_result['best_result']['n_bets']} on "
          f"{opt_result['best_result']['n_matches_bet']} matches")

    return opt_result


if __name__ == '__main__':
    main()
