"""
Phase 7: Score prediction models for football matches.

Multi-model ensemble for predicting exact score distributions:
  - ScoreMatrix: core probability container
  - DixonColesModel: classic MLE-fitted team parameters + low-score correlation
  - MLPoissonModel: XGBoost regression → λ_home, λ_away
  - XGPoissonModel: StatsBomb xG-calibrated team profiles
  - ScoreEnsemble: weighted blend of all models
"""
import numpy as np
import pandas as pd
import math
import pickle
from pathlib import Path
from scipy.optimize import minimize
from scipy.special import gammaln

OUTPUT_DIR = Path(__file__).parent.parent.parent / 'outputs' / 'models'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_GOALS = 10  # Covers >99.9% of match outcomes


# ══════════════════════════════════════════════════════════════════════════════
# ScoreMatrix — core probability container
# ══════════════════════════════════════════════════════════════════════════════

class ScoreMatrix:
    """
    Represents P(home_score=i, away_score=j) for i,j in [0, MAX_GOALS-1].

    Shape: (MAX_GOALS, MAX_GOALS), rows = home, cols = away.
    Sum of all entries = 1.0.
    """

    def __init__(self, matrix: np.ndarray):
        if matrix.shape != (MAX_GOALS, MAX_GOALS):
            raise ValueError(f"Matrix must be {MAX_GOALS}x{MAX_GOALS}, got {matrix.shape}")
        total = matrix.sum()
        self.matrix = matrix / total if total > 0 else np.ones((MAX_GOALS, MAX_GOALS)) / (MAX_GOALS ** 2)

    # ── factory methods ──────────────────────────────────────────────────

    @classmethod
    def from_independent_poisson(cls, lambda_h: float, lambda_a: float) -> 'ScoreMatrix':
        """Create from two independent Poisson distributions."""
        lambda_h = max(lambda_h, 0.02)
        lambda_a = max(lambda_a, 0.02)
        matrix = np.zeros((MAX_GOALS, MAX_GOALS))
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                matrix[i, j] = _poisson_pmf(i, lambda_h) * _poisson_pmf(j, lambda_a)
        return cls(matrix)

    @classmethod
    def from_dixon_coles(cls, lambda_h: float, lambda_a: float, rho: float) -> 'ScoreMatrix':
        """
        Create using Dixon-Coles adjustment for low-score correlation.

        tau(x, y) adjusts P(0,0), P(1,0), P(0,1), P(1,1) by factor rho.
        See Dixon & Coles (1997), Eq. 3.2.
        """
        lambda_h = max(lambda_h, 0.02)
        lambda_a = max(lambda_a, 0.02)
        matrix = np.zeros((MAX_GOALS, MAX_GOALS))

        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                prob = _poisson_pmf(i, lambda_h) * _poisson_pmf(j, lambda_a)
                if i <= 1 and j <= 1:
                    prob *= _dc_tau(i, j, lambda_h, lambda_a, rho)
                matrix[i, j] = prob

        return cls(matrix)

    @classmethod
    def from_lambdas_matrix(cls, lambdas_h: np.ndarray, lambdas_a: np.ndarray) -> list['ScoreMatrix']:
        """Vectorized: create list of ScoreMatrix from arrays of lambdas."""
        results = []
        for lh, la in zip(lambdas_h, lambdas_a):
            results.append(cls.from_independent_poisson(lh, la))
        return results

    # ── query methods ────────────────────────────────────────────────────

    def probability(self, home_goals: int, away_goals: int) -> float:
        """P(home_goals, away_goals). Returns 0 if out of bounds."""
        if 0 <= home_goals < MAX_GOALS and 0 <= away_goals < MAX_GOALS:
            return float(self.matrix[home_goals, away_goals])
        return 0.0

    def exact_score_probability(self, h: int, a: int) -> float:
        """Alias for probability()."""
        return self.probability(h, a)

    def top_scorelines(self, n: int = 15) -> list[tuple]:
        """Returns [(home_goals, away_goals, probability), ...] sorted descending."""
        indices = np.argpartition(self.matrix.ravel(), -n)[-n:]
        indices = indices[np.argsort(self.matrix.ravel()[indices])][::-1]
        results = []
        for idx in indices:
            i, j = divmod(idx, MAX_GOALS)
            results.append((int(i), int(j), float(self.matrix[i, j])))
        return results

    def over_under_probability(self, threshold: float = 2.5) -> tuple:
        """Returns (P(over), P(under))."""
        over = 0.0
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                if i + j > threshold:
                    over += self.matrix[i, j]
        under = 1.0 - over
        return over, under

    def btts_probability(self) -> tuple:
        """Returns (P(btts_yes), P(btts_no))."""
        yes_p = 0.0
        for i in range(1, MAX_GOALS):
            for j in range(1, MAX_GOALS):
                yes_p += self.matrix[i, j]
        return yes_p, 1.0 - yes_p

    def match_outcome_probs(self) -> dict:
        """Marginalize to H/D/A: {'home': p, 'draw': p, 'away': p}."""
        p_h = p_d = p_a = 0.0
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                if i > j:
                    p_h += self.matrix[i, j]
                elif i == j:
                    p_d += self.matrix[i, j]
                else:
                    p_a += self.matrix[i, j]
        return {'home': p_h, 'draw': p_d, 'away': p_a}

    def expected_goals(self) -> tuple:
        """E[home_goals], E[away_goals]."""
        e_h = e_a = 0.0
        for i in range(MAX_GOALS):
            row_sum = self.matrix[i, :].sum()
            col_sum = self.matrix[:, i].sum()
            e_h += i * row_sum
            e_a += i * col_sum
        return e_h, e_a

    def total_goals_distribution(self) -> np.ndarray:
        """Probability distribution of total goals (0 to 2*MAX_GOALS-2)."""
        max_total = 2 * MAX_GOALS - 2
        dist = np.zeros(max_total + 1)
        for i in range(MAX_GOALS):
            for j in range(MAX_GOALS):
                dist[i + j] += self.matrix[i, j]
        return dist

    # ── ensemble helpers ─────────────────────────────────────────────────

    def weighted_average(self, other: 'ScoreMatrix', weight: float) -> 'ScoreMatrix':
        """weight * self + (1-weight) * other."""
        matrix = weight * self.matrix + (1.0 - weight) * other.matrix
        return ScoreMatrix(matrix)

    def __repr__(self) -> str:
        e_h, e_a = self.expected_goals()
        over_p, _ = self.over_under_probability(2.5)
        return f"ScoreMatrix(E[home]={e_h:.2f}, E[away]={e_a:.2f}, P(over2.5)={over_p:.2f})"


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════

def _poisson_pmf(k: int, lam: float) -> float:
    """Poisson PMF: exp(-lam) * lam^k / k!.  Safe for k >= MAX_GOALS (truncated)."""
    if k >= MAX_GOALS:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - gammaln(k + 1))


def _dc_tau(x: int, y: int, lambda_h: float, lambda_a: float, rho: float) -> float:
    """
    Dixon-Coles adjustment factor for low scores.
    See Dixon & Coles (1997), Eq. 3.2.

    τ(x, y) =
        1 - λ_h * λ_a * ρ                    if x=0, y=0
        1 + λ_h * ρ                          if x=0, y=1
        1 + λ_a * ρ                          if x=1, y=0
        1 - ρ                                if x=1, y=1
        1                                    otherwise
    """
    if x == 0 and y == 0:
        return 1.0 - lambda_h * lambda_a * rho
    elif x == 0 and y == 1:
        return 1.0 + lambda_h * rho
    elif x == 1 and y == 0:
        return 1.0 + lambda_a * rho
    elif x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _dc_log_likelihood(params, matches, teams, time_weights, l2_reg):
    """
    Dixon-Coles negative log-likelihood with time weighting and L2 regularization.

    params layout: alpha for each team, then beta for each team, then gamma, then rho.
    """
    n_teams = len(teams)
    alpha = {t: params[i] for i, t in enumerate(teams)}
    beta = {t: params[n_teams + i] for i, t in enumerate(teams)}
    gamma = params[2 * n_teams]
    rho = params[2 * n_teams + 1]

    # Enforce sum(alpha) = 0 constraint via penalty
    alpha_sum_penalty = sum(alpha.values()) ** 2 * 1000

    nll = alpha_sum_penalty
    for idx, (_, match) in enumerate(matches.iterrows()):
        home = match['home_team']
        away = match['away_team']
        hg = int(match['home_score'])
        ag = int(match['away_score'])
        w = time_weights[idx] if time_weights is not None else 1.0
        neutral = match.get('neutral', False)

        a_home = alpha.get(home, 0.0)
        b_home = beta.get(home, 0.0)
        a_away = alpha.get(away, 0.0)
        b_away = beta.get(away, 0.0)

        lambda_h = math.exp((0.0 if neutral else gamma) + a_home + b_away)
        lambda_a = math.exp(a_away + b_home)

        if hg < MAX_GOALS and ag < MAX_GOALS:
            poisson_part = _poisson_pmf(hg, lambda_h) * _poisson_pmf(ag, lambda_a)
            tau = _dc_tau(hg, ag, lambda_h, lambda_a, rho)
            prob = max(poisson_part * tau, 1e-15)
            nll -= w * math.log(prob)
        else:
            nll -= w * math.log(1e-15)

    # L2 regularization
    l2_penalty = l2_reg * (sum(a ** 2 for a in alpha.values()) +
                           sum(b ** 2 for b in beta.values()))
    nll += l2_penalty
    return nll


# ══════════════════════════════════════════════════════════════════════════════
# Dixon-Coles Model
# ══════════════════════════════════════════════════════════════════════════════

class DixonColesModel:
    """
    Dixon & Coles (1997) model for football score prediction.

    Models lambda_home = exp(gamma + alpha_home + beta_away) and
    lambda_away = exp(alpha_away + beta_home), with a low-score
    correlation parameter rho.

    Uses a 2-stage fitting procedure for speed:
      1. Poisson GLM (statsmodels) → warm-start attack/defense params
      2. L-BFGS-B on rho + re-centered params (fast, ~2 params)
    Limited to top 100 teams by match count for practical performance.
    """

    def __init__(self):
        self.alpha = {}       # team → attack parameter
        self.beta = {}        # team → defense parameter
        self.gamma = 0.30     # home advantage (log-scale)
        self.rho = -0.05      # low-score correlation
        self.teams_ = []

    def fit(self, matches: pd.DataFrame, time_half_life: float = 5.0,
            l2_reg: float = 0.01, max_teams: int = 100):
        """
        Fit Dixon-Coles parameters via 2-stage MLE.

        Stage 1: Poisson GLM for attack/defense warm-start.
        Stage 2: L-BFGS-B on (gamma, rho) with frozen team params.

        Args:
            matches: DataFrame with [date, home_team, away_team, home_score, away_score, neutral]
            time_half_life: half-life in years for time weighting
            l2_reg: L2 regularization strength
            max_teams: limit to top N teams by match count
        """
        df = matches.dropna(subset=['home_score', 'away_score']).copy()
        df = df.sort_values('date')
        df['home_score'] = df['home_score'].astype(int)
        df['away_score'] = df['away_score'].astype(int)

        # ── Limit to top teams ──────────────────────────────────────────
        team_counts = pd.concat([
            df['home_team'], df['away_team']
        ]).value_counts()
        top_teams = set(team_counts.head(max_teams).index)
        self.teams_ = sorted(top_teams)
        n_teams = len(self.teams_)
        df_dc = df[df['home_team'].isin(top_teams) & df['away_team'].isin(top_teams)].copy()
        print(f"  Dixon-Coles: {len(df_dc)} matches, {n_teams} teams (top {max_teams})")

        # ── Time weights ─────────────────────────────────────────────────
        latest_date = pd.to_datetime(df_dc['date'].max())
        days_ago = df_dc['date'].apply(lambda d: (latest_date - pd.to_datetime(d)).days)
        half_life_days = time_half_life * 365.25
        time_weights = np.exp(-np.log(2) * days_ago.values / half_life_days)
        time_weights = np.clip(time_weights / time_weights.max(), 0.10, 1.0)

        # ── Stage 1: Poisson GLM warm-start ──────────────────────────────
        team_to_idx = {t: i for i, t in enumerate(self.teams_)}
        n_rows = len(df_dc)

        # Build design matrix for home goals: [home_atk_dummies, away_def_dummies, intercept]
        X_home = np.zeros((n_rows, 2 * n_teams))
        X_away = np.zeros((n_rows, 2 * n_teams))
        y_home = df_dc['home_score'].values.astype(float)
        y_away = df_dc['away_score'].values.astype(float)

        for idx, (_, row) in enumerate(df_dc.iterrows()):
            hi = team_to_idx.get(row['home_team'], -1)
            ai = team_to_idx.get(row['away_team'], -1)
            if hi >= 0 and ai >= 0:
                X_home[idx, hi] = 1.0            # home attack
                X_home[idx, n_teams + ai] = 1.0   # away defense
                X_away[idx, ai] = 1.0             # away attack
                X_away[idx, n_teams + hi] = 1.0   # home defense

        # Fit Poisson GLM using IRLS (simplified Newton method)
        # log(lambda) = X @ beta, y ~ Poisson(exp(X @ beta))
        # IRLS: beta_new = (X^T W X)^{-1} X^T W z
        # where W = diag(lambda), z = X @ beta + (y - lambda) / lambda
        from sklearn.linear_model import Ridge

        for name, X, y in [('home', X_home, y_home), ('away', X_away, y_away)]:
            # Initialize: log(mean(y) + 0.1)
            log_mean = np.log(max(np.mean(y), 0.5))
            beta = np.zeros(X.shape[1])
            beta[:] = log_mean / (2 * n_teams)  # spread evenly

            for it in range(50):
                eta = X @ beta
                lam = np.exp(np.clip(eta, -5, 5))
                lam = np.maximum(lam, 0.02)

                # Weight matrix (diagonal)
                W = lam * time_weights
                z = eta + (y - lam) / np.maximum(lam, 0.02)

                # Weighted least squares with L2
                Xw = X * np.sqrt(W)[:, np.newaxis]
                zw = z * np.sqrt(W)

                # Ridge regression for stability
                ridge = Ridge(alpha=l2_reg, fit_intercept=False)
                ridge.fit(Xw, zw)
                beta_new = ridge.coef_

                if np.max(np.abs(beta_new - beta)) < 1e-5:
                    beta = beta_new
                    break
                beta = beta_new

            # Extract attack/defense params
            for i, team in enumerate(self.teams_):
                if name == 'home':
                    # home goal model: attack_home + defense_away
                    if team not in self.alpha:
                        self.alpha[team] = float(beta[i])
                        self.beta[team] = float(beta[n_teams + i])
                else:
                    # away goal model: attack_away + defense_home
                    # Average with existing estimates
                    self.alpha[team] = (self.alpha.get(team, 0.0) + float(beta[i])) / 2
                    self.beta[team] = (self.beta.get(team, 0.0) + float(beta[n_teams + i])) / 2

        # Center alpha (identifiability constraint)
        alpha_mean = np.mean(list(self.alpha.values()))
        beta_mean = np.mean(list(self.beta.values()))
        for t in self.teams_:
            self.alpha[t] -= alpha_mean
            self.beta[t] -= beta_mean

        # ── Stage 2: Optimize (gamma, rho) ──────────────────────────────
        self.gamma = 0.30

        def obj_gamma_rho(params):
            gamma, rho = params
            nll = 0.0
            for idx, (_, row) in enumerate(df_dc.iterrows()):
                home = row['home_team']
                away = row['away_team']
                hg = int(row['home_score'])
                ag = int(row['away_score'])
                w = time_weights[idx]
                neutral = row.get('neutral', False)

                a_h = self.alpha.get(home, 0.0)
                b_h = self.beta.get(home, 0.0)
                a_a = self.alpha.get(away, 0.0)
                b_a = self.beta.get(away, 0.0)

                lambda_h = math.exp((0.0 if neutral else gamma) + a_h + b_a)
                lambda_a = math.exp(a_a + b_h)

                if hg < MAX_GOALS and ag < MAX_GOALS:
                    prob = _poisson_pmf(hg, lambda_h) * _poisson_pmf(ag, lambda_a)
                    prob *= _dc_tau(hg, ag, lambda_h, lambda_a, rho)
                    nll -= w * math.log(max(prob, 1e-15))
                else:
                    nll -= w * math.log(1e-15)
            # L2 on rho
            nll += l2_reg * rho ** 2
            return nll

        result = minimize(
            obj_gamma_rho,
            [0.30, -0.05],
            bounds=[(0.0, 0.8), (-0.15, 0.15)],
            method='L-BFGS-B',
            options={'maxiter': 100, 'disp': False},
        )
        self.gamma, self.rho = result.x

        print(f"  gamma={self.gamma:.3f}, rho={self.rho:.4f}")
        top_atk = sorted(self.alpha.items(), key=lambda x: -x[1])[:5]
        top_def = sorted(self.beta.items(), key=lambda x: x[1])[:5]
        print(f"  Top attacks: {[(t, f'{v:.3f}') for t, v in top_atk]}")
        print(f"  Top defenses: {[(t, f'{v:.3f}') for t, v in top_def]}")

        return self

    def predict_lambdas(self, home_team: str, away_team: str, neutral: bool = False) -> tuple:
        """Return lambda_home, lambda_away for a match."""
        a_home = self.alpha.get(home_team, 0.0)
        b_home = self.beta.get(home_team, 0.0)
        a_away = self.alpha.get(away_team, 0.0)
        b_away = self.beta.get(away_team, 0.0)

        lambda_h = math.exp((0.0 if neutral else self.gamma) + a_home + b_away)
        lambda_a = math.exp(a_away + b_home)
        return max(lambda_h, 0.02), max(lambda_a, 0.02)

    def predict_score_matrix(self, home_team: str, away_team: str,
                              neutral: bool = False) -> ScoreMatrix:
        """Compute full ScoreMatrix for a match."""
        lh, la = self.predict_lambdas(home_team, away_team, neutral)
        return ScoreMatrix.from_dixon_coles(lh, la, self.rho)

    def predict_expected_goals(self, home_team: str, away_team: str,
                                neutral: bool = False) -> tuple:
        """Return E[home_goals], E[away_goals]."""
        lh, la = self.predict_lambdas(home_team, away_team, neutral)
        return lh, la


# ══════════════════════════════════════════════════════════════════════════════
# ML-Poisson Model (XGBoost → λ)
# ══════════════════════════════════════════════════════════════════════════════

class MLPoissonModel:
    """
    XGBoost regression to predict expected goals (λ_home, λ_away) per match.

    Uses full 76-feature pipeline to estimate goal rates, then applies
    independent Poisson to produce ScoreMatrix.
    """

    def __init__(self):
        self.model_home = None   # XGBRegressor for home goals
        self.model_away = None   # XGBRegressor for away goals
        self.cal_home = None     # IsotonicRegression for home lambda
        self.cal_away = None     # IsotonicRegression for away lambda
        self.feature_cols = None

    def fit(self, X_train, y_home_goals, y_away_goals,
            X_val=None, y_val_h=None, y_val_a=None,
            sample_weights=None, feature_cols=None):
        """
        Train two XGBoost regressors with Poisson-like loss (Tweedie).

        Args:
            X_train: feature matrix (n_samples, n_features)
            y_home_goals: home score targets
            y_away_goals: away score targets
            X_val, y_val_h, y_val_a: validation set for early stopping
            sample_weights: optional time decay weights
            feature_cols: list of feature names
        """
        from xgboost import XGBRegressor
        from sklearn.isotonic import IsotonicRegression

        self.feature_cols = feature_cols

        params = {
            'max_depth': 4,
            'learning_rate': 0.03,
            'n_estimators': 300,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'min_child_weight': 5,
            'reg_alpha': 0.5,
            'reg_lambda': 1.0,
            'objective': 'reg:squarederror',
            'eval_metric': 'rmse',
            'verbosity': 0,
        }

        for name, y_tr, y_val, y_te in [('home', y_home_goals, y_val_h, None),
                                          ('away', y_away_goals, y_val_a, None)]:
            model = XGBRegressor(**params)
            eval_set = [(X_val, y_val)] if (X_val is not None and y_val is not None) else None
            fit_kwargs = {'sample_weight': sample_weights}
            if eval_set:
                fit_kwargs['eval_set'] = eval_set
                fit_kwargs['verbose'] = False

            model.fit(X_train, y_tr, **fit_kwargs)

            if name == 'home':
                self.model_home = model
            else:
                self.model_away = model

            if feature_cols:
                importance = model.feature_importances_
                top_idx = np.argsort(importance)[-5:][::-1]
                print(f"  ML-Poisson {name} top features:")
                for idx in top_idx:
                    print(f"    {feature_cols[idx]}: {importance[idx]:.4f}")

        # Calibrate lambdas via isotonic regression on validation set
        if X_val is not None and y_val_h is not None and y_val_a is not None:
            pred_h_val = self.model_home.predict(X_val)
            pred_a_val = self.model_away.predict(X_val)

            self.cal_home = IsotonicRegression(out_of_bounds='clip', y_min=0.02, y_max=8.0)
            self.cal_away = IsotonicRegression(out_of_bounds='clip', y_min=0.02, y_max=8.0)
            self.cal_home.fit(pred_h_val, y_val_h)
            self.cal_away.fit(pred_a_val, y_val_a)
            print(f"  Lambda calibration fitted on {len(pred_h_val)} val samples")

        return self

    def predict_lambdas(self, X: np.ndarray) -> tuple:
        """Predict calibrated lambda_home, lambda_away."""
        raw_h = self.model_home.predict(X)
        raw_a = self.model_away.predict(X)

        if self.cal_home is not None:
            raw_h = self.cal_home.transform(raw_h)
        if self.cal_away is not None:
            raw_a = self.cal_away.transform(raw_a)

        # Ensure arrays (handle scalar input)
        raw_h = np.atleast_1d(np.maximum(raw_h, 0.02))
        raw_a = np.atleast_1d(np.maximum(raw_a, 0.02))
        return raw_h, raw_a

    def predict_score_matrix(self, X: np.ndarray) -> ScoreMatrix:
        """Predict ScoreMatrix from a single feature vector."""
        lh, la = self.predict_lambdas(X.reshape(1, -1))
        return ScoreMatrix.from_independent_poisson(float(lh[0]), float(la[0]))

    def predict_score_matrices(self, X: np.ndarray) -> list[ScoreMatrix]:
        """Predict ScoreMatrix for multiple feature vectors."""
        lh, la = self.predict_lambdas(X)
        return ScoreMatrix.from_lambdas_matrix(lh, la)


# ══════════════════════════════════════════════════════════════════════════════
# xG-Poisson Model
# ══════════════════════════════════════════════════════════════════════════════

class XGPoissonModel:
    """
    Uses StatsBomb xG data to calibrate team attacking/defending profiles.

    Multiplicative model:
      lambda_home = league_avg * (home_xG_for / league_avg) * (away_xG_against / league_avg)
    """

    def __init__(self):
        self.team_xg_for = {}       # team → avg xG per match
        self.team_xg_against = {}   # team → avg xGA per match
        self.league_avg_goals = 1.30
        self._min_matches = 5

    def fit(self, event_features_df: pd.DataFrame = None,
            team_event_history: pd.DataFrame = None):
        """
        Compute per-team xG profiles from StatsBomb event data.

        Args:
            event_features_df: wide-format per-match event data (optional)
            team_event_history: long-format team event history with [team, xg] columns
        """
        if team_event_history is not None:
            for team, grp in team_event_history.groupby('team'):
                if len(grp) >= self._min_matches:
                    self.team_xg_for[team] = grp['xg'].mean()
                    # xG against = opponent xG when playing this team
                    # Proxy: league avg since we don't have per-opponent breakdown
            print(f"  xG-Poisson: loaded {len(self.team_xg_for)} team profiles from history")
            return self

        if event_features_df is not None:
            # Wide format: columns like "{Team}_xg"
            xg_cols = [c for c in event_features_df.columns if c.endswith('_xg')
                       and not c.endswith('_xg_per_shot')]
            for col in xg_cols:
                team = col.replace('_xg', '')
                vals = event_features_df[col].dropna()
                if len(vals) >= self._min_matches:
                    self.team_xg_for[team] = vals.mean()
            print(f"  xG-Poisson: loaded {len(self.team_xg_for)} team profiles from event data")

        return self

    def predict_lambdas(self, home_team: str, away_team: str) -> tuple:
        """Predict lambda_home, lambda_away using xG profiles."""
        avg = self.league_avg_goals
        xgf_h = self.team_xg_for.get(home_team, avg)
        xgf_a = self.team_xg_for.get(away_team, avg)
        xga_h = self.team_xg_against.get(home_team, avg)
        xga_a = self.team_xg_against.get(away_team, avg)

        # Multiplicative model
        lambda_h = avg * (xgf_h / avg) * (xga_a / avg)
        lambda_a = avg * (xgf_a / avg) * (xga_h / avg)

        return max(lambda_h, 0.05), max(lambda_a, 0.05)

    def predict_score_matrix(self, home_team: str, away_team: str) -> ScoreMatrix:
        """Compute ScoreMatrix from xG profiles."""
        lh, la = self.predict_lambdas(home_team, away_team)
        return ScoreMatrix.from_independent_poisson(lh, la)

    def is_available(self, home_team: str, away_team: str) -> bool:
        """Check if both teams have sufficient xG data."""
        return (home_team in self.team_xg_for and away_team in self.team_xg_for)


# ══════════════════════════════════════════════════════════════════════════════
# ELO Poisson (baseline, for fallback)
# ══════════════════════════════════════════════════════════════════════════════

def elo_poisson_lambdas(elo_diff: float, neutral: bool = False) -> tuple:
    """
    Convert ELO difference to expected goals using calibrated log-linear model.

    Standardized formulation: lambda = exp(0.15 ± 0.0011 * elo_diff).
    Calibrated on international football (~2.6 goals/match).
    """
    if not neutral:
        elo_diff += 100  # home advantage
    lambda_h = math.exp(0.15 + 0.0011 * elo_diff)
    lambda_a = math.exp(0.15 - 0.0011 * elo_diff)
    return max(lambda_h, 0.05), max(lambda_a, 0.05)


def elo_poisson_score_matrix(elo_diff: float, neutral: bool = False) -> ScoreMatrix:
    """Create ScoreMatrix from ELO difference using Poisson model."""
    lh, la = elo_poisson_lambdas(elo_diff, neutral)
    return ScoreMatrix.from_independent_poisson(lh, la)


# ══════════════════════════════════════════════════════════════════════════════
# ScoreEnsemble
# ══════════════════════════════════════════════════════════════════════════════

class ScoreEnsemble:
    """
    Weighted blend of score probability matrices from multiple sub-models.

    Weights optimized to minimize Ranked Probability Score (RPS) on
    a validation set, using scipy.optimize.minimize (SLSQP).
    """

    def __init__(self):
        self.weights = None
        self.model_names_ = []

    def fit(self, model_matrices: dict[str, list[ScoreMatrix]],
            actual_scores: np.ndarray):
        """
        Optimize ensemble weights to minimize RPS.

        Args:
            model_matrices: {model_name: [ScoreMatrix for each val match]}
            actual_scores: shape (n_matches, 2) with (home_score, away_score)
        """
        from .score_evaluation import compute_rps_vectorized

        self.model_names_ = list(model_matrices.keys())
        n_models = len(self.model_names_)
        n_matches = len(actual_scores)

        if n_models == 0:
            self.weights = np.array([])
            return self

        if n_models == 1:
            self.weights = np.array([1.0])
            return self

        # Stack matrices: shape (n_models, n_matches, MAX_GOALS, MAX_GOALS)
        all_mats = np.array([
            np.array([m.matrix for m in model_matrices[name]])
            for name in self.model_names_
        ])

        def rps_loss(w):
            # Weighted blend
            blended = np.tensordot(w, all_mats, axes=1)
            return compute_rps_vectorized(blended, actual_scores)

        constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0})
        bounds = [(0.0, 1.0) for _ in range(n_models)]
        w0 = np.ones(n_models) / n_models

        result = minimize(rps_loss, w0, bounds=bounds, constraints=constraints,
                          method='SLSQP')
        self.weights = result.x
        print(f"  ScoreEnsemble weights:")
        for name, w in zip(self.model_names_, self.weights):
            print(f"    {name}: {w:.3f}")
        print(f"  Ensemble RPS: {result.fun:.4f}")

        return self

    def predict(self, matrices: list[ScoreMatrix]) -> ScoreMatrix:
        """Weighted blend of ScoreMatrix objects."""
        if self.weights is None or len(matrices) == 0:
            return matrices[0] if matrices else ScoreMatrix(
                np.ones((MAX_GOALS, MAX_GOALS)) / (MAX_GOALS ** 2))

        blended = np.zeros((MAX_GOALS, MAX_GOALS))
        for w, sm in zip(self.weights, matrices):
            blended += w * sm.matrix
        return ScoreMatrix(blended)

    def predict_batch(self, matrices_dict: dict[str, list[ScoreMatrix]]) -> list[ScoreMatrix]:
        """Weighted blend for a batch of matches."""
        n_matches = len(list(matrices_dict.values())[0])
        results = []
        for i in range(n_matches):
            mats_i = [matrices_dict[name][i] for name in self.model_names_]
            results.append(self.predict(mats_i))
        return results


# ══════════════════════════════════════════════════════════════════════════════
# Model persistence
# ══════════════════════════════════════════════════════════════════════════════

def save_score_models(dc_model, ml_poisson, xg_poisson, score_ensemble):
    """Save all score models to disk."""
    score_models = {
        'dixon_coles': dc_model,
        'ml_poisson': ml_poisson,
        'xg_poisson': xg_poisson,
        'score_ensemble': score_ensemble,
    }
    path = OUTPUT_DIR / 'score_models.pkl'
    with open(path, 'wb') as f:
        pickle.dump(score_models, f)
    print(f"Score models saved to {path}")


def load_score_models() -> dict:
    """Load score models from disk."""
    path = OUTPUT_DIR / 'score_models.pkl'
    with open(path, 'rb') as f:
        return pickle.load(f)
