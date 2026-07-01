"""
Phase 7: Score prediction training pipeline.

Trains Dixon-Coles, ML-Poisson, xG-Poisson, and ScoreEnsemble models,
then evaluates on the 2022 World Cup test set.
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
from src.models.train import prepare_features, FEATURE_COLS
from src.models.score_models import (
    ScoreMatrix, DixonColesModel, MLPoissonModel, XGPoissonModel,
    ScoreEnsemble, elo_poisson_score_matrix, save_score_models,
)
from src.models.score_evaluation import (
    evaluate_score_predictions, compare_score_models,
    compute_rps_vectorized, compute_logloss_exact_scores,
)

DATA_DIR = Path(__file__).parent.parent / 'data'


def build_matrix_batch(sm_list: list) -> np.ndarray:
    """Convert list of ScoreMatrix to numpy array (n, 10, 10)."""
    return np.array([sm.matrix for sm in sm_list])


def main():
    print("=" * 60)
    print("PHASE 7: SCORE PREDICTION TRAINING PIPELINE")
    print("=" * 60)

    # ── 1. Load and build features ──────────────────────────────────────
    print("\n[1/5] Loading data and building features...")
    df = load_and_clean(str(DATA_DIR / 'raw' / 'results.csv'))
    full_df = build_all_features(df)

    # Merge event features (for xG model)
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

    # Only played matches with valid scores
    played = full_df[full_df['home_score'].notna() &
                     full_df['away_score'].notna()].copy()
    played = played[played['home_score'].apply(lambda x: isinstance(x, (int, float, np.integer, np.floating)))]
    # Ensure int
    played['home_score'] = played['home_score'].astype(int)
    played['away_score'] = played['away_score'].astype(int)
    print(f"  Played matches: {len(played)}")

    # ── 2. Train Dixon-Coles ────────────────────────────────────────────
    print("\n[2/5] Training Dixon-Coles model...")
    dc_model = DixonColesModel()

    # Use matches from 2000 onwards for Dixon-Coles
    dc_train = played[played['date'].apply(lambda d: d.year >= 2000)].copy()
    dc_model.fit(dc_train, time_half_life=5.0, l2_reg=0.01)

    # ── 3. Train ML-Poisson ─────────────────────────────────────────────
    print("\n[3/5] Training ML-Poisson model...")
    played_sorted = played.sort_values('date')

    # Time-based split: train on pre-2022.06, val on 2022.06-2022.10, test on WC 2022
    train_mask = played_sorted['date'] < '2022-06-01'
    val_mask = (played_sorted['date'] >= '2022-06-01') & (played_sorted['date'] < '2022-10-01')
    test_mask = played_sorted['date'].apply(
        lambda d: d.year == 2022 and d.month >= 11
    )

    train_feat = played_sorted[train_mask].copy()
    val_feat = played_sorted[val_mask].copy()
    test_feat = played_sorted[test_mask].copy()

    print(f"  Train: {len(train_feat)}, Val: {len(val_feat)}, Test: {len(test_feat)}")

    # Prepare features
    X_tr, _, _, _, cols_tr, _ = prepare_features(train_feat)
    X_val, _, _, _, cols_val, _ = prepare_features(val_feat)
    X_te, _, _, _, cols_te, _ = prepare_features(test_feat)

    # Ensure same columns
    use_cols = [c for c in FEATURE_COLS if c in train_feat.columns]
    for c in use_cols:
        if c not in train_feat.columns:
            train_feat[c] = 0.0
        if c not in val_feat.columns:
            val_feat[c] = 0.0
        if c not in test_feat.columns:
            test_feat[c] = 0.0

    X_train = np.nan_to_num(train_feat[use_cols].values, nan=0.0, posinf=0.0, neginf=0.0)
    X_val_arr = np.nan_to_num(val_feat[use_cols].values, nan=0.0, posinf=0.0, neginf=0.0)
    X_test = np.nan_to_num(test_feat[use_cols].values, nan=0.0, posinf=0.0, neginf=0.0)

    # Also fill NaN in feature DataFrames (used later)
    for col in use_cols:
        if col in train_feat.columns and train_feat[col].isna().any():
            train_feat[col] = train_feat[col].fillna(0.0)
        if col in val_feat.columns and val_feat[col].isna().any():
            val_feat[col] = val_feat[col].fillna(0.0)
        if col in test_feat.columns and test_feat[col].isna().any():
            test_feat[col] = test_feat[col].fillna(0.0)

    y_train_h_goals = train_feat['home_score'].astype(int).values
    y_train_a_goals = train_feat['away_score'].astype(int).values
    y_val_h_goals = val_feat['home_score'].astype(int).values
    y_val_a_goals = val_feat['away_score'].astype(int).values
    y_test_h_goals = test_feat['home_score'].astype(int).values
    y_test_a_goals = test_feat['away_score'].astype(int).values

    # Time decay weights for ML-Poisson training
    latest_date = pd.to_datetime(train_feat['date'].max())
    days_ago = train_feat['date'].apply(
        lambda d: (latest_date - pd.to_datetime(d)).days
    )
    half_life_days = 3 * 365.25
    train_weights = np.exp(-np.log(2) * days_ago.values / half_life_days)
    train_weights = np.clip(train_weights / train_weights.max(), 0.10, 1.0)

    ml_poisson = MLPoissonModel()
    ml_poisson.fit(
        X_train, y_train_h_goals, y_train_a_goals,
        X_val=X_val_arr, y_val_h=y_val_h_goals, y_val_a=y_val_a_goals,
        sample_weights=train_weights,
        feature_cols=use_cols,
    )

    # ── 4. Train xG-Poisson ─────────────────────────────────────────────
    print("\n[4/5] Training xG-Poisson model...")
    xg_poisson = XGPoissonModel()

    # Load event features CSV for team xG profiles
    evt_features_path = DATA_DIR / 'processed' / 'event_features.csv'
    if evt_features_path.exists():
        evt_features = pd.read_csv(evt_features_path)
        xg_poisson.fit(event_features_df=evt_features)
    else:
        print("  event_features.csv not found, xG-Poisson will use league avg")

    # Build team event history from results_with_event_features
    if evt_path.exists() and 'event_home_xg' in full_df.columns:
        home_xg = full_df[['date', 'home_team', 'event_home_xg']].dropna(subset=['event_home_xg'])
        home_xg = home_xg.rename(columns={'home_team': 'team', 'event_home_xg': 'xg'})
        away_xg = full_df[['date', 'away_team', 'event_away_xg']].dropna(subset=['event_away_xg'])
        away_xg = away_xg.rename(columns={'away_team': 'team', 'event_away_xg': 'xg'})
        team_hist = pd.concat([home_xg, away_xg], ignore_index=True)
        if len(team_hist) > 0:
            xg_poisson.fit(team_event_history=team_hist)

    # ── 5. Evaluate and Ensemble ────────────────────────────────────────
    print("\n[5/5] Evaluating models and building ensemble...")
    test_actual = np.column_stack([y_test_h_goals, y_test_a_goals])

    # ELO baseline predictions
    elo_matrices_test = []
    for _, row in test_feat.iterrows():
        elo_diff = row['elo_diff'] if pd.notna(row.get('elo_diff')) else 0
        neutral = row.get('neutral', False)
        elo_matrices_test.append(elo_poisson_score_matrix(elo_diff, neutral))
    elo_mats = build_matrix_batch(elo_matrices_test)

    # Dixon-Coles predictions
    dc_matrices_test = []
    for _, row in test_feat.iterrows():
        dc_matrices_test.append(
            dc_model.predict_score_matrix(row['home_team'], row['away_team'],
                                          neutral=row.get('neutral', False))
        )
    dc_mats = build_matrix_batch(dc_matrices_test)

    # ML-Poisson predictions
    ml_matrices_test = ml_poisson.predict_score_matrices(X_test)
    ml_mats = build_matrix_batch(ml_matrices_test)

    # xG-Poisson predictions (with fallback to ELO)
    xg_matrices_test = []
    for _, row in test_feat.iterrows():
        if xg_poisson.is_available(row['home_team'], row['away_team']):
            xg_matrices_test.append(
                xg_poisson.predict_score_matrix(row['home_team'], row['away_team']))
        else:
            elo_diff = row['elo_diff'] if pd.notna(row.get('elo_diff')) else 0
            xg_matrices_test.append(
                elo_poisson_score_matrix(elo_diff, row.get('neutral', False)))
    xg_mats = build_matrix_batch(xg_matrices_test)

    # Evaluate each model
    print("\n─── Individual Model Evaluation (WC 2022 Test) ───")
    evaluate_score_predictions(elo_mats, test_actual, 'ELO Poisson (baseline)')
    evaluate_score_predictions(dc_mats, test_actual, 'Dixon-Coles')
    evaluate_score_predictions(ml_mats, test_actual, 'ML-Poisson')
    evaluate_score_predictions(xg_mats, test_actual, 'xG-Poisson')

    # Compare
    all_preds = {
        'ELO Poisson': elo_mats,
        'Dixon-Coles': dc_mats,
        'ML-Poisson': ml_mats,
        'xG-Poisson': xg_mats,
    }
    comp_df = compare_score_models(all_preds, test_actual)
    print("\n─── Model Comparison ───")
    print(comp_df[['model', 'rps_total_goals', 'logloss_exact_score',
                    'mae_home', 'mae_away', 'mae_total']].to_string(index=False))

    # Ensemble
    print("\n─── Score Ensemble ───")
    score_ensemble = ScoreEnsemble()
    # Build validation matrices for weight optimization
    val_actual = np.column_stack([y_val_h_goals, y_val_a_goals])

    dc_val = []
    for _, row in val_feat.iterrows():
        dc_val.append(
            dc_model.predict_score_matrix(row['home_team'], row['away_team'],
                                          neutral=row.get('neutral', False)))
    ml_val = ml_poisson.predict_score_matrices(X_val_arr)

    xg_val = []
    for _, row in val_feat.iterrows():
        if xg_poisson.is_available(row['home_team'], row['away_team']):
            xg_val.append(xg_poisson.predict_score_matrix(row['home_team'], row['away_team']))
        else:
            elo_diff = row['elo_diff'] if pd.notna(row.get('elo_diff')) else 0
            xg_val.append(elo_poisson_score_matrix(elo_diff, row.get('neutral', False)))

    val_model_mats = {
        'DixonColes': dc_val,
        'MLPoisson': ml_val,
        'XGPoisson': xg_val,
    }
    score_ensemble.fit(val_model_mats, val_actual)

    # Ensemble test predictions
    test_model_mats = {
        'DixonColes': dc_matrices_test,
        'MLPoisson': ml_matrices_test,
        'XGPoisson': xg_matrices_test,
    }
    ensemble_matrices_test = score_ensemble.predict_batch(test_model_mats)
    ens_mats = build_matrix_batch(ensemble_matrices_test)
    evaluate_score_predictions(ens_mats, test_actual, 'SCORE ENSEMBLE')

    # ── Save models ─────────────────────────────────────────────────────
    save_score_models(dc_model, ml_poisson, xg_poisson, score_ensemble)

    # ── Show sample predictions ─────────────────────────────────────────
    print("\n─── Sample WC 2022 Predictions ───")
    for i in range(min(5, len(test_feat))):
        row = test_feat.iloc[i]
        ens_sm = ensemble_matrices_test[i]
        e_h, e_a = ens_sm.expected_goals()
        over_p, _ = ens_sm.over_under_probability(2.5)
        btts_p, _ = ens_sm.btts_probability()
        top3 = ens_sm.top_scorelines(3)
        print(f"\n  {row['home_team']} vs {row['away_team']} "
              f"(actual: {int(row['home_score'])}-{int(row['away_score'])})")
        print(f"  E[goals]: {e_h:.2f} - {e_a:.2f}")
        print(f"  P(Over 2.5): {over_p:.1%}  P(BTTS): {btts_p:.1%}")
        print(f"  Top scores: {', '.join(f'{h}-{a} ({p:.1%})' for h, a, p in top3)}")

    print("\nDone! Score models saved to outputs/models/score_models.pkl")


if __name__ == '__main__':
    main()
