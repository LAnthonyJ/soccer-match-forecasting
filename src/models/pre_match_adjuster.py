"""
Pre-match signal quantification framework (Phase 3).

Core formula:
    P_adjusted = P_ML + Σ(signal_i × coefficient_i)

Each signal's coefficient is CALIBRATED from historical data.
Only statistically significant signals (p < 0.1, n >= 20) are applied.

13 pre-match signals:
  46  key_player_missing     Key player missing through injury or suspension
  47  missing_caps_pct       Share of caps held by absent players
  48  missing_goals_pct      Share of goals scored by absent players
  49  starting_xi_strength   Starting lineup strength
  50  formation_change       Formation change
  51  new_manager_matches    Managerial stability
  52  team_news_sentiment    Pre-match news sentiment
  53  injury_severity        Injury severity
  54  tactical_matchup       Tactical matchup
  56  debutants_in_squad     Number of debutants
  57  squad_height_avg       Average squad height
  59  prev_match_extra_time  Extra time in previous match
  60  travel_fatigue         Accumulated travel fatigue
"""
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent.parent / 'outputs' / 'models'


class SignalCalibration:
    """Calibration data for one pre-match signal."""

    def __init__(self, name: str):
        self.name = name
        self.n_examples = 0
        self.coefficients = {}          # {outcome: mean_delta}
        self.p_values = {}              # {outcome: p_value}
        self.confidence_intervals = {}  # {outcome: (lower, upper)}
        self.is_significant = False

    def __repr__(self):
        if self.n_examples == 0:
            return f"Signal({self.name}): uncalibrated"
        parts = [f"Signal({self.name}): n={self.n_examples}"]
        parts.append("ACTIVE" if self.is_significant else "insufficient")
        for k, v in self.coefficients.items():
            parts.append(f"{k}={v:+.3f}")
        return " ".join(parts)


class PreMatchAdjuster:
    """
    Learns and applies pre-match signal adjustments.

    For each signal, finds historical matches where the signal was present,
    compares actual vs predicted outcomes, and computes the average delta
    as the impact coefficient.

    Usage:
        adjuster = PreMatchAdjuster()
        adjuster.calibrate(matches, ml_probs, actual_outcomes)
        adjusted = adjuster.adjust(new_ml_probs, new_signals)
    """

    MIN_EXAMPLES = 20
    P_THRESHOLD = 0.10
    MAX_ADJUSTMENT = 0.08   # cap per-signal adjustment to prevent overfitting
    TIME_HALF_LIFE = 4.0    # years — recent examples weighted more

    SIGNAL_NAMES = [
        'key_player_missing',
        'missing_caps_pct',
        'missing_goals_pct',
        'starting_xi_strength',
        'formation_change',
        'new_manager_matches',
        'team_news_sentiment',
        'injury_severity',
        'tactical_matchup',
        'debutants_in_squad',
        'squad_height_avg',
        'prev_match_extra_time',
        'travel_fatigue',
    ]

    def __init__(self):
        self.calibrations: dict[str, SignalCalibration] = {}

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(self,
                  matches: pd.DataFrame,
                  ml_probs: dict,
                  actual_outcomes: dict,
                  reference_date: pd.Timestamp = None) -> 'PreMatchAdjuster':
        """
        Learn impact coefficients from historical data.

        Args:
            matches: DataFrame with signal columns + 'date'.
                     Each signal column contains the signal value for the
                     HOME team's perspective. The adjuster uses home-side
                     signals and flips for away-side interpretation.
            ml_probs: {'home': array[n], 'draw': array[n], 'away': array[n]}
            actual_outcomes: {'home': array[n], 'draw': array[n], 'away': array[n]}
            reference_date: for time-decay weighting (default: latest match date)
        """
        if reference_date is None:
            reference_date = pd.to_datetime(matches['date']).max()

        for signal_name in self.SIGNAL_NAMES:
            if signal_name not in matches.columns:
                continue

            cal = self._calibrate_one(
                signal_name,
                matches[signal_name].values,
                pd.to_datetime(matches['date']).values,
                ml_probs,
                actual_outcomes,
                reference_date,
            )
            if cal is not None:
                self.calibrations[signal_name] = cal

        return self

    def _calibrate_one(self, name, signal_values, dates,
                       ml_probs, actual, ref_date) -> SignalCalibration:
        """Calibrate a single signal."""
        cal = SignalCalibration(name)

        # Identify rows where signal is active
        if signal_values.dtype in (np.float64, np.float32, float):
            mask = ~np.isnan(signal_values) & (np.abs(signal_values) > 1e-9)
        else:
            mask = ~pd.isna(signal_values) & (signal_values != 0)

        n = mask.sum()
        cal.n_examples = int(n)

        if n < self.MIN_EXAMPLES:
            return cal

        # Time-decay weights
        years_ago = np.abs((ref_date - pd.to_datetime(dates[mask])).days) / 365.25
        weights = np.exp(-np.log(2) * np.asarray(years_ago, dtype=float) / self.TIME_HALF_LIFE)
        weights = np.clip(weights, 0.1, 1.0)

        significant_any = False
        for outcome in ['home', 'draw', 'away']:
            deltas = actual[outcome][mask] - ml_probs[outcome][mask]

            coef = np.average(deltas, weights=weights) if weights.sum() > 0 else np.mean(deltas)
            coef = np.clip(float(coef), -self.MAX_ADJUSTMENT, self.MAX_ADJUSTMENT)

            # Standard error and p-value (weighted)
            residual = deltas - coef
            var = np.average(residual ** 2, weights=weights) if weights.sum() > 0 else np.var(residual)
            se = np.sqrt(var / n) if var > 0 else 1.0
            t_stat = coef / se if se > 0 else 0.0
            p_value = float(2 * stats.t.sf(abs(t_stat), df=max(1, n - 1)))

            cal.coefficients[outcome] = coef
            cal.p_values[outcome] = p_value
            cal.confidence_intervals[outcome] = (
                coef - 1.96 * se, coef + 1.96 * se
            )
            if p_value < self.P_THRESHOLD:
                significant_any = True

        cal.is_significant = significant_any
        return cal

    # ------------------------------------------------------------------
    # Adjustment
    # ------------------------------------------------------------------

    def adjust(self, ml_probs: np.ndarray,
               signals: dict) -> np.ndarray:
        """
        Apply calibrated signal adjustments.

        Args:
            ml_probs: shape (n_matches, 3) → [P(home), P(draw), P(away)]
            signals: dict of {signal_name: array-like or scalar}

        Returns:
            Adjusted probabilities, shape (n_matches, 3), rows sum to 1.
        """
        adjusted = ml_probs.copy().astype(float)
        n = adjusted.shape[0]
        outcome_idx = {'home': 0, 'draw': 1, 'away': 2}

        for signal_name, values in signals.items():
            cal = self.calibrations.get(signal_name)
            if cal is None or not cal.is_significant:
                continue

            # Normalise signal strength to [0, 1]
            if isinstance(values, (int, float)):
                arr = np.full(n, float(values))
            else:
                arr = np.asarray(values, dtype=float)
                arr = np.nan_to_num(arr, nan=0.0)

            mx = np.abs(arr).max()
            if mx > 1.0:
                arr = arr / mx

            for outcome, idx in outcome_idx.items():
                coef = cal.coefficients.get(outcome)
                if coef is not None:
                    adjusted[:, idx] += arr * coef

        # Clip and renormalise
        adjusted = np.clip(adjusted, 0.01, 0.99)
        adjusted /= adjusted.sum(axis=1, keepdims=True)

        return adjusted

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def get_active(self) -> list[str]:
        return [n for n, c in self.calibrations.items() if c.is_significant]

    def summary(self) -> str:
        lines = ["=" * 60,
                 "PreMatchAdjuster — Calibration Summary",
                 "=" * 60]
        active = self.get_active()
        lines.append(f"Signals calibrated: {len(self.calibrations)}")
        lines.append(f"Signals active:    {len(active)}  (p<{self.P_THRESHOLD}, n>={self.MIN_EXAMPLES})")
        lines.append(f"Max adjustment:    ±{self.MAX_ADJUSTMENT:.0%}")
        lines.append("")

        for name, cal in sorted(self.calibrations.items(),
                                 key=lambda x: -x[1].n_examples):
            status = "ACTIVE" if cal.is_significant else "(insufficient)"
            lines.append(f"  {name}  n={cal.n_examples}  {status}")
            for o in ['home', 'draw', 'away']:
                if o in cal.coefficients:
                    ci = cal.confidence_intervals.get(o, ('?', '?'))
                    lines.append(
                        f"    {o:5s}: {cal.coefficients[o]:+.4f}  "
                        f"p={cal.p_values[o]:.3f}  "
                        f"95%CI [{ci[0]:+.3f}, {ci[1]:+.3f}]"
                    )
            lines.append("")
        return "\n".join(lines)

    def save(self, path: str = None):
        import pickle
        p = Path(path or OUTPUT_DIR / 'pre_match_adjuster.pkl')
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'wb') as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str = None):
        import pickle
        p = Path(path or OUTPUT_DIR / 'pre_match_adjuster.pkl')
        with open(p, 'rb') as f:
            return pickle.load(f)
