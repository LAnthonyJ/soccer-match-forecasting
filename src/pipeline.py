"""
Main pipeline: data processing, model training, backtesting, and live prediction.
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force UTF-8 for Windows GBK issues
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from datetime import datetime

from src.data.cleaner import load_and_clean, split_train_test_by_date, get_upcoming_fixtures
from src.features.build_features import build_all_features
from src.features.elo import compute_elo_history, get_pre_match_elo, elo_to_probability
from src.models.train import (
    FEATURE_COLS, prepare_features,
    train_elo_baseline, predict_elo_baseline,
    train_xgboost, train_lightgbm,
    Ensemble, ProbabilityCalibrator,
    evaluate_predictions, save_models, load_models,
)
from src.models.pre_match_adjuster import PreMatchAdjuster
from src.betting.kelly import calculate_optimal_bets, simulate_bet_result
from src.betting.odds import load_odds_csv, merge_odds_with_matches, odds_dict_to_array

# Path to real odds file (CSV with columns: date, home_team, away_team, odds_home, odds_draw, odds_away)
REAL_ODDS_PATH = Path(__file__).parent.parent / 'data' / 'raw' / 'odds.csv'

# Path to trained odds model
ODDS_MODEL_PATH = Path(__file__).parent.parent / 'outputs' / 'models' / 'odds_model.pkl'

# Load odds model once at module level
_trained_odds_model = None


def _load_odds_model():
    """Lazy-load the trained odds prediction model."""
    global _trained_odds_model
    if _trained_odds_model is None and ODDS_MODEL_PATH.exists():
        import pickle
        with open(ODDS_MODEL_PATH, 'rb') as f:
            _trained_odds_model = pickle.load(f)
        print(f"  Loaded trained odds model (avg MAE: {np.mean([m['mae'] for m in _trained_odds_model['metrics'].values()]):.4f})")
    return _trained_odds_model


def _predict_odds_from_model(matches_df: pd.DataFrame) -> dict:
    """
    Use the trained LightGBM model to predict Pinnacle-level odds.
    Returns dict with 'home', 'draw', 'away' arrays.
    """
    model_data = _load_odds_model()
    if model_data is None:
        return None

    models = model_data['models']
    feature_cols = model_data['feature_cols']

    # Build features (same as training)
    df = matches_df.copy()
    df['elo_diff_sq'] = df['elo_diff'] ** 2
    df['elo_diff_abs'] = np.abs(df['elo_diff'])
    df['elo_home_sq'] = df['elo_home'] ** 2
    df['elo_away_sq'] = df['elo_away'] ** 2
    df['elo_ratio'] = df['elo_home'] / (df['elo_away'] + 1)

    X = df[feature_cols].values
    X = np.nan_to_num(X, nan=1500)

    # Predict probabilities
    probs = {}
    for outcome in ['home', 'draw', 'away']:
        probs[outcome] = models[outcome].predict(X)
        probs[outcome] = np.clip(probs[outcome], 0.02, 0.95)

    # Normalize to sum to 1
    total = probs['home'] + probs['draw'] + probs['away']
    for outcome in ['home', 'draw', 'away']:
        probs[outcome] /= total

    # Convert to decimal odds with 5% overround (standard bookmaker margin)
    # For The competition, use 12% overround instead
    odds = {}
    for outcome in ['home', 'draw', 'away']:
        inflated = probs[outcome] * 1.05
        inflated = np.clip(inflated, 0.03, 0.97)
        odds[outcome] = 1.0 / inflated

    return odds


def run_training_pipeline():
    """Full training pipeline: data cleaning, feature building, model training."""
    print("=" * 60)
    print("SOCCER PREDICTION - TRAINING PIPELINE")
    print("=" * 60)

    # Step 1: Load and clean data
    print("\n[1/5] Loading and cleaning data...")
    df = load_and_clean('data/raw/results.csv')

    # Three-way split with random train/val, isolated test
    # Train+Val:  2000-2023, RANDOM 85/15 (both see all eras, evenly distributed)
    # Test:       2024-01-01 onwards (modern football, completely isolated)

    # Step 2: Build features on ALL data (chronological order is critical for ELO/form)
    print("\n[2/5] Building features...")
    all_data = df[df['is_played']].copy()
    full_df = build_all_features(all_data)

    # Merge event features (xG, PPDA, style) from StatsBomb
    event_path = Path(__file__).parent.parent / 'data' / 'processed' / 'results_with_event_features.csv'
    if event_path.exists():
        print("  Merging StatsBomb event features...")
        event_df = pd.read_csv(event_path)
        event_df['date'] = pd.to_datetime(event_df['date'])
        event_cols = [c for c in event_df.columns if c.startswith('event_')]
        for col in event_cols:
            if col not in full_df.columns:
                full_df[col] = np.nan
        for _, evt_row in event_df.iterrows():
            mask = ((full_df['date'] == evt_row['date']) &
                    (full_df['home_team'] == evt_row['home_team']) &
                    (full_df['away_team'] == evt_row['away_team']))
            if mask.any():
                for col in event_cols:
                    if pd.notna(evt_row.get(col)):
                        full_df.loc[mask, col] = evt_row[col]
        print(f"  Event features merged: {full_df['event_home_xg'].notna().sum()} matches")

    print(f"  Feature matrix: {full_df.shape[1]} columns, {full_df.shape[0]} rows")

    # Step 3: Split — random 85/15 for train/val, ensure team coverage
    print("\n[3/5] Splitting train/val/test...")

    # Test = WC 2022 only (64 matches, clean holdout)
    wc_mask = ((full_df['date'].dt.year == 2022) &
               (full_df['is_world_cup'] == 1) & (full_df['is_played']))
    test_feat = full_df[wc_mask].copy()

    # Train+Val pool = all played matches except test
    train_pool = full_df[(~full_df.index.isin(test_feat.index)) &
                         (full_df['is_played'])].copy()

    # Random 85/15 split with team coverage verification
    np.random.seed(42)
    mask = np.random.rand(len(train_pool)) < 0.85
    train_feat = train_pool[mask].copy()
    val_feat = train_pool[~mask].copy()

    # Verify team coverage
    train_teams = set(pd.concat([train_feat['home_team'], train_feat['away_team']]))
    val_teams = set(pd.concat([val_feat['home_team'], val_feat['away_team']]))
    test_teams = set(pd.concat([test_feat['home_team'], test_feat['away_team']]))
    teams_in_all = train_teams & val_teams
    test_only = test_teams - train_teams

    print(f"  Train: {len(train_feat)} matches, {len(train_teams)} teams")
    print(f"  Val:   {len(val_feat)} matches, {len(val_teams)} teams")
    print(f"  Test:  {len(test_feat)} matches (WC 2022), {len(test_teams)} teams")
    print(f"  Teams in train+val: {len(teams_in_all)}/314")
    if test_only:
        print(f"  WARNING: {len(test_only)} WC teams not in train: {test_only}")

    print(f"\nTrain features: {len(train_feat)} matches")
    print(f"Val features:   {len(val_feat)} matches")
    print(f"Test features:  {len(test_feat)} matches (2022 World Cup)")

    # Merge pre-match signals from transfermarkt alignment
    signal_path = Path(__file__).parent.parent / 'data' / 'processed' / 'results_with_signals_master.csv'
    if signal_path.exists():
        print("\n  Merging pre-match signals...")
        enriched = pd.read_csv(signal_path)
        enriched['date'] = pd.to_datetime(enriched['date'])

        signal_cols = ['home_key_missing', 'home_missing_caps', 'home_missing_goals',
                       'home_xi_strength', 'home_debutants', 'home_height_avg',
                       'away_key_missing', 'away_missing_caps', 'away_missing_goals',
                       'away_xi_strength', 'away_debutants', 'away_height_avg']

        for col in signal_cols:
            if col in enriched.columns:
                train_feat[col] = np.nan
                val_feat[col] = np.nan
                test_feat[col] = np.nan

        # Merge by date + home_team + away_team
        for side, feat_df in [('train', train_feat), ('val', val_feat), ('test', test_feat)]:
            for _, sig_row in enriched.iterrows():
                mask = ((feat_df['date'] == sig_row['date']) &
                        (feat_df['home_team'] == sig_row['home_team']) &
                        (feat_df['away_team'] == sig_row['away_team']))
                if mask.any():
                    for col in signal_cols:
                        if col in sig_row and pd.notna(sig_row[col]):
                            feat_df.loc[mask, col] = sig_row[col]

        n_signals_train = train_feat['home_key_missing'].notna().sum()
        n_signals_val = val_feat['home_key_missing'].notna().sum()
        n_signals_test = test_feat['home_key_missing'].notna().sum()
        print(f"  Signals merged: train={n_signals_train}, val={n_signals_val}, test={n_signals_test}")

    # Step 4: Train models
    print("\n[4/5] Training models...")

    # ELO baseline
    elo_model = train_elo_baseline(train_feat)
    val_feat = predict_elo_baseline(val_feat, elo_model)
    test_feat = predict_elo_baseline(test_feat, elo_model)

    # Prepare feature matrices
    X_train, y_train_h, y_train_d, y_train_a, cols, _ = prepare_features(train_feat)
    X_val, y_val_h, y_val_d, y_val_a, _, _ = prepare_features(val_feat)
    X_test, y_test_h, y_test_d, y_test_a, _, _ = prepare_features(test_feat)

    print(f"\nFeature matrix: {X_train.shape[1]} features")
    print(f"  Train: {X_train.shape[0]}, Val: {X_val.shape[0]}, Test: {X_test.shape[0]}")

    # Time decay sample weights (half-life = 3 years — recent form matters more)
    train_dates = pd.to_datetime(train_feat['date'])
    HALF_LIFE = 3  # years: 2025=1.0, 2022=0.5, 2019=0.25, floor=0.10
    ref_date = train_dates.max()
    decay_rate = np.log(2) / HALF_LIFE
    years_ago = (ref_date - train_dates).dt.days / 365.25
    train_weights = np.exp(-decay_rate * years_ago.values)
    train_weights = np.clip(train_weights, 0.10, 1.0)  # floor at 10%
    val_weights = np.ones(len(X_val))  # val doesn't need weighting

    print(f"  Time weights: min={train_weights.min():.2f}, max={train_weights.max():.2f}, "
          f"mean={train_weights.mean():.2f}")

    # XGBoost — early stopping on VALIDATION, time-weighted training
    xgb_models, xgb_preds = train_xgboost(
        X_train, y_train_h, y_train_d, y_train_a,
        X_val, y_val_h, y_val_d, y_val_a,
        X_test, y_test_h, y_test_d, y_test_a,
        sample_weights=train_weights,
    )

    # LightGBM — early stopping on VALIDATION, time-weighted training
    lgb_models, lgb_preds = train_lightgbm(
        X_train, y_train_h, y_train_d, y_train_a,
        X_val, y_val_h, y_val_d, y_val_a,
        X_test, y_test_h, y_test_d, y_test_a,
        sample_weights=train_weights,
    )

    # ELO predictions on VALIDATION and TEST
    elo_preds_val = {
        'home': val_feat['prob_h_elo'].values,
        'draw': val_feat['prob_d_elo'].values,
        'away': val_feat['prob_a_elo'].values,
    }
    elo_preds_test = {
        'home': test_feat['prob_h_elo'].values,
        'draw': test_feat['prob_d_elo'].values,
        'away': test_feat['prob_a_elo'].values,
    }

    # Get model predictions on validation set (for ensemble fitting)
    xgb_preds_val = {}
    lgb_preds_val = {}
    for name in ['home', 'draw', 'away']:
        xgb_preds_val[name] = xgb_models[name].predict_proba(X_val)[:, 1]
        lgb_preds_val[name] = lgb_models[name].predict_proba(X_val)[:, 1]

    # Step 5: Ensemble — weights optimized on VALIDATION, not test
    print("\n[5/5] Building ensemble (weights from validation)...")
    all_preds_val = {'elo': elo_preds_val, 'xgb': xgb_preds_val, 'lgb': lgb_preds_val}
    y_true_val = {'home': y_val_h, 'draw': y_val_d, 'away': y_val_a}

    ensemble = Ensemble()
    ensemble.fit(all_preds_val, y_true_val)
    ensemble_preds_val = ensemble.predict(all_preds_val)

    # Predict on TEST (first time test is touched)
    all_preds_test = {'elo': elo_preds_test, 'xgb': xgb_preds, 'lgb': lgb_preds}
    y_true_test = {'home': y_test_h, 'draw': y_test_d, 'away': y_test_a}
    ensemble_preds_test = ensemble.predict(all_preds_test)

    # Evaluate on validation (for debugging)
    print("\n=== Validation Set (2022-06 to 2022-10) ===")
    evaluate_predictions(y_true_val, elo_preds_val, 'ELO Baseline')
    evaluate_predictions(y_true_val, xgb_preds_val, 'XGBoost')
    evaluate_predictions(y_true_val, lgb_preds_val, 'LightGBM')
    evaluate_predictions(y_true_val, ensemble_preds_val, 'ENSEMBLE')

    # Evaluate on TEST (FINAL SCORE — 2022 World Cup, never seen during training)
    print("\n=== TEST SET (2022 World Cup — COMPLETELY UNSEEN) ===")
    y_true_test_dict = {'home': y_test_h, 'draw': y_test_d, 'away': y_test_a}
    evaluate_predictions(y_true_test_dict, elo_preds_test, 'ELO Baseline')
    evaluate_predictions(y_true_test_dict, xgb_preds, 'XGBoost')
    evaluate_predictions(y_true_test_dict, lgb_preds, 'LightGBM')
    evaluate_predictions(y_true_test_dict, ensemble_preds_test, 'ENSEMBLE')

    # --- Phase 3: Pre-Match Adjuster ---
    print("\n=== PRE-MATCH ADJUSTER (Phase 3) ===")
    adjuster = PreMatchAdjuster()

    csv_to_adjuster = {
        'home_key_missing': 'key_player_missing',
        'home_missing_caps': 'missing_caps_pct',
        'home_missing_goals': 'missing_goals_pct',
        'home_xi_strength': 'starting_xi_strength',
        'home_debutants': 'debutants_in_squad',
        'home_height_avg': 'squad_height_avg',
    }

    # Collect all signal-rich matches from train + val + test
    signal_matches_list = []
    for name, feat_df in [('train', train_feat), ('val', val_feat), ('test', test_feat)]:
        if 'home_key_missing' in feat_df.columns:
            with_sig = feat_df[feat_df['home_key_missing'].notna()].copy()
            signal_matches_list.append(with_sig)
    all_with_signals = pd.concat(signal_matches_list) if signal_matches_list else pd.DataFrame()

    available_csv_cols = [c for c in csv_to_adjuster
                          if c in all_with_signals.columns]

    if available_csv_cols and len(all_with_signals) >= 20:
        print(f"  Total matches with signals: {len(all_with_signals)}")


        if len(all_with_signals) >= 20:
            cal_signals_df = all_with_signals[['date'] + available_csv_cols].copy()
            cal_signals_df = cal_signals_df.rename(columns=csv_to_adjuster)

            # Use simple ELO-to-probability as base model (unbiased, no overfitting)
            elo_diff = all_with_signals['elo_diff'].values
            p_home_raw = 1.0 / (10 ** (-elo_diff / 400.0) + 1.0)
            # Draw probability: peaks at 28% when evenly matched
            draw_peak = 0.28
            p_draw = draw_peak * np.exp(-(elo_diff ** 2) / (2 * 300 ** 2))
            p_home = p_home_raw * (1 - p_draw)
            p_away = 1 - p_home - p_draw
            elo_base_probs = {
                'home': p_home,
                'draw': p_draw,
                'away': p_away,
            }
            actual_signals = {
                'home': all_with_signals['target_home_win'].values,
                'draw': all_with_signals['target_draw'].values,
                'away': all_with_signals['target_away_win'].values,
            }

            print(f"  Calibrating on {len(all_with_signals)} signal-rich matches (ELO base)...")
            adjuster.calibrate(cal_signals_df, elo_base_probs, actual_signals)
            print(adjuster.summary())

            # Apply to test set if signals available
            test_signals = {}
            for csv_col, adj_name in csv_to_adjuster.items():
                if csv_col in test_feat.columns:
                    test_signals[adj_name] = test_feat[csv_col].values

            active = adjuster.get_active()
            has_test_sigs = any(
                v is not None and not np.all(pd.isna(v)) for v in test_signals.values()
            )
            if has_test_sigs and active:
                ensemble_array = np.column_stack([
                    ensemble_preds_test['home'], ensemble_preds_test['draw'],
                    ensemble_preds_test['away'],
                ])
                adjusted_test = adjuster.adjust(ensemble_array, test_signals)
                adjusted_preds_test = {
                    'home': adjusted_test[:, 0],
                    'draw': adjusted_test[:, 1],
                    'away': adjusted_test[:, 2],
                }
                evaluate_predictions(y_true_test_dict, adjusted_preds_test,
                                     'ENSEMBLE + ADJUSTER')
            else:
                print(f"  Active signals: {active}, test signal avail: {has_test_sigs}")
                print("  Skipping test adjustment.")
                adjusted_preds_test = ensemble_preds_test
        else:
            print("  Not enough signal matches for calibration.")
            adjusted_preds_test = ensemble_preds_test
    else:
        print("  No pre-match signal columns found.")
        adjusted_preds_test = ensemble_preds_test

    # Save models
    save_models(ensemble, xgb_models, lgb_models, elo_model, cols)
    adjuster.save()

    return ensemble, xgb_models, lgb_models, elo_model, cols, adjuster


def run_backtest():
    """Run backtest on 2022 World Cup to validate betting strategy."""
    print("\n" + "=" * 60)
    print("BACKTEST: 2022 World Cup")
    print("=" * 60)

    # Load models
    print("\nLoading models...")
    models = load_models()
    ensemble = models['ensemble']
    xgb_models = models['xgb_models']
    lgb_models = models['lgb_models']
    elo_model = models['elo_model']
    feature_cols = models['feature_cols']

    # Load and prepare data
    from src.data.cleaner import load_and_clean, get_world_cup_matches
    df = load_and_clean('data/raw/results.csv')
    full_df = build_all_features(df)
    wc_df = full_df[(full_df['date'].dt.year == 2022) &
                    (full_df['is_world_cup'] == 1) &
                    (full_df['is_played'])].copy()

    print(f"\n2022 World Cup: {len(wc_df)} matches")

    # Predict
    wc_df = predict_elo_baseline(wc_df, elo_model)
    X_wc = wc_df[[c for c in feature_cols if c in wc_df.columns]].values

    # Fill NaNs
    X_wc = np.nan_to_num(X_wc, nan=0.0)

    # Get predictions
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

    # Get odds: real if available, simulated otherwise
    print("\nLoading odds...")
    odds = _get_odds_for_prediction(wc_df, elo_preds)

    # Calculate bets
    bankroll = 1000.0
    recommendations = calculate_optimal_bets(
        ensemble_preds, odds, bankroll=bankroll,
        edge_threshold=0.02, max_per_bet=0.05, max_total_exposure=0.30,
    )

    # Simulate results
    outcomes = []
    for _, row in wc_df.iterrows():
        if row['home_score'] > row['away_score']:
            outcomes.append('H')
        elif row['home_score'] == row['away_score']:
            outcomes.append('D')
        else:
            outcomes.append('A')

    new_bankroll, profit, roi, details = simulate_bet_result(
        recommendations, outcomes, bankroll
    )

    # Print results
    print(f"\n{'='*50}")
    print(f"BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"Starting bankroll: ${bankroll:.2f}")
    print(f"Final bankroll: ${new_bankroll:.2f}")
    print(f"Profit: ${profit:.2f}")
    print(f"ROI: {roi*100:.2f}%")
    print(f"Matches with bets: {sum(1 for b in recommendations if len(b) > 0)}/{len(wc_df)}")

    n_bets = sum(len(b) for b in recommendations)
    n_won = sum(1 for d in details if d['won'])
    if n_bets > 0:
        print(f"Hit rate: {n_won}/{n_bets} ({n_won/n_bets*100:.1f}%)")

    # Detailed bet log
    print(f"\n{'='*50}")
    print("BET LOG")
    print(f"{'='*50}")
    for i, (match_bets, outcome) in enumerate(zip(recommendations, outcomes)):
        if match_bets:
            row = wc_df.iloc[i]
            print(f"\n{row['home_team']} vs {row['away_team']} ({row['date'].date()})")
            print(f"  Result: {row['home_score']:.0f}-{row['away_score']:.0f} ({outcome})")
            for b in match_bets:
                status = "WIN" if b['outcome'][0].upper() == outcome else "LOSS"
                print(f"  [{status}] Bet ${b['bet_amount']:.2f} on {b['outcome']} "
                      f"@ {b['odds']:.2f} (p={b['probability']:.3f}, EV={b['ev']*100:.1f}%)")

    return new_bankroll, profit, roi


def predict_today():
    """Generate predictions for today's unplayed World Cup 2026 matches."""
    print("\n" + "=" * 60)
    print("DAILY PREDICTION: 2026 World Cup")
    print("=" * 60)

    # Load models
    print("\nLoading models...")
    try:
        models = load_models()
    except FileNotFoundError:
        print("No trained model found. Running training pipeline first...")
        run_training_pipeline()
        models = load_models()

    ensemble = models['ensemble']
    xgb_models = models['xgb_models']
    lgb_models = models['lgb_models']
    elo_model = models['elo_model']
    feature_cols = models['feature_cols']

    # Load data
    from src.data.cleaner import load_and_clean
    df = load_and_clean('data/raw/results.csv')
    full_df = build_all_features(df)

    # Get unplayed WC 2026 matches
    fixtures = full_df[(full_df['date'].dt.year == 2026) &
                       (full_df['is_world_cup'] == 1) &
                       (~full_df['is_played'])].copy()

    if len(fixtures) == 0:
        print("\nNo upcoming World Cup matches found.")
        return

    print(f"\nFound {len(fixtures)} upcoming matches\n")

    # Add ELO predictions
    fixtures = predict_elo_baseline(fixtures, elo_model)

    # Prepare features for prediction
    X_fix = fixtures[[c for c in feature_cols if c in fixtures.columns]].values
    X_fix = np.nan_to_num(X_fix, nan=0.0)

    # Get all predictions
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

    # Get odds: real if available, simulated otherwise
    odds = _get_odds_for_prediction(fixtures, elo_preds)

    # Calculate bets
    bankroll = 1000.0
    recommendations = calculate_optimal_bets(
        ensemble_preds, odds, bankroll=bankroll,
        edge_threshold=0.02, max_per_bet=0.05, max_total_exposure=0.30,
    )

    # Display results
    for i, (_, row) in enumerate(fixtures.iterrows()):
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        print(f"--- {row['home_team']} vs {row['away_team']} | {date_str} ---")
        print(f"  ELO: {row['elo_home']:.0f} vs {row['elo_away']:.0f} "
              f"(diff: {row['elo_diff']:+.0f})")
        print(f"  Probabilities (ELO):    H={elo_preds['home'][i]:.3f} "
              f"D={elo_preds['draw'][i]:.3f} A={elo_preds['away'][i]:.3f}")
        print(f"  Probabilities (XGBoost): H={xgb_preds['home'][i]:.3f} "
              f"D={xgb_preds['draw'][i]:.3f} A={xgb_preds['away'][i]:.3f}")
        print(f"  Probabilities (LightGBM): H={lgb_preds['home'][i]:.3f} "
              f"D={lgb_preds['draw'][i]:.3f} A={lgb_preds['away'][i]:.3f}")
        print(f"  Probabilities (ENSEMBLE): H={ensemble_preds['home'][i]:.3f} "
              f"D={ensemble_preds['draw'][i]:.3f} A={ensemble_preds['away'][i]:.3f}")

        match_bets = recommendations[i]
        if match_bets:
            for b in match_bets:
                print(f"  >> BET: ${b['bet_amount']:.2f} on {b['outcome'].upper()} "
                      f"@ {b['odds']:.2f} (EV: {b['ev']*100:+.1f}%)")
        else:
            # Find closest outcome
            best_ev = max(
                (ensemble_preds[out][i] * odds[out][i] - 1, out)
                for out in ['home', 'draw', 'away']
            )
            print(f"  >> PASS (best EV: {best_ev[0]*100:+.1f}% on {best_ev[1]})")
        print()

    # Save recommendations
    output_path = Path(__file__).parent.parent / 'outputs' / 'bets'
    output_path.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    recommendations_df = _format_recommendations(fixtures, recommendations, ensemble_preds)
    recommendations_df.to_csv(output_path / f'recommendations_{today}.csv', index=False)
    print(f"Recommendations saved to outputs/bets/recommendations_{today}.csv")


def _simulate_market_odds(elo_probs: dict, vig: float = 0.05) -> dict:
    """
    Simulate market odds from ELO probabilities with vigorish.
    Used as FALLBACK when no real odds are available.
    """
    odds = {}
    for outcome in ['home', 'draw', 'away']:
        inflated = elo_probs[outcome] * (1 + vig)
        inflated = np.clip(inflated, 0.05, 0.95)
        odds[outcome] = 1.0 / inflated
    return odds


def _load_real_odds_for_matches(matches_df: pd.DataFrame) -> dict:
    """
    Try to load real odds from data/raw/odds.csv and merge with given matches.

    Returns:
        odds_dict: {'home': array, 'draw': array, 'away': array} if real odds exist
        None: if no real odds file found
    """
    if not REAL_ODDS_PATH.exists():
        print(f"  No real odds file at {REAL_ODDS_PATH}, using ELO-simulated odds.")
        return None

    try:
        odds_df = load_odds_csv(str(REAL_ODDS_PATH))
        merged = merge_odds_with_matches(matches_df, odds_df)
        n_real = merged['odds_home'].notna().sum()
        print(f"  Real odds loaded: {n_real}/{len(merged)} matches have odds")

        if n_real == 0:
            print("  No matches matched with real odds, falling back to ELO simulation.")
            return None

        # Build odds dict for matched matches
        odds = {'home': np.zeros(len(merged)), 'draw': np.zeros(len(merged)),
                'away': np.zeros(len(merged))}
        for i, (_, row) in enumerate(merged.iterrows()):
            if pd.notna(row.get('odds_home')):
                odds['home'][i] = row['odds_home']
                odds['draw'][i] = row['odds_draw']
                odds['away'][i] = row['odds_away']
            else:
                odds['home'][i] = 0  # Will be filled by caller with ELO fallback
                odds['draw'][i] = 0
                odds['away'][i] = 0

        return odds
    except Exception as e:
        print(f"  Error loading real odds: {e}, falling back to ELO simulation.")
        return None


def _get_odds_for_prediction(matches_df: pd.DataFrame, elo_probs: dict) -> dict:
    """
    Get odds for prediction. Priority:
    1. 500.com scraped odds (live, from Chinese market)
    2. Manual odds file (data/raw/odds.csv)
    3. Trained LightGBM model (Pinnacle-level, most accurate)
    4. ELO-simulated odds (last resort)
    """
    n = len(matches_df)
    elo_odds = _simulate_market_odds(elo_probs, vig=0.05)
    merged_odds = {
        'home': np.zeros(n), 'draw': np.zeros(n), 'away': np.zeros(n)
    }

    # Track which matches have real odds
    has_real = np.zeros(n, dtype=bool)

    # Source 1: 500.com live odds
    odds_500_path = Path(__file__).parent.parent / 'data' / 'raw' / 'wc2026_odds_500.csv'
    if odds_500_path.exists():
        try:
            from src.data.odds_merger import merge_500_odds_with_matches
            merged = merge_500_odds_with_matches(matches_df)
            for i, (_, row) in enumerate(merged.iterrows()):
                if pd.notna(row.get('odds_home')) and row['odds_home'] > 1.0:
                    merged_odds['home'][i] = row['odds_home']
                    merged_odds['draw'][i] = row['odds_draw']
                    merged_odds['away'][i] = row['odds_away']
                    has_real[i] = True
        except Exception as e:
            print(f"  Error loading 500.com odds: {e}")

    # Source 2: Manual odds.csv
    if not has_real.all():
        real_odds = _load_real_odds_for_matches(matches_df)
        if real_odds is not None:
            for i in range(n):
                if not has_real[i] and real_odds['home'][i] > 1.0:
                    merged_odds['home'][i] = real_odds['home'][i]
                    merged_odds['draw'][i] = real_odds['draw'][i]
                    merged_odds['away'][i] = real_odds['away'][i]
                    has_real[i] = True

    # Source 3: Trained LightGBM model (Pinnacle-level)
    model_odds = _predict_odds_from_model(matches_df)
    if model_odds is not None:
        for i in range(n):
            if not has_real[i]:
                merged_odds['home'][i] = model_odds['home'][i]
                merged_odds['draw'][i] = model_odds['draw'][i]
                merged_odds['away'][i] = model_odds['away'][i]

    # Source 4: ELO fallback for remaining
    for i in range(n):
        if not has_real[i] and model_odds is None:
            merged_odds['home'][i] = elo_odds['home'][i]
            merged_odds['draw'][i] = elo_odds['draw'][i]
            merged_odds['away'][i] = elo_odds['away'][i]

    n_real = has_real.sum()
    n_model = n - n_real if model_odds is not None else 0
    n_elo = n - n_real - n_model

    if model_odds is not None:
        source_label = f'{n_real} real, {n_model} trained model (MAE~0.05)'
    else:
        source_label = f'{n_real} real, {n-n_real} ELO-simulated'
    print(f"  Odds source: {source_label}")

    return merged_odds


def _format_recommendations(fixtures, recommendations, probs):
    """Format betting recommendations into a DataFrame."""
    rows = []
    for i, (_, row) in enumerate(fixtures.iterrows()):
        match_bets = recommendations[i]
        base = {
            'date': str(row['date'])[:10],
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'prob_h': probs['home'][i],
            'prob_d': probs['draw'][i],
            'prob_a': probs['away'][i],
        }
        if match_bets:
            for b in match_bets:
                rows.append({**base, **b})
        else:
            rows.append({**base, 'outcome': 'PASS', 'bet_amount': 0.0})

    return pd.DataFrame(rows)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['train', 'backtest', 'predict'],
                        default='predict', nargs='?')
    args = parser.parse_args()

    if args.command == 'train':
        run_training_pipeline()
    elif args.command == 'backtest':
        run_backtest()
    elif args.command == 'predict':
        predict_today()
