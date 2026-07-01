"""
Train a highly reliable odds prediction model using LightGBM.
Uses cross-validated (Pinnacle/bet365/Marathon) ground truth odds.
"""
import pandas as pd
import numpy as np
import lightgbm as lgb
import pickle
import warnings
import sys, io, os
os.environ['PYTHONIOENCODING'] = 'utf-8'
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
warnings.filterwarnings('ignore')

PROCESSED_DIR = Path(__file__).parent.parent.parent / 'data' / 'processed'
OUTPUT_DIR = Path(__file__).parent.parent.parent / 'outputs' / 'models'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_training_data(tiers: list = None):
    """Load unified training dataset, optionally filtering by quality tier."""
    if tiers is None:
        tiers = ['T1_gold', 'T2_silver']

    df = pd.read_parquet(PROCESSED_DIR / 'unified_odds_training.parquet')

    # Filter to specified quality tiers
    df = df[df['quality_tier'].isin(tiers)].copy()
    print(f"Loaded {len(df)} matches (tiers: {tiers})")

    # Sort by date
    df = df.sort_values('date').reset_index(drop=True)
    return df


def compute_elo_for_training(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute ELO ratings for all teams in the training dataset.
    Uses the same ELO system as our prediction model.
    """
    from src.features.elo import compute_elo_history, get_pre_match_elo

    # Prepare data for ELO computation
    elo_input = df[['date', 'home_team', 'away_team',
                     'home_score', 'away_score']].copy()
    elo_input.columns = ['date', 'home_team', 'away_team',
                          'home_score', 'away_score']
    elo_input['tournament'] = 'league'
    elo_input['neutral'] = False  # Assume home advantage for all

    print("Computing ELO ratings...")
    elo_history = compute_elo_history(elo_input)
    df = get_pre_match_elo(elo_history, df)

    return df


def build_features(df: pd.DataFrame) -> tuple:
    """Build feature matrix for odds prediction."""
    df = df.dropna(subset=['elo_diff']).copy()

    # Core features
    feature_cols = [
        'elo_home', 'elo_away', 'elo_diff',
    ]

    # Add quadratic terms for non-linear relationships
    df['elo_diff_sq'] = df['elo_diff'] ** 2
    df['elo_diff_abs'] = np.abs(df['elo_diff'])
    df['elo_home_sq'] = df['elo_home'] ** 2
    df['elo_away_sq'] = df['elo_away'] ** 2

    # Ratio features
    df['elo_ratio'] = df['elo_home'] / (df['elo_away'] + 1)

    extra_cols = ['elo_diff_sq', 'elo_diff_abs', 'elo_home_sq',
                   'elo_away_sq', 'elo_ratio']
    feature_cols.extend(extra_cols)

    # Target: implied probabilities from ground truth odds
    X = df[feature_cols].values
    y_home = df['prob_h_gt'].values
    y_draw = df['prob_d_gt'].values
    y_away = df['prob_a_gt'].values

    # Check for NaN
    valid = ~(np.isnan(X).any(axis=1) | np.isnan(y_home) | np.isnan(y_draw) | np.isnan(y_away))
    X = X[valid]
    y_home = y_home[valid]
    y_draw = y_draw[valid]
    y_away = y_away[valid]

    print(f"Features: {X.shape[1]} columns, {X.shape[0]} samples")
    print(f"Target range: H=[{y_home.min():.3f}, {y_home.max():.3f}], "
          f"D=[{y_draw.min():.3f}, {y_draw.max():.3f}], "
          f"A=[{y_away.min():.3f}, {y_away.max():.3f}]")

    return X, y_home, y_draw, y_away, feature_cols


def train_odds_model():
    """Main training pipeline."""
    print("=" * 60)
    print("TRAINING ODDS PREDICTION MODEL")
    print("=" * 60)

    # 1. Load data
    print("\n[1/4] Loading training data...")
    df = load_training_data(tiers=['T1_gold', 'T2_silver'])

    # 2. Compute ELO
    print("\n[2/4] Computing ELO ratings...")
    df = compute_elo_for_training(df)

    # 3. Build features
    print("\n[3/4] Building features...")
    X, y_h, y_d, y_a, feature_cols = build_features(df)

    # 4. Train/test split (temporal: train on older, test on newer)
    split_idx = int(len(X) * 0.85)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_h_train, y_h_test = y_h[:split_idx], y_h[split_idx:]
    y_d_train, y_d_test = y_d[:split_idx], y_d[split_idx:]
    y_a_train, y_a_test = y_a[:split_idx], y_a[split_idx:]

    print(f"\n[4/4] Training models...")
    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    # Train separate models for each outcome
    params = {
        'objective': 'regression',
        'metric': 'mae',
        'boosting_type': 'gbdt',
        'num_leaves': 63,
        'learning_rate': 0.05,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'min_data_in_leaf': 50,
        'verbose': -1,
        'n_estimators': 1000,
    }

    models = {}
    predictions = {}
    metrics = {}

    for name, y_tr, y_te in [('home', y_h_train, y_h_test),
                               ('draw', y_d_train, y_d_test),
                               ('away', y_a_train, y_a_test)]:
        print(f"\n--- {name.upper()} ---")

        model = lgb.LGBMRegressor(**params)
        model.fit(
            X_train, y_tr,
            eval_set=[(X_test, y_te)],
            eval_metric='mae',
            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(100)],
        )

        y_pred = model.predict(X_test)
        mae = mean_absolute_error(y_te, y_pred)
        r2 = r2_score(y_te, y_pred)
        corr = np.corrcoef(y_te, y_pred)[0, 1]

        print(f"  MAE: {mae:.4f}, R2: {r2:.4f}, Corr: {corr:.4f}")

        # Feature importance
        importance = model.feature_importances_
        top_idx = np.argsort(importance)[-5:][::-1]
        print(f"  Top features:")
        for idx in top_idx:
            print(f"    {feature_cols[idx]}: {importance[idx]:.4f}")

        models[name] = model
        predictions[name] = y_pred
        metrics[name] = {'mae': mae, 'r2': r2, 'corr': corr}

    # Overall evaluation
    print(f"\n=== OVERALL ===")
    avg_mae = np.mean([m['mae'] for m in metrics.values()])
    avg_r2 = np.mean([m['r2'] for m in metrics.values()])
    print(f"Average MAE: {avg_mae:.4f}")
    print(f"Average R2: {avg_r2:.4f}")

    # Calibration check: predicted vs actual
    for name in ['home', 'draw', 'away']:
        yp = predictions[name]
        yt = y_h_test if name == 'home' else (y_d_test if name == 'draw' else y_a_test)
        # Bin predictions and check mean actual
        bins = np.percentile(yp, [0, 20, 40, 60, 80, 100])
        print(f"\n  {name} calibration:")
        for i in range(len(bins)-1):
            mask = (yp >= bins[i]) & (yp < bins[i+1])
            if mask.sum() > 10:
                print(f"    pred [{bins[i]:.3f}, {bins[i+1]:.3f}): "
                      f"actual mean={yt[mask].mean():.3f}, n={mask.sum()}")

    # Save model
    model_path = OUTPUT_DIR / 'odds_model.pkl'
    with open(model_path, 'wb') as f:
        pickle.dump({
            'models': models,
            'feature_cols': feature_cols,
            'metrics': metrics,
        }, f)
    print(f"\nModel saved to {model_path}")

    return models, metrics


if __name__ == '__main__':
    train_odds_model()
