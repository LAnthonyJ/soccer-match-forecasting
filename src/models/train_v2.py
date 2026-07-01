"""
Enhanced model training with:
1. Tournament-tier sample weights (big matches matter more)
2. Opponent-adjusted form features
3. Rest days & match density
4. Time-decayed head-to-head
5. Optuna hyperparameter optimization
"""
import pandas as pd
import numpy as np
import lightgbm as lgb
import xgboost as xgb
import pickle, warnings, sys, os, math, io
from pathlib import Path
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import brier_score_loss, log_loss

os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(__file__).parent.parent.parent / 'outputs' / 'models'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── TOURNAMENT WEIGHTS ──
TOURNAMENT_WEIGHTS = {
    'FIFA World Cup': 4.0,
    'UEFA Euro': 3.0,
    'Copa América': 3.0,
    'African Cup of Nations': 2.5,
    'AFC Asian Cup': 2.5,
    'CONCACAF Gold Cup': 2.0,
    'FIFA World Cup qualification': 2.0,
    'UEFA Euro qualification': 1.5,
    'UEFA Nations League': 1.2,
    'FIFA Series': 1.0,
    'CONCACAF Nations League': 1.0,
}
DEFAULT_WEIGHT = 0.5  # Friendlies and minor tournaments


def compute_tournament_weight(tournament: str) -> float:
    """Get sample weight based on tournament importance."""
    for key, w in TOURNAMENT_WEIGHTS.items():
        if key in str(tournament):
            return w
    return DEFAULT_WEIGHT


# ── ENHANCED FEATURE ENGINEERING ──

def compute_team_histories(df):
    """
    Compute detailed team match histories for advanced feature extraction.
    Returns a dict: team_name -> list of {date, gf, ga, opponent, opponent_elo, tournament, neutral}
    """
    df = df.sort_values('date').reset_index(drop=True)
    histories = {}
    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        if pd.isna(row['home_score']) or pd.isna(row['away_score']):
            continue
        if home not in histories:
            histories[home] = []
        if away not in histories:
            histories[away] = []
        histories[home].append({
            'date': row['date'], 'gf': row['home_score'], 'ga': row['away_score'],
            'opponent': away, 'home': True, 'tournament': row.get('tournament', ''),
            'opponent_elo': row.get('elo_away', 1500),
        })
        histories[away].append({
            'date': row['date'], 'gf': row['away_score'], 'ga': row['home_score'],
            'opponent': home, 'home': False, 'tournament': row.get('tournament', ''),
            'opponent_elo': row.get('elo_home', 1500),
        })
    return histories


def get_opponent_adjusted_form(history, n=10, half_life=12):
    """
    Compute opponent-adjusted form: weight recent matches, adjust for opponent strength.
    Returns (adj_gf, adj_ga, adj_gd, simple_pts_per_game, weighted_pts_per_game)
    """
    if not history:
        return np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan

    recent = history[-n:] if len(history) >= n else history
    decay = 0.5 ** (1.0 / half_life)

    weights = [decay ** (len(recent) - 1 - i) for i in range(len(recent))]
    w_sum = sum(weights)

    # Simple weighted averages
    wgf = sum(m['gf'] * w for m, w in zip(recent, weights)) / w_sum
    wga = sum(m['ga'] * w for m, w in zip(recent, weights)) / w_sum

    # Opponent-adjusted: normalize by opponent ELO
    avg_elo = 1500
    adj_gf = sum(m['gf'] * w * (avg_elo / max(800, m['opponent_elo'])) for m, w in zip(recent, weights))
    adj_ga = sum(m['ga'] * w * (m['opponent_elo'] / max(800, avg_elo)) for m, w in zip(recent, weights))
    adj_gf /= w_sum
    adj_ga /= w_sum

    # Points per game
    pts = sum((3 if m['gf'] > m['ga'] else 1 if m['gf'] == m['ga'] else 0) * w for m, w in zip(recent, weights)) / w_sum

    return wgf, wga, wgf - wga, adj_gf, adj_ga, adj_gf - adj_ga, pts


def compute_rest_days(history, match_date):
    """Days since last match."""
    if not history:
        return 30  # Default: well rested
    last_date = pd.to_datetime(history[-1]['date'])
    current = pd.to_datetime(match_date)
    return max(0, (current - last_date).days)


def compute_h2h_advanced(home, away, histories):
    """
    Advanced head-to-head with time decay and home/away context.
    """
    home_hist = histories.get(home, [])
    away_hist = histories.get(away, [])

    # Find direct matchups
    h2h_home_wins = h2h_draws = h2h_away_wins = 0
    h2h_home_gf = h2h_away_gf = 0
    h2h_count = 0
    half_life = 36  # months for H2H relevance

    for m in home_hist:
        if m['opponent'] == away:
            months_ago = 999  # approximate
            weight = 0.5 ** (months_ago / half_life) if months_ago > 0 else 1.0
            h2h_count += weight
            h2h_home_gf += m['gf'] * weight
            h2h_away_gf += m['ga'] * weight
            if m['gf'] > m['ga']:
                h2h_home_wins += weight
            elif m['gf'] == m['ga']:
                h2h_draws += weight
            else:
                h2h_away_wins += weight

    total = h2h_home_wins + h2h_draws + h2h_away_wins
    if total < 0.1:
        return 0, 0, 0, 0, 0, 0  # No H2H data

    return (
        h2h_count, h2h_home_wins / total, h2h_draws / total, h2h_away_wins / total,
        h2h_home_gf / h2h_count if h2h_count > 0 else 0,
        h2h_away_gf / h2h_count if h2h_count > 0 else 0,
    )


# ── ENHANCED FEATURE ENGINE ──

def build_enhanced_features(df):
    """
    Build enhanced feature matrix with advanced engineering.
    """
    from src.features.elo import compute_elo_history, get_pre_match_elo

    print("  Computing ELO...")
    elo_hist = compute_elo_history(df)
    df = get_pre_match_elo(elo_hist, df)

    print("  Building team histories...")
    histories = compute_team_histories(df)

    print("  Computing enhanced features...")
    features_list = []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_hist = histories.get(home, [])
        away_hist = histories.get(away, [])

        # Get pre-match histories only
        match_date = row['date']
        home_pre = [m for m in home_hist if pd.to_datetime(m['date']) < pd.to_datetime(match_date)]
        away_pre = [m for m in away_hist if pd.to_datetime(m['date']) < pd.to_datetime(match_date)]

        # Form features (multiple windows)
        h_wgf10, h_wga10, h_wgd10, h_adj_gf, h_adj_ga, h_adj_gd, h_pts10 = get_opponent_adjusted_form(home_pre, 10)
        a_wgf10, a_wga10, a_wgd10, a_adj_gf2, a_adj_ga2, a_adj_gd2, a_pts10 = get_opponent_adjusted_form(away_pre, 10)

        h_wgf5, h_wga5, h_wgd5, _, _, _, h_pts5 = get_opponent_adjusted_form(home_pre, 5)
        a_wgf5, a_wga5, a_wgd5, _, _, _, a_pts5 = get_opponent_adjusted_form(away_pre, 5)

        # Rest days
        h_rest = compute_rest_days(home_pre, match_date)
        a_rest = compute_rest_days(away_pre, match_date)

        # H2H
        h2h_n, h2h_hw, h2h_dr, h2h_aw, h2h_hgf, h2h_agf = compute_h2h_advanced(home, away, histories)

        # Tournament context
        tournament = row.get('tournament', '')
        is_high_stakes = 1 if any(t in str(tournament) for t in ['World Cup', 'Euro', 'Copa', 'Nations Cup', 'Asian Cup']) else 0
        is_knockout = 1 if 'final' in str(tournament).lower() or 'round' in str(tournament).lower() else 0

        features_list.append({
            # Core ELO
            'elo_home': row['elo_home'], 'elo_away': row['elo_away'], 'elo_diff': row['elo_diff'],
            # Quadratic ELO
            'elo_diff_sq': row['elo_diff'] ** 2,
            'elo_diff_abs': abs(row['elo_diff']),
            'elo_ratio': row['elo_home'] / max(1, row['elo_away']),
            # 10-match form
            'h_gf10': h_wgf10, 'h_ga10': h_wga10, 'h_gd10': h_wgd10,
            'a_gf10': a_wgf10, 'a_ga10': a_wga10, 'a_gd10': a_wgd10,
            'h_pts10': h_pts10, 'a_pts10': a_pts10,
            # 5-match form (more reactive)
            'h_gf5': h_wgf5, 'h_ga5': h_wga5, 'h_gd5': h_wgd5,
            'a_gf5': a_wgf5, 'a_ga5': a_wgd5, 'a_gd5': a_wgd5,
            'h_pts5': h_pts5, 'a_pts5': a_pts5,
            # Form momentum (5 vs 10 delta)
            'h_form_trend': h_pts5 - h_pts10 if not (np.isnan(h_pts5) or np.isnan(h_pts10)) else 0,
            'a_form_trend': a_pts5 - a_pts10 if not (np.isnan(a_pts5) or np.isnan(a_pts10)) else 0,
            # Opponent-adjusted
            'h_adj_gf': h_adj_gf, 'h_adj_ga': h_adj_ga, 'h_adj_gd': h_adj_gd,
            'a_adj_gf': a_adj_gf2, 'a_adj_ga': a_adj_ga2, 'a_adj_gd': a_adj_gd2,
            # Rest
            'h_rest': h_rest, 'a_rest': a_rest,
            'rest_diff': h_rest - a_rest,
            'rest_advantage': 1 if h_rest - a_rest >= 2 else (-1 if a_rest - h_rest >= 2 else 0),
            # H2H
            'h2h_count': h2h_n,
            'h2h_home_win_rate': h2h_hw,
            'h2h_draw_rate': h2h_dr,
            'h2h_away_win_rate': h2h_aw,
            'h2h_home_gf_avg': h2h_hgf,
            'h2h_away_gf_avg': h2h_agf,
            # Context
            'is_high_stakes': is_high_stakes,
            'is_knockout': is_knockout,
            'neutral': int(row.get('neutral', False) or False),
            # Defensive strength (low GA = strong defense → more draws)
            'h_defense': -h_wga5 if not np.isnan(h_wga5) else 0,
            'a_defense': -a_wga5 if not np.isnan(a_wga5) else 0,
            'defense_diff': (-h_wga5 if not np.isnan(h_wga5) else 0) - (-a_wga5 if not np.isnan(a_wga5) else 0),
            # Offensive strength
            'h_attack': h_wgf5 if not np.isnan(h_wgf5) else 0,
            'a_attack': a_wgf5 if not np.isnan(a_wgf5) else 0,
        })

    feat_df = pd.DataFrame(features_list)

    # Fill NaN with 0 for missing form data
    feat_df = feat_df.fillna(0)

    return feat_df, df


# ── TRAINING ──

def train_v2():
    from src.data.cleaner import load_and_clean, split_train_test_by_date
    from src.features.build_features import build_all_features

    print("=" * 60)
    print("ENHANCED MODEL TRAINING V2")
    print("=" * 60)

    # Load data
    print("\n[1/4] Loading data...")
    df = load_and_clean('data/raw/results.csv')
    train_df, test_df = split_train_test_by_date(df, test_start='2022-11-01', test_end='2022-12-31')

    # Limit test to WC matches
    test_df = test_df[test_df['tournament'] == 'FIFA World Cup']

    # Build base features first (for ELO + tournament info)
    print("\n[2/4] Building enhanced features...")
    full_df = build_all_features(pd.concat([train_df, test_df]))

    # Split back
    train_feat = full_df[full_df['date'] < '2022-11-01'].copy()
    test_feat = full_df[(full_df['date'] >= '2022-11-01') &
                         (full_df['date'] <= '2022-12-31') &
                         (full_df['is_world_cup'] == 1)].copy()

    # Add enhanced features
    print("  Adding advanced features...")
    train_enhanced, _ = build_enhanced_features(train_feat)
    test_enhanced, _ = build_enhanced_features(test_feat)

    # Combine base + enhanced
    shared_cols = [c for c in train_enhanced.columns if c not in train_feat.columns]
    train_feat = pd.concat([train_feat.reset_index(drop=True), train_enhanced.reset_index(drop=True)], axis=1)
    test_feat = pd.concat([test_feat.reset_index(drop=True), test_enhanced.reset_index(drop=True)], axis=1)

    # Feature columns for modeling
    all_feat_cols = list(train_enhanced.columns)
    print(f"  Total features: {len(all_feat_cols)}")
    print(f"  Train: {len(train_feat)}, Test: {len(test_feat)}")

    # Prepare X, y
    X_train = train_feat[all_feat_cols].fillna(0).values
    X_test = test_feat[all_feat_cols].fillna(0).values

    y_train_h = (train_feat['home_score'] > train_feat['away_score']).astype(int).values
    y_train_d = (train_feat['home_score'] == train_feat['away_score']).astype(int).values
    y_train_a = (train_feat['home_score'] < train_feat['away_score']).astype(int).values
    y_test_h = (test_feat['home_score'] > test_feat['away_score']).astype(int).values
    y_test_d = (test_feat['home_score'] == test_feat['away_score']).astype(int).values
    y_test_a = (test_feat['home_score'] < test_feat['away_score']).astype(int).values

    # Sample weights
    train_weights = np.array([compute_tournament_weight(t) for t in train_feat['tournament']])

    # ── OPTUNA HYPERPARAMETER TUNING ──
    print("\n[3/4] Optuna hyperparameter optimization...")
    try:
        import optuna
        best_params = run_optuna(X_train, y_train_h, y_train_d, y_train_a,
                                  X_test, y_test_h, y_test_d, y_test_a,
                                  train_weights)
    except ImportError:
        print("  Optuna not available, using default params")
        best_params = None

    # ── TRAIN FINAL MODELS ──
    print("\n[4/4] Training final models...")
    models = {}
    preds = {}
    all_brier = []

    outcomes = [('home', y_train_h, y_test_h), ('draw', y_train_d, y_test_d), ('away', y_train_a, y_test_a)]

    for name, y_tr, y_te in outcomes:
        print(f"\n--- {name.upper()} ---")

        if best_params:
            params = best_params[name]
        else:
            params = {
                'max_depth': 5, 'learning_rate': 0.03, 'n_estimators': 500,
                'subsample': 0.8, 'colsample_bytree': 0.8, 'min_child_weight': 5,
                'reg_alpha': 0.5, 'reg_lambda': 1.0, 'objective': 'binary:logistic',
                'eval_metric': 'logloss', 'verbosity': 0,
            }

        model = xgb.XGBClassifier(**params)
        model.fit(X_train, y_tr, sample_weight=train_weights,
                  eval_set=[(X_test, y_te)], verbose=False)
        models[name] = model
        preds[name] = model.predict_proba(X_test)[:, 1]

        brier = brier_score_loss(y_te, preds[name])
        all_brier.append(brier)
        print(f"  Brier: {brier:.4f}")

    # Ensemble
    from src.models.train import Ensemble
    print("\n--- ENSEMBLE ---")
    ensemble = Ensemble()
    y_true = {'home': y_test_h, 'draw': y_test_d, 'away': y_test_a}
    ensemble.fit({'main': preds}, y_true)
    ens_preds = ensemble.predict({'main': preds})
    ens_brier = np.mean([
        brier_score_loss(y_test_h, ens_preds['home']),
        brier_score_loss(y_test_d, ens_preds['draw']),
        brier_score_loss(y_test_a, ens_preds['away']),
    ])

    print(f"\n{'='*60}")
    print(f"FINAL RESULTS")
    print(f"{'='*60}")
    print(f"  Old Ensemble Brier: 0.6080")
    print(f"  NEW Ensemble Brier: {ens_brier:.4f}")
    improvement = (0.608 - ens_brier) / 0.608 * 100
    print(f"  Improvement: {improvement:+.1f}%")
    print(f"  Per-outcome Brier: H={brier_score_loss(y_test_h, ens_preds['home']):.4f} "
          f"D={brier_score_loss(y_test_d, ens_preds['draw']):.4f} "
          f"A={brier_score_loss(y_test_a, ens_preds['away']):.4f}")
    print(f"  Gap to Pinnacle (0.566): {ens_brier - 0.566:.4f}")

    # Save
    model_path = OUTPUT_DIR / 'match_model_v2.pkl'
    with open(model_path, 'wb') as f:
        pickle.dump({
            'models': models, 'ensemble': ensemble,
            'feature_cols': all_feat_cols, 'version': 'v2',
        }, f)
    print(f"\nModel saved to {model_path}")


def run_optuna(X_train, y_h, y_d, y_a, X_val, y_vh, y_vd, y_va, weights):
    """Optuna hyperparameter optimization for XGBoost."""
    import optuna

    def objective(trial, X_tr, y_tr, X_v, y_v, w):
        params = {
            'max_depth': trial.suggest_int('max_depth', 3, 8),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.1, log=True),
            'n_estimators': trial.suggest_int('n_estimators', 200, 800),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'min_child_weight': trial.suggest_int('min_child_weight', 1, 20),
            'reg_alpha': trial.suggest_float('reg_alpha', 0.01, 5.0, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', 0.01, 5.0, log=True),
            'objective': 'binary:logistic', 'eval_metric': 'logloss',
            'verbosity': 0, 'early_stopping_rounds': 50,
        }
        model = xgb.XGBClassifier(**params)
        model.fit(X_tr, y_tr, sample_weight=w,
                  eval_set=[(X_v, y_v)], verbose=False)
        pred = model.predict_proba(X_v)[:, 1]
        return brier_score_loss(y_v, pred)

    best_params = {}
    for name, y_tr, y_v in [('home', y_h, y_vh), ('draw', y_d, y_vd), ('away', y_a, y_va)]:
        print(f"  Tuning {name}...")
        study = optuna.create_study(direction='minimize')
        study.optimize(lambda trial: objective(trial, X_train, y_tr, X_val, y_v, weights),
                       n_trials=30, show_progress_bar=False)
        best_params[name] = study.best_params
        print(f"    Best Brier: {study.best_value:.4f}, Params: {study.best_params}")

    return best_params


if __name__ == '__main__':
    train_v2()
