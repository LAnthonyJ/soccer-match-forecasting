"""
Kelly Criterion for optimal bet sizing.
Implements fractional Kelly with risk management constraints.
"""
import numpy as np


def kelly_fraction(p_model: float, odds_decimal: float,
                   fraction: float = 0.25) -> float:
    """
    Calculate Kelly fraction for a single bet.

    f* = (p * b - q) / b
    where b = odds - 1 (net decimal odds)

    Args:
        p_model: Model-estimated probability of winning
        odds_decimal: Decimal odds offered
        fraction: Fraction of full Kelly to use (0.25 = quarter Kelly)

    Returns:
        Fraction of bankroll to bet (0.0 to 1.0, but typically < 0.05)
    """
    if odds_decimal <= 1.0 or p_model <= 0 or p_model >= 1:
        return 0.0

    b = odds_decimal - 1.0  # Net odds
    q = 1.0 - p_model

    # Full Kelly
    f_star = (p_model * b - q) / b

    # Negative edge: don't bet
    if f_star <= 0:
        return 0.0

    # Fractional Kelly
    return f_star * fraction


def calculate_ev(p_model: float, odds_decimal: float) -> float:
    """Calculate expected value as a fraction of stake."""
    return p_model * odds_decimal - 1.0


def calculate_optimal_bets(probs: dict, odds: dict,
                           bankroll: float = 1000.0,
                           edge_threshold: float = 0.02,
                           max_per_bet: float = 0.05,
                           max_total_exposure: float = 0.30) -> dict:
    """
    Calculate optimal bet sizes for a set of matches.

    Args:
        probs: Dict with keys 'home', 'draw', 'away' arrays of shape (n_matches,)
        odds: Dict with keys 'home', 'draw', 'away' arrays of shape (n_matches,)
        bankroll: Current bankroll
        edge_threshold: Minimum EV to place a bet
        max_per_bet: Maximum fraction of bankroll per single bet
        max_total_exposure: Maximum total fraction of bankroll at risk

    Returns:
        Dict with bet recommendations per match
    """
    n_matches = len(probs['home'])
    bets = []

    for i in range(n_matches):
        match_bets = []
        for outcome in ['home', 'draw', 'away']:
            p = probs[outcome][i]
            o = odds[outcome][i]

            if o <= 1.0 or p <= 0:
                continue

            ev = calculate_ev(p, o)
            if ev < edge_threshold:
                continue

            kelly_f = kelly_fraction(p, o, fraction=0.25)
            bet_fraction = min(kelly_f, max_per_bet)
            bet_amount = bankroll * bet_fraction

            match_bets.append({
                'outcome': outcome,
                'probability': p,
                'odds': o,
                'ev': ev,
                'kelly_fraction': kelly_f,
                'bet_fraction': bet_fraction,
                'bet_amount': bet_amount,
            })

        bets.append(match_bets)

    # Enforce total exposure limit
    total_bet = sum(
        sum(b['bet_amount'] for b in match_bets)
        for match_bets in bets
    )
    if total_bet > bankroll * max_total_exposure:
        # Scale down proportionally
        scale = (bankroll * max_total_exposure) / total_bet
        for match_bets in bets:
            for b in match_bets:
                b['bet_amount'] *= scale
                b['bet_fraction'] *= scale

    return bets


def simulate_bet_result(bets: list, actual_outcomes: list,
                        bankroll: float) -> tuple:
    """
    Simulate the result of placed bets.

    Args:
        bets: List of bet recommendations per match
        actual_outcomes: List of actual outcomes ('H', 'D', 'A')
        bankroll: Starting bankroll

    Returns:
        (new_bankroll, profit, bet_details)
    """
    total_staked = 0
    total_returned = 0
    details = []

    for match_bets, actual in zip(bets, actual_outcomes):
        for bet in match_bets:
            total_staked += bet['bet_amount']
            won = (bet['outcome'][0].upper() == actual)
            returned = bet['bet_amount'] * bet['odds'] if won else 0
            total_returned += returned
            details.append({
                **bet,
                'actual': actual,
                'won': won,
                'returned': returned,
            })

    profit = total_returned - total_staked
    new_bankroll = bankroll + profit
    roi = profit / total_staked if total_staked > 0 else 0

    return new_bankroll, profit, roi, details
