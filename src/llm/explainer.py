"""
Phase 5: LLM prediction explainer.

Generates natural language (English) match predictions combining:
  - Ensemble model probabilities
  - Top SHAP feature contributions
  - Market odds comparison
  - Betting recommendation (Edge >= 10% rule)
"""
import os, json, requests
import numpy as np
import pandas as pd

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_URL = 'https://api.deepseek.com/v1/chat/completions'


EXPLANATION_PROMPT = """Write a 300–400 word pre-match football forecast in English using only the supplied evidence.

Fixture: {tournament}, {home_team} vs {away_team}, {match_date}, {venue}.
Model probabilities: home {model_h:.1%}, draw {model_d:.1%}, away {model_a:.1%}.
Model prediction: {model_prediction}.
Decimal odds: home {odds_h}, draw {odds_d}, away {odds_a}.
Market probabilities: home {mkt_h:.1%}, draw {mkt_d:.1%}, away {mkt_a:.1%}.
Feature contributions:
{top_features}
Pre-match information: {pre_match_info}
Recent form: {home_team}: {home_form}; {away_team}: {away_form}.
Head-to-head record: {h2h_info}
Model recommendation: {betting_advice}

State the predicted outcome and confidence. Cite three to five reasons using the supplied data.
Explain any differences from the market and report whether the model's edge meets its 10% threshold.
Flag missing data explicitly."""


def build_top_features(feature_importance: list[tuple],
                        feature_descriptions: dict) -> str:
    """
    Format top feature contributions into readable text.

    Args:
        feature_importance: [(name, value, impact), ...]
        feature_descriptions: {name: description}
    """
    if not feature_importance:
        return "Feature contributions unavailable"

    lines = []
    for name, value, impact in feature_importance[:5]:
        desc = feature_descriptions.get(name, name)
        direction = "favors the home team" if impact > 0 else "favors the away team" if impact < 0 else "neutral"
        lines.append(f"- {desc}: {direction} (value={value:.2f})")
    return "\n".join(lines) if lines else "Feature contributions unavailable"


def format_betting_advice(edge: float, outcome: str, odds: float,
                           model_prob: float, mkt_prob: float) -> str:
    """Generate betting recommendation text."""
    if edge >= 0.10:
        return (
            f"Model recommendation: {outcome}, odds {odds:.2f}, model probability {model_prob:.1%}, "
            f"Market probability {mkt_prob:.1%}, edge={edge:+.1%}. "
            f"The model uses quarter-Kelly sizing capped at 5% of bankroll."
        )
    elif edge >= 0.05:
        return (
            f"Marginal edge: {outcome} may offer value  (Edge={edge:+.1%}), "
            f"The edge is below the 10% recommendation threshold."
        )
    else:
        return "No bet. The model finds no material difference from market pricing."


def generate_explanation(
    home_team: str,
    away_team: str,
    model_probs: dict,        # {'home': float, 'draw': float, 'away': float}
    odds: dict,               # {'home': float, 'draw': float, 'away': float}
    tournament: str = "FIFA World Cup 2026",
    match_date: str = "",
    venue: str = "Neutral venue",
    top_features: list[tuple] = None,
    feature_descriptions: dict = None,
    home_form: str = "Data not available",
    away_form: str = "Data not available",
    h2h_info: str = "Data not available",
    pre_match_info: str = "Pre-match and injury information unavailable",
    use_llm: bool = True,
) -> str:
    """
    Generate a full prediction explanation.

    If use_llm=True and DEEPSEEK_KEY is set, uses DeepSeek for natural language.
    Otherwise returns a structured text summary.
    """
    # Market implied probs
    mkt_raw = np.array([1 / odds['home'], 1 / odds['draw'], 1 / odds['away']])
    mkt_probs = mkt_raw / mkt_raw.sum()

    # Determine model prediction
    mp = np.array([model_probs['home'], model_probs['draw'], model_probs['away']])
    pred_idx = np.argmax(mp)
    pred_label = ['home win', 'draw', 'away win'][pred_idx]
    pred_team = home_team if pred_idx == 0 else away_team if pred_idx == 2 else 'draw'

    # Edge
    edge = mp - mkt_probs
    best_edge = np.max(edge)
    best_outcome = ['home win', 'draw', 'away win'][np.argmax(edge)]
    best_odds = [odds['home'], odds['draw'], odds['away']][np.argmax(edge)]

    # Feature text
    ft_text = build_top_features(top_features or [], feature_descriptions or {})

    # Betting
    bet_text = format_betting_advice(best_edge, best_outcome, best_odds,
                                     mp[np.argmax(edge)], mkt_probs[np.argmax(edge)])

    # Prediction summary
    prediction_text = f"{pred_label} (confidence: {mp[pred_idx]:.1%})"

    if not use_llm or not DEEPSEEK_KEY:
        # Fallback: structured text
        return f"""## {home_team} vs {away_team}

**Predictions**: {prediction_text}

**Probability distribution**: 
- {home_team} win: {model_probs['home']:.1%} (market {mkt_probs[0]:.1%})
- draw:{model_probs['draw']:.1%} (market {mkt_probs[1]:.1%})
- {away_team} win: {model_probs['away']:.1%} (market {mkt_probs[2]:.1%})

**Key factors**: 
{ft_text}

**Head-to-head record**: {h2h_info}
**{home_team} recent form**: {home_form}
**{away_team} recent form**: {away_form}
**Pre-match information**: {pre_match_info}

{bet_text}
"""

    # DeepSeek LLM generation
    prompt = EXPLANATION_PROMPT.format(
        tournament=tournament,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date or "Recent",
        venue=venue,
        model_h=model_probs['home'],
        model_d=model_probs['draw'],
        model_a=model_probs['away'],
        model_prediction=prediction_text,
        odds_h=odds['home'],
        odds_d=odds['draw'],
        odds_a=odds['away'],
        mkt_h=mkt_probs[0],
        mkt_d=mkt_probs[1],
        mkt_a=mkt_probs[2],
        top_features=ft_text,
        pre_match_info=pre_match_info,
        home_form=home_form,
        away_form=away_form,
        h2h_info=h2h_info,
        betting_advice=bet_text,
    )

    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={
                'Authorization': f'Bearer {DEEPSEEK_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
                'max_tokens': 800,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"DeepSeek error: {e}, using fallback")
        return generate_explanation(
            home_team, away_team, model_probs, odds,
            tournament, match_date, venue, top_features,
            feature_descriptions, home_form, away_form,
            h2h_info, pre_match_info, use_llm=False,
        )


# ---------------------------------------------------------------------------
# Feature descriptions for readable explanations
# ---------------------------------------------------------------------------

FEATURE_DESCRIPTIONS = {
    'elo_diff': 'Elo rating difference',
    'elo_home': 'Home Elo rating',
    'elo_away': 'Away Elo rating',
    'h2h_goal_diff': 'Mean head-to-head goal difference',
    'h2h_comp_win_home': 'Home win rate in competitive head-to-head matches',
    'h2h_matches': 'Number of head-to-head matches',
    'h2h_same_venue_pct': 'Share of meetings at the same venue type',
    'neutral_venue': 'Neutral venue',
    'home_wga': 'Home weighted goals conceded per match',
    'away_wga': 'Away weighted goals conceded per match',
    'home_wgd': 'Home weighted goal difference per match',
    'away_wgd': 'Away weighted goal difference per match',
    'home_form_ga_avg': 'Home goals conceded per match (last 10)',
    'away_form_ga_avg': 'Away goals conceded per match (last 10)',
    'home_tournament_form': 'Home historical win rate in this competition',
    'away_tournament_form': 'Away historical win rate in this competition',
    'fifa_points_home': 'Home FIFA ranking points',
    'fifa_points_away': 'Away FIFA ranking points',
    'is_wc_qualifier': 'World Cup qualifier',
    'is_friendly': 'Friendly',
    'is_continental': 'Continental competition',
    'coach_win_rate_home': 'Home coach win rate',
    'coach_win_rate_away': 'Away coach win rate',
    'event_home_xg': 'Home recent expected goals per match',
    'event_away_xg': 'Away recent expected goals per match',
    'event_home_ppda': 'Home pressing intensity (PPDA)',
    'event_away_ppda': 'Away pressing intensity (PPDA)',
    'event_home_def_line_height': 'Home defensive line height',
    'event_away_def_line_height': 'Away defensive line height',
    'event_home_pass_completion': 'Home pass completion rate',
    'event_away_pass_completion': 'Away pass completion rate',
    'event_home_direct_speed': 'Home direct attacking speed',
    'event_away_direct_speed': 'Away direct attacking speed',
    'event_home_progressive_passes': 'Home progressive passes',
    'event_away_progressive_passes': 'Away progressive passes',
}


if __name__ == '__main__':
    # Quick test
    explanation = generate_explanation(
        home_team='Czechia',
        away_team='Mexico',
        model_probs={'home': 0.621, 'draw': 0.228, 'away': 0.151},
        odds={'home': 3.65, 'draw': 3.50, 'away': 2.01},
        tournament='FIFA World Cup 2026 Group Stage',
        match_date='2026-06-24',
        use_llm=True,
    )
    print(explanation)
