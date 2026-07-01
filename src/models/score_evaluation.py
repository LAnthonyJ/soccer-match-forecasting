"""
Phase 7: Score prediction evaluation metrics.

Primary metric: Ranked Probability Score (RPS) — the standard proper scoring
rule for ordered categorical outcomes in football score prediction.

Also computes:
  - LogLoss on exact scores
  - Over/Under calibration
  - BTTS calibration
  - MAE on expected goals
  - Model comparison tables
"""
import numpy as np
import pandas as pd

MAX_GOALS = 10


def compute_rps(score_matrix: np.ndarray, actual_home: int, actual_away: int) -> float:
    """
    Compute Ranked Probability Score for one match.

    RPS = sum_{i,j} (F_pred(i,j) - F_obs(i,j))^2
    where F is the CDF (cumulative over the ordered outcome space).

    We use the bivariate ordering: compare total goals first, then goal difference.
    F_pred(i, j) = sum of P(k, l) for all (k, l) where k+l < i+j,
                   or k+l == i+j and k-l < i-j.

    score_matrix: shape (MAX_GOALS, MAX_GOALS) probability matrix.
    """
    # Build CDF: cumulative over total goals
    # Simpler formulation: RPS = 1/(M-1) * sum_{r=1}^{M-1} (P_pred(R <= r) - I(R_obs <= r))^2
    # where R is total goals.
    # For bivariate RPS with home/away, we use the 2D extension:
    # RPS = mean over thresholds of (F_pred - F_obs)^2

    max_total = 2 * (MAX_GOALS - 1)
    rps = 0.0
    n_thresholds = 0

    for t in range(0, max_total + 1):
        # P(total goals <= t)
        pred_cdf = 0.0
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                if i + j <= t:
                    pred_cdf += score_matrix[i, j]
        obs_cdf = 1.0 if (actual_home + actual_away) <= t else 0.0
        rps += (pred_cdf - obs_cdf) ** 2
        n_thresholds += 1

    # Add goal difference thresholds within each total
    for t in range(0, max_total + 1):
        for gd in range(-MAX_GOALS + 1, MAX_GOALS):
            pred_cdf = 0.0
            for i in range(MAX_GOALS):
                for j in range(MAX_GOALS):
                    if i + j < t:
                        pred_cdf += score_matrix[i, j]
                    elif i + j == t and i - j <= gd:
                        pred_cdf += score_matrix[i, j]
            obs_cdf = 1.0 if (actual_home + actual_away < t or
                              (actual_home + actual_away == t and actual_home - actual_away <= gd)) else 0.0
            rps += (pred_cdf - obs_cdf) ** 2
            n_thresholds += 1

    return rps / max(n_thresholds, 1)


def compute_rps_total_goals(score_matrix: np.ndarray,
                             actual_home: int, actual_away: int) -> float:
    """
    Simplified RPS: only over total goals (not goal difference).
    Standard 1D RPS over the total-goals distribution.

    RPS = 1/(M-1) * sum_{r=1}^{M-1} (P(R <= r) - I(R_obs <= r))^2
    """
    max_total = 2 * (MAX_GOALS - 1)
    actual_total = actual_home + actual_away

    rps = 0.0
    pred_cdf = 0.0
    for r in range(0, max_total + 1):
        # P(total == r)
        p_r = 0.0
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                if i + j == r:
                    p_r += score_matrix[i, j]
        pred_cdf += p_r
        obs_cdf = 1.0 if actual_total <= r else 0.0
        rps += (pred_cdf - obs_cdf) ** 2

    return rps / max_total


def compute_rps_vectorized(score_matrices: np.ndarray,
                            actual_scores: np.ndarray) -> float:
    """
    Vectorized RPS over n matches using 1D total-goals formulation.

    score_matrices: shape (n_matches, MAX_GOALS, MAX_GOALS)
    actual_scores: shape (n_matches, 2)

    Returns mean RPS across matches.
    """
    n_matches = len(score_matrices)
    if n_matches == 0:
        return 0.0

    max_total = 2 * (MAX_GOALS - 1)
    rps_total = 0.0

    # Compute total-goals distribution for each match
    # Total goals CDF accumulation
    total_goals_dist = np.zeros((n_matches, max_total + 1))
    for r in range(max_total + 1):
        for i in range(MAX_GOALS):
            j = r - i
            if 0 <= j < MAX_GOALS:
                total_goals_dist[:, r] += score_matrices[:, i, j]

    pred_cdf = np.zeros(n_matches)
    for r in range(0, max_total + 1):
        pred_cdf += total_goals_dist[:, r]
        actual_total = actual_scores[:, 0] + actual_scores[:, 1]
        obs_cdf = (actual_total <= r).astype(float)
        rps_total += np.mean((pred_cdf - obs_cdf) ** 2)

    return rps_total / max_total


def compute_logloss_exact_scores(score_matrices: np.ndarray,
                                  actual_scores: np.ndarray) -> float:
    """
    Mean -log(P(actual_score)) across matches.
    Only considers scores within the MAX_GOALS range.
    """
    n = len(score_matrices)
    if n == 0:
        return 0.0
    logloss = 0.0
    for i in range(n):
        h = int(actual_scores[i, 0])
        a = int(actual_scores[i, 1])
        prob = score_matrices[i, h, a] if h < MAX_GOALS and a < MAX_GOALS else 1e-15
        logloss -= np.log(max(prob, 1e-15))
    return logloss / n


def compute_mae_expected_goals(score_matrices: np.ndarray,
                                actual_scores: np.ndarray) -> dict:
    """Mean Absolute Error on expected goals."""
    n = len(score_matrices)
    if n == 0:
        return {'mae_home': 0, 'mae_away': 0, 'mae_total': 0}

    e_home = np.zeros(n)
    e_away = np.zeros(n)
    for i in range(MAX_GOALS):
        e_home += i * score_matrices[:, i, :].sum(axis=1)
        e_away += i * score_matrices[:, :, i].sum(axis=1)

    mae_h = np.mean(np.abs(e_home - actual_scores[:, 0]))
    mae_a = np.mean(np.abs(e_away - actual_scores[:, 1]))
    mae_t = np.mean(np.abs(e_home + e_away - actual_scores[:, 0] - actual_scores[:, 1]))
    return {'mae_home': mae_h, 'mae_away': mae_a, 'mae_total': mae_t}


def calibration_curve_ou(preds_over: np.ndarray, actual_scores: np.ndarray,
                          n_bins: int = 10) -> dict:
    """
    Calibration analysis for Over/Under 2.5 predictions.

    Returns bucket-level: predicted over%, observed over%, count.
    """
    actual_total = actual_scores[:, 0] + actual_scores[:, 1]
    actual_over = (actual_total > 2.5).astype(float)

    bins = np.linspace(0, 1, n_bins + 1)
    result = []
    for i in range(n_bins):
        mask = (preds_over >= bins[i]) & (preds_over < bins[i + 1])
        if mask.sum() > 0:
            result.append({
                'bin_start': bins[i],
                'bin_end': bins[i + 1],
                'predicted': preds_over[mask].mean(),
                'observed': actual_over[mask].mean(),
                'count': mask.sum(),
            })
    return result


def calibration_curve_btts(preds_btts: np.ndarray, actual_scores: np.ndarray,
                            n_bins: int = 10) -> dict:
    """Calibration analysis for BTTS predictions."""
    actual_btts = ((actual_scores[:, 0] > 0) & (actual_scores[:, 1] > 0)).astype(float)

    bins = np.linspace(0, 1, n_bins + 1)
    result = []
    for i in range(n_bins):
        mask = (preds_btts >= bins[i]) & (preds_btts < bins[i + 1])
        if mask.sum() > 0:
            result.append({
                'bin_start': bins[i],
                'bin_end': bins[i + 1],
                'predicted': preds_btts[mask].mean(),
                'observed': actual_btts[mask].mean(),
                'count': mask.sum(),
            })
    return result


def evaluate_score_predictions(score_matrices: np.ndarray,
                                actual_scores: np.ndarray,
                                model_name: str = 'Model') -> dict:
    """
    Full score prediction evaluation suite.

    Returns dict with all metrics.
    """
    n = len(score_matrices)
    if n == 0:
        return {}

    rps_tg = compute_rps_vectorized(score_matrices, actual_scores)
    ll = compute_logloss_exact_scores(score_matrices, actual_scores)
    mae = compute_mae_expected_goals(score_matrices, actual_scores)

    # Compute O/U and BTTS probabilities for each match
    preds_over = np.zeros(n)
    preds_btts = np.zeros(n)
    for i in range(n):
        over_p = 0.0
        btts_p = 0.0
        for h in range(MAX_GOALS):
            for a in range(MAX_GOALS):
                if h + a > 2.5:
                    over_p += score_matrices[i, h, a]
                if h > 0 and a > 0:
                    btts_p += score_matrices[i, h, a]
        preds_over[i] = over_p
        preds_btts[i] = btts_p

    results = {
        'model': model_name,
        'n_matches': n,
        'rps_total_goals': rps_tg,
        'logloss_exact_score': ll,
        **mae,
        'mean_over25_prob': preds_over.mean(),
        'mean_btts_prob': preds_btts.mean(),
    }

    print(f"\n=== {model_name} Score Evaluation ===")
    print(f"  Matches: {n}")
    print(f"  RPS (total goals): {rps_tg:.4f}")
    print(f"  LogLoss (exact score): {ll:.4f}")
    print(f"  MAE home goals: {mae['mae_home']:.3f}")
    print(f"  MAE away goals: {mae['mae_away']:.3f}")
    print(f"  MAE total goals: {mae['mae_total']:.3f}")
    print(f"  Mean P(Over 2.5): {preds_over.mean():.3f}")
    print(f"  Mean P(BTTS): {preds_btts.mean():.3f}")

    # O/U Calibration
    cal_ou = calibration_curve_ou(preds_over, actual_scores)
    if cal_ou:
        cal_df = pd.DataFrame(cal_ou)
        ece = np.mean([abs(r['predicted'] - r['observed']) * r['count'] / n for r in cal_ou])
        results['ou_calibration_ece'] = ece
        print(f"  O/U ECE: {ece:.4f}")

    return results


def compare_score_models(models_predictions: dict[str, np.ndarray],
                          actual_scores: np.ndarray) -> pd.DataFrame:
    """
    Compare multiple score models on key metrics.

    Args:
        models_predictions: {model_name: score_matrices array (n_matches, MAX_GOALS, MAX_GOALS)}
        actual_scores: shape (n_matches, 2)

    Returns comparison DataFrame.
    """
    rows = []
    for name, mats in models_predictions.items():
        eval_result = evaluate_score_predictions(mats, actual_scores, name)
        rows.append(eval_result)
    return pd.DataFrame(rows).sort_values('rps_total_goals')
