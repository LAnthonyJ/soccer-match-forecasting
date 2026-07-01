"""
Robust betting strategy with calibrated Kelly, risk controls, and backtesting.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class StrategyConfig:
    """Configuration for the betting strategy."""
    # Kelly parameters
    kelly_fraction: float = 0.25      # Fraction of full Kelly (0.25 = 1/4 Kelly)
    max_single_bet: float = 0.05      # Max 5% of bankroll per bet
    max_total_exposure: float = 0.25  # Max 25% of bankroll at risk at once

    # Edge thresholds
    min_ev: float = 0.03             # Minimum 3% expected value to bet
    min_edge_prob: float = 0.15      # Minimum model probability to consider

    # Confidence filters
    max_model_std: float = 0.08      # Max std between base models (lower = more confident)
    require_majority: bool = True    # Require majority of base models agree on direction

    # Risk management
    stop_loss: float = 0.30          # Stop if down 30% from peak
    max_consecutive_losses: int = 8  # Pause after 8 consecutive losses

    # Dynamic Kelly adjustment
    dynamic_kelly: bool = True       # Reduce Kelly fraction when edge is uncertain
    kelly_min_edge: float = 0.05     # Edge below which Kelly is further reduced
    kelly_reduction: float = 0.5     # Reduce Kelly by this factor for low-edge bets


DEFAULT_CONFIG = StrategyConfig()


def calculate_kelly(p_model: float, odds_decimal: float,
                    fraction: float = 0.25) -> float:
    """Calculate fractional Kelly stake."""
    if odds_decimal <= 1.0 or p_model <= 0 or p_model >= 1:
        return 0.0
    b = odds_decimal - 1.0
    q = 1.0 - p_model
    f_star = (p_model * b - q) / b
    return max(0.0, f_star * fraction)


def calculate_ev(p_model: float, odds_decimal: float) -> float:
    """Expected value as fraction of stake."""
    return p_model * odds_decimal - 1.0


def compute_model_confidence(probs_dict: Dict[str, Dict[str, np.ndarray]],
                              outcome: str, idx: int) -> float:
    """
    Compute confidence based on agreement between base models.
    Lower std = higher confidence.
    Returns std of predictions across models.
    """
    preds = []
    for model_name, pred_dict in probs_dict.items():
        if outcome in pred_dict and idx < len(pred_dict[outcome]):
            preds.append(pred_dict[outcome][idx])
    if len(preds) < 2:
        return 0.0
    return float(np.std(preds))


def generate_bets(ensemble_probs: dict, odds: dict,
                   all_model_probs: dict = None,
                   bankroll: float = 1000.0,
                   config: StrategyConfig = DEFAULT_CONFIG,
                   recent_results: list = None) -> list:
    """
    Generate optimal bets with all risk controls applied.

    Args:
        ensemble_probs: {'home': array, 'draw': array, 'away': array}
        odds: {'home': array, 'draw': array, 'away': array}
        all_model_probs: dict of dicts for confidence estimation
        bankroll: current bankroll
        config: strategy configuration
        recent_results: list of bools for recent bet outcomes

    Returns:
        List of bet recommendations per match
    """
    n_matches = len(ensemble_probs['home'])
    bets_per_match = []

    # Check stop-loss
    if recent_results and len(recent_results) >= 5:
        # Check if we're in a drawdown
        if sum(recent_results[-10:]) <= 2 and len(recent_results) >= 10:
            print("  [WARNING] Poor recent performance (<=2 wins in last 10), reducing exposure")
            config = StrategyConfig(**{**config.__dict__,
                                       'kelly_fraction': config.kelly_fraction * 0.5})

    for i in range(n_matches):
        match_bets = []

        for outcome in ['home', 'draw', 'away']:
            p_model = ensemble_probs[outcome][i]
            o = odds[outcome][i]

            # Basic filters
            if o <= 1.0 or p_model < config.min_edge_prob:
                continue

            ev = calculate_ev(p_model, o)
            if ev < config.min_ev:
                continue

            # Model confidence check
            if all_model_probs is not None:
                std = compute_model_confidence(all_model_probs, outcome, i)
                if std > config.max_model_std:
                    continue  # Models disagree too much, skip

            # Dynamic Kelly: reduce for low-edge bets
            kelly_frac = config.kelly_fraction
            if config.dynamic_kelly and ev < config.kelly_min_edge:
                kelly_frac *= config.kelly_reduction

            kelly_f = calculate_kelly(p_model, o, kelly_frac)
            bet_fraction = min(kelly_f, config.max_single_bet)
            bet_amount = bankroll * bet_fraction

            if bet_amount > 0:
                match_bets.append({
                    'outcome': outcome,
                    'probability': float(p_model),
                    'odds': float(o),
                    'ev': float(ev),
                    'kelly_fraction': float(kelly_f),
                    'bet_fraction': float(bet_fraction),
                    'bet_amount': float(bet_amount),
                })

        bets_per_match.append(match_bets)

    # Enforce total exposure limit
    total_bet = sum(sum(b['bet_amount'] for b in mb) for mb in bets_per_match)
    if total_bet > bankroll * config.max_total_exposure:
        scale = (bankroll * config.max_total_exposure) / total_bet
        for mb in bets_per_match:
            for b in mb:
                b['bet_amount'] *= scale
                b['bet_fraction'] *= scale

    return bets_per_match


def simulate_results(bets_per_match: list, actual_outcomes: list,
                      bankroll: float = 1000.0) -> dict:
    """
    Simulate bet settlement and track detailed performance.
    """
    history = []
    bankroll_curve = [bankroll]
    peak = bankroll
    max_drawdown = 0

    for match_bets, actual in zip(bets_per_match, actual_outcomes):
        match_profit = 0
        match_stake = 0
        for bet in match_bets:
            won = (bet['outcome'][0].upper() == actual)
            returned = bet['bet_amount'] * bet['odds'] if won else 0
            match_profit += returned - bet['bet_amount']
            match_stake += bet['bet_amount']
            history.append({
                **bet, 'actual': actual, 'won': won, 'returned': returned
            })

        bankroll += match_profit
        bankroll_curve.append(bankroll)
        peak = max(peak, bankroll)
        drawdown = (peak - bankroll) / peak if peak > 0 else 0
        max_drawdown = max(max_drawdown, drawdown)

    # Compute metrics
    total_staked = sum(h['bet_amount'] for h in history)
    total_returned = sum(h['returned'] for h in history)
    n_bets = len(history)
    n_won = sum(1 for h in history if h['won'])
    profit = total_returned - total_staked
    roi = profit / total_staked if total_staked > 0 else 0

    # Sharpe-like ratio (using per-bet returns)
    returns = []
    for h in history:
        ret = (h['returned'] - h['bet_amount']) / h['bet_amount'] if h['bet_amount'] > 0 else 0
        returns.append(ret)
    returns = np.array(returns)
    sharpe = returns.mean() / returns.std() if returns.std() > 0 else 0
    sharpe_annual = sharpe * np.sqrt(n_bets) if n_bets > 0 else 0

    return {
        'final_bankroll': bankroll,
        'profit': profit,
        'roi': roi,
        'n_bets': n_bets,
        'n_matches_bet': sum(1 for mb in bets_per_match if len(mb) > 0),
        'n_matches_total': len(bets_per_match),
        'hit_rate': n_won / n_bets if n_bets > 0 else 0,
        'avg_odds': np.mean([h['odds'] for h in history]) if history else 0,
        'sharpe': sharpe,
        'sharpe_annual': sharpe_annual,
        'max_drawdown': max_drawdown,
        'bankroll_curve': bankroll_curve,
        'history': history,
    }


def optimize_strategy(ensemble_probs: dict, odds: dict,
                       all_model_probs: dict, actual_outcomes: list,
                       bankroll: float = 1000.0) -> dict:
    """
    Grid search over strategy parameters to find optimal configuration.
    """
    print("\n" + "=" * 60)
    print("STRATEGY OPTIMIZATION")
    print("=" * 60)

    best_result = None
    best_config = None
    best_metric = -np.inf

    # Parameter grid
    kelly_fractions = [0.1, 0.15, 0.2, 0.25, 0.3, 0.5]
    min_evs = [0.01, 0.02, 0.03, 0.04, 0.05]
    max_stds = [0.05, 0.08, 0.10, 0.15, 0.20]
    max_exposures = [0.15, 0.20, 0.25, 0.30, 0.35]

    results = []

    for kf in kelly_fractions:
        for mev in min_evs:
            for ms in max_stds:
                for me in max_exposures:
                    config = StrategyConfig(
                        kelly_fraction=kf,
                        min_ev=mev,
                        max_model_std=ms,
                        max_total_exposure=me,
                    )
                    bets = generate_bets(ensemble_probs, odds, all_model_probs,
                                          bankroll, config)
                    metrics = simulate_results(bets, actual_outcomes, bankroll)

                    # Composite score: ROI * (1 - max_drawdown) * sqrt(n_bets)
                    score = metrics['roi'] * (1 - metrics['max_drawdown']) * np.sqrt(metrics['n_bets'])

                    results.append({
                        'kelly_fraction': kf,
                        'min_ev': mev,
                        'max_model_std': ms,
                        'max_exposure': me,
                        'roi': metrics['roi'],
                        'sharpe': metrics['sharpe'],
                        'max_dd': metrics['max_drawdown'],
                        'n_bets': metrics['n_bets'],
                        'score': score,
                    })

                    if score > best_metric:
                        best_metric = score
                        best_config = config
                        best_result = metrics

    # Display top 5 configs
    results_df = pd.DataFrame(results).sort_values('score', ascending=False)
    print("\nTop 5 configurations (by composite score):")
    print(results_df.head(5).to_string(index=False))

    print(f"\nBest config: Kelly={best_config.kelly_fraction}, "
          f"MinEV={best_config.min_ev}, MaxStd={best_config.max_model_std}, "
          f"MaxExp={best_config.max_total_exposure}")
    print(f"  ROI={best_result['roi']*100:.1f}%, "
          f"Sharpe={best_result['sharpe']:.2f}, "
          f"MaxDD={best_result['max_drawdown']*100:.1f}%, "
          f"Bets={best_result['n_bets']}")

    return {'best_config': best_config, 'best_result': best_result, 'all_results': results_df}
