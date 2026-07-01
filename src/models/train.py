"""
Model training pipeline: ELO baseline, XGBoost, LightGBM, ensemble.
"""
import pandas as pd
import numpy as np
import math
from sklearn.model_selection import TimeSeriesSplit
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss
import xgboost as xgb
import lightgbm as lgb
import pickle
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent / 'outputs' / 'models'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def train_elo_baseline(train_df: pd.DataFrame) -> dict:
    """
    ELO-only baseline: Poisson regression for expected goals.
    Trained on historical goal distributions by ELO difference buckets.
    """
    print("\n=== ELO Baseline Model ===")

    # Bin ELO differences to estimate goal rates
    train_df = train_df.dropna(subset=['elo_diff']).copy()
    train_df['elo_bucket'] = pd.cut(train_df['elo_diff'],
                                     bins=[-600, -400, -250, -100, 0, 100, 250, 400, 600],
                                     labels=[-500, -325, -175, -50, 50, 175, 325, 500])

    # Average goals per ELO bucket
    bucket_stats = train_df.groupby('elo_bucket').agg(
        home_goals_avg=('home_score', 'mean'),
        away_goals_avg=('away_score', 'mean'),
        home_win_rate=('target_home_win', 'mean'),
        draw_rate=('target_draw', 'mean'),
        away_win_rate=('target_away_win', 'mean'),
        count=('home_score', 'count'),
    ).reset_index()

    print(bucket_stats.to_string())

    return {'bucket_stats': bucket_stats}


def predict_elo_baseline(matches: pd.DataFrame, elo_model: dict) -> pd.DataFrame:
    """
    Predict probabilities using the ELO baseline model.
    Uses unified log-linear Poisson formulation from elo.py.
    """
    from src.features.elo import elo_to_expected_goals

    matches = matches.copy()

    probs_h, probs_d, probs_a = [], [], []

    for _, row in matches.iterrows():
        elo_diff = row['elo_diff']
        if pd.isna(elo_diff):
            probs_h.append(0.38)
            probs_d.append(0.28)
            probs_a.append(0.34)
            continue

        neutral = row.get('neutral', False)
        lambda_home, lambda_away = elo_to_expected_goals(elo_diff, neutral=neutral)

        # Score probabilities via independent Poisson
        p_h = 0
        p_d = 0
        p_a = 0

        for gh in range(8):
            for ga in range(8):
                prob = (np.exp(-lambda_home) * lambda_home ** gh / max(1, math.factorial(gh)) *
                        np.exp(-lambda_away) * lambda_away ** ga / max(1, math.factorial(ga)))
                if gh > ga:
                    p_h += prob
                elif gh == ga:
                    p_d += prob
                else:
                    p_a += prob

        total = p_h + p_d + p_a
        if total > 0:
            p_h /= total
            p_d /= total
            p_a /= total

        probs_h.append(p_h)
        probs_d.append(p_d)
        probs_a.append(p_a)

    matches['prob_h_elo'] = probs_h
    matches['prob_d_elo'] = probs_d
    matches['prob_a_elo'] = probs_a
    return matches


FEATURE_COLS = [
    # === Category 1: Squad Quality ===
    'elo_home', 'elo_away', 'elo_diff',            # #1 ELO (existing)
    'fifa_points_home', 'fifa_points_away',        # #3 FIFA points (new)
    # #6-8, starting_xi_value: from transfermarkt (pending data merge)

    # === Category 3: Recent Form ===
    'home_form_gf_avg', 'home_form_ga_avg', 'home_form_gd_avg',
    'home_form_win_rate', 'home_form_points_per_game', 'home_form_n_matches',
    'away_form_gf_avg', 'away_form_ga_avg', 'away_form_gd_avg',
    'away_form_win_rate', 'away_form_points_per_game', 'away_form_n_matches',
    # #19-20: form 5-match (using existing 10-match as proxy)
    # #21: opponent ELO avg (new)
    'home_opp_elo_avg_5', 'away_opp_elo_avg_5',
    # #24: tournament-weighted form (new)
    'home_weighted_form', 'away_weighted_form',
    # #26: tournament-specific form (new)
    'home_tournament_form', 'away_tournament_form',

    # === Category 4: Head-to-Head ===
    'h2h_matches', 'h2h_home_win_pct', 'h2h_draw_pct', 'h2h_away_win_pct',  # existing
    # #28-#31: enhanced H2H (new)
    'h2h_recent_win_home', 'h2h_recent_win_away',
    'h2h_comp_win_home', 'h2h_comp_win_away',
    'h2h_goal_diff', 'h2h_same_venue_pct',

    # === Category 5: Tournament Context ===
    'is_friendly', 'is_world_cup', 'is_wc_qualifier', 'is_continental',  # #32 existing
    'is_knockout',                                                       # #33 new
    'days_since_last_home', 'days_since_last_away', 'rest_advantage',    # #37 new
    'match_seq_home', 'match_seq_away',                                  # #38 new
    'neutral_venue',                                                      # #40 new

    # === Category 3 (continued): Weighted Form (existing) ===
    'home_wgf', 'home_wga', 'home_wgd',
    'away_wgf', 'away_wga', 'away_wgd',

    # === Coach (new) ===
    'coach_win_rate_home', 'coach_win_rate_away',

    # === StatsBomb Event Features (new — Phase 4) ===
    'event_home_xg', 'event_away_xg',
    'event_home_xg_per_shot', 'event_away_xg_per_shot',
    'event_home_pass_completion', 'event_away_pass_completion',
    'event_home_ppda', 'event_away_ppda',
    'event_home_direct_speed', 'event_away_direct_speed',
    'event_home_def_line_height', 'event_away_def_line_height',
    'event_home_pressures', 'event_away_pressures',
    'event_home_progressive_passes', 'event_away_progressive_passes',
    'event_home_style_possession', 'event_away_style_possession',
    'event_home_style_highpress', 'event_away_style_highpress',
    'event_home_style_counter', 'event_away_style_counter',
    'event_home_style_lowblock', 'event_away_style_lowblock',

    # === Tactical Matchup (new) ===
    'tactical_matchup_adv', 'tactical_matchup_n',

    # === Style Matchup (continuous differences) ===
    'matchup_ppda_diff', 'matchup_pass_diff', 'matchup_speed_diff',
    'matchup_defline_diff', 'matchup_pressure_diff', 'matchup_progpass_diff',
]


def prepare_features(df: pd.DataFrame) -> tuple:
    """Prepare feature matrix X and target y from feature DataFrame."""
    df = df.dropna(subset=['elo_diff']).copy()

    # Fill missing form features with median
    for col in FEATURE_COLS:
        if col in df.columns and df[col].isna().any():
            df[col] = df[col].fillna(df[col].median())

    # Ensure all feature columns exist
    available_cols = [c for c in FEATURE_COLS if c in df.columns]
    X = df[available_cols].values
    y_h = df['target_home_win'].values
    y_d = df['target_draw'].values
    y_a = df['target_away_win'].values

    return X, y_h, y_d, y_a, available_cols, df


def train_xgboost(X_train, y_train_h, y_train_d, y_train_a,
                  X_val, y_val_h, y_val_d, y_val_a,
                  X_test, y_test_h, y_test_d, y_test_a,
                  sample_weights=None) -> dict:
    """Train XGBoost models for each outcome.

    X_train/y_train: training data (2000-2022.06)
    X_val/y_val:     validation data (2022.06-2022.10) for early stopping
    X_test/y_test:   test data (2022 World Cup) - COMPLETELY UNTOUCHED
    sample_weights:  time decay weights for each training sample
    """
    print("\n=== XGBoost Models ===")

    params = {
        'max_depth': 5,
        'learning_rate': 0.03,
        'n_estimators': 500,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'min_child_weight': 5,
        'reg_alpha': 0.5,
        'reg_lambda': 1.0,
        'objective': 'binary:logistic',
        'eval_metric': 'logloss',
        'verbosity': 0,
    }

    models = {}
    preds_val = {}
    preds_test = {}

    for name, y_tr, y_val, y_te in [('home', y_train_h, y_val_h, y_test_h),
                                      ('draw', y_train_d, y_val_d, y_test_d),
                                      ('away', y_train_a, y_val_a, y_test_a)]:
        model = xgb.XGBClassifier(**params, early_stopping_rounds=50)
        model.fit(
            X_train, y_tr,
            sample_weight=sample_weights,  # ← time decay weighting
            eval_set=[(X_val, y_val)],      # ← early stopping on VALIDATION
            verbose=False,
        )
        models[name] = model
        preds_val[name] = model.predict_proba(X_val)[:, 1]
        preds_test[name] = model.predict_proba(X_test)[:, 1]

        # Feature importance
        importance = model.feature_importances_
        top_idx = np.argsort(importance)[-5:][::-1]
        print(f"\n{name} top features:")
        for idx in top_idx:
            print(f"  {FEATURE_COLS[idx]}: {importance[idx]:.4f}")

        # Evaluate on VALIDATION
        ll_val = log_loss(y_val, np.column_stack([1 - preds_val[name], preds_val[name]]))
        print(f"  Val LogLoss={ll_val:.4f}")

    return models, preds_test


def train_lightgbm(X_train, y_train_h, y_train_d, y_train_a,
                   X_val, y_val_h, y_val_d, y_val_a,
                   X_test, y_test_h, y_test_d, y_test_a,
                   sample_weights=None) -> dict:
    """Train LightGBM models for each outcome.

    X_train/y_train: training data
    X_val/y_val:     validation data for early stopping
    X_test/y_test:   test data - COMPLETELY UNTOUCHED
    sample_weights:  time decay weights for each training sample
    """
    print("\n=== LightGBM Models ===")

    params = {
        'max_depth': 4,
        'learning_rate': 0.03,
        'n_estimators': 500,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'min_child_samples': 20,
        'reg_alpha': 0.3,
        'reg_lambda': 0.5,
        'verbosity': -1,
    }

    models = {}
    preds_test = {}

    for name, y_tr, y_val, y_te in [('home', y_train_h, y_val_h, y_test_h),
                                      ('draw', y_train_d, y_val_d, y_test_d),
                                      ('away', y_train_a, y_val_a, y_test_a)]:
        model = lgb.LGBMClassifier(**params)
        model.fit(
            X_train, y_tr,
            sample_weight=sample_weights,  # ← time decay weighting
            eval_set=[(X_val, y_val)],      # ← early stopping on VALIDATION
            eval_metric='logloss',
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
        )
        models[name] = model
        preds_test[name] = model.predict_proba(X_test)[:, 1]

        # Evaluate on VALIDATION
        y_val_pred = model.predict_proba(X_val)[:, 1]
        ll_val = log_loss(y_val, np.column_stack([1 - y_val_pred, y_val_pred]))
        print(f"  {name}: Val LogLoss={ll_val:.4f}")

    return models, preds_test


class ProbabilityCalibrator:
    """Calibrate predicted probabilities using isotonic regression."""

    def __init__(self):
        self.calibrators = {}

    def fit(self, y_true, y_prob, outcome: str):
        """Fit isotonic regression calibrator."""
        cal = IsotonicRegression(out_of_bounds='clip', y_min=1e-6, y_max=1 - 1e-6)
        cal.fit(y_prob, y_true)
        self.calibrators[outcome] = cal
        return self

    def calibrate(self, probs_df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        """Calibrate probabilities and rename columns."""
        result = probs_df.copy()
        for outcome in ['home', 'draw', 'away']:
            cal_key = f'{prefix}_{outcome}'
            prob_col = f'prob_{outcome}_{prefix}'
            if cal_key in self.calibrators:
                result[prob_col] = self.calibrators[cal_key].transform(
                    result[prob_col.replace('prob_', '')]
                )
        return result

    def fit_all(self, y_dict: dict, prob_dict: dict):
        """Fit calibrators for all model probabilities."""
        for outcome in ['home', 'draw', 'away']:
            for model_name, probs in prob_dict.items():
                self.fit(y_dict[outcome], probs[outcome], f'{model_name}_{outcome}')
        return self


class Ensemble:
    """Meta-ensemble combining base model predictions."""

    def __init__(self):
        self.weights = {'home': {}, 'draw': {}, 'away': {}}
        self.calibrator = ProbabilityCalibrator()

    def fit(self, model_preds: dict, y_true: dict):
        """
        Find optimal weights for combining base models.
        Uses constrained least squares to ensure weights sum to 1.
        """
        from scipy.optimize import minimize

        for outcome in ['home', 'draw', 'away']:
            # Collect base model predictions for this outcome
            pred_arrays = []
            model_names = []
            for model_name, preds_dict in model_preds.items():
                if outcome in preds_dict:
                    pred_arrays.append(preds_dict[outcome])
                    model_names.append(model_name)

            if len(pred_arrays) <= 1:
                self.weights[outcome] = {model_names[0]: 1.0} if model_names else {}
                continue

            X_stack = np.column_stack(pred_arrays)
            y = y_true[outcome]

            # Minimize mean squared error with weight constraints
            def loss(w):
                pred = X_stack @ w
                return np.mean((pred - y) ** 2)

            constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0})
            bounds = [(0.0, 1.0) for _ in range(len(pred_arrays))]
            w0 = np.ones(len(pred_arrays)) / len(pred_arrays)

            result = minimize(loss, w0, bounds=bounds, constraints=constraints,
                              method='SLSQP')
            self.weights[outcome] = dict(zip(model_names, result.x))
            print(f"  {outcome} weights: {self.weights[outcome]}")

    def predict(self, model_preds: dict) -> dict:
        """Combine model predictions using learned weights."""
        combined = {'home': np.zeros(len(list(model_preds.values())[0]['home'])),
                     'draw': np.zeros(len(list(model_preds.values())[0]['draw'])),
                     'away': np.zeros(len(list(model_preds.values())[0]['away']))}

        for outcome in ['home', 'draw', 'away']:
            for model_name, weight in self.weights[outcome].items():
                if model_name in model_preds:
                    combined[outcome] += weight * model_preds[model_name][outcome]

        # Normalize to sum to 1
        total = combined['home'] + combined['draw'] + combined['away']
        for outcome in ['home', 'draw', 'away']:
            combined[outcome] /= total

        return combined


def evaluate_predictions(y_true: dict, y_pred: dict, name: str = 'Model'):
    """Evaluate prediction quality. Primary metric: LogLoss."""
    print(f"\n=== {name} Evaluation ===")

    for outcome in ['home', 'draw', 'away']:
        yt = y_true[outcome]
        yp = y_pred[outcome]
        ll = log_loss(yt, np.column_stack([1 - yp, yp]))
        print(f"  {outcome}: LogLoss={ll:.4f}")

    # Overall LogLoss (multi-class)
    yt_multi = np.column_stack([y_true['home'], y_true['draw'], y_true['away']])
    yp_multi = np.column_stack([y_pred['home'], y_pred['draw'], y_pred['away']])
    overall_ll = log_loss(yt_multi.argmax(axis=1), yp_multi)
    print(f"  Overall LogLoss: {overall_ll:.4f}")


def save_models(ensemble: Ensemble, xgb_models: dict, lgb_models: dict,
                elo_model: dict, feature_cols: list):
    """Save trained models to disk."""
    models = {
        'ensemble': ensemble,
        'xgb_models': xgb_models,
        'lgb_models': lgb_models,
        'elo_model': elo_model,
        'feature_cols': feature_cols,
    }
    path = OUTPUT_DIR / 'full_model.pkl'
    with open(path, 'wb') as f:
        pickle.dump(models, f)
    print(f"\nModels saved to {path}")


def load_models() -> dict:
    """Load trained models from disk."""
    path = OUTPUT_DIR / 'full_model.pkl'
    with open(path, 'rb') as f:
        return pickle.load(f)
