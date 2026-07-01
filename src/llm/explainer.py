"""
Phase 5: LLM prediction explainer.

Generates natural language (Chinese) match predictions combining:
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


EXPLANATION_PROMPT = """你是一名资深足球分析师。基于以下结构化数据，生成一场比赛的预测分析。

## 比赛信息
- 赛事：{tournament}
- 对阵：{home_team} vs {away_team}
- 日期：{match_date}
- 场地：{venue}

## 模型预测
- 主胜({home_team})：{model_h:.1%}
- 平局：{model_d:.1%}
- 客胜({away_team})：{model_a:.1%}
- 模型倾向：{model_prediction}

## 市场赔率
- 主胜赔率：{odds_h}
- 平局赔率：{odds_d}
- 客胜赔率：{odds_a}
- 市场隐含概率：主{mkt_h:.1%} / 平{mkt_d:.1%} / 客{mkt_a:.1%}

## 关键影响因素
{top_features}

## 赛前信息
{pre_match_info}

## 球队近况
- {home_team}：{home_form}
- {away_team}：{away_form}

## 历史交锋
{h2h_info}

## 投注建议
{betting_advice}

---

请用中文生成一份简洁的预测分析（300-400字），包含：
1. 最终预测（明确胜负平，附置信度）
2. 3-5条核心理由，每条必须引用上方具体数据
3. 与市场赔率的分歧说明（如有）
4. 投注意见（仅当Edge>10%时推荐）

注意：
- 不要编造信息，只使用上方提供的数据
- 如果数据缺失，直接说"数据暂缺"
- 温度=0，直接给出结论，不需要犹豫
"""


def build_top_features(feature_importance: list[tuple],
                        feature_descriptions: dict) -> str:
    """
    Format top feature contributions into readable text.

    Args:
        feature_importance: [(name, value, impact), ...]
        feature_descriptions: {name: description}
    """
    if not feature_importance:
        return "暂无特征贡献数据"

    lines = []
    for name, value, impact in feature_importance[:5]:
        desc = feature_descriptions.get(name, name)
        direction = "有利于主队" if impact > 0 else "有利于客队" if impact < 0 else "影响中性"
        lines.append(f"- {desc}：{direction}（值={value:.2f}）")
    return "\n".join(lines) if lines else "暂无特征贡献数据"


def format_betting_advice(edge: float, outcome: str, odds: float,
                           model_prob: float, mkt_prob: float) -> str:
    """Generate betting recommendation text."""
    if edge >= 0.10:
        return (
            f"【推荐投注】{outcome}，赔率{odds:.2f}，模型概率{model_prob:.1%}，"
            f"市场概率{mkt_prob:.1%}，Edge={edge:+.1%}。"
            f"建议使用1/4 Kelly准则，单注不超过本金5%。"
        )
    elif edge >= 0.05:
        return (
            f"【边缘机会】{outcome}存在一定价值（Edge={edge:+.1%}），"
            f"但未达到10%门槛，建议观望。"
        )
    else:
        return "【不建议投注】模型与市场定价基本一致，无明显套利空间。"


def generate_explanation(
    home_team: str,
    away_team: str,
    model_probs: dict,        # {'home': float, 'draw': float, 'away': float}
    odds: dict,               # {'home': float, 'draw': float, 'away': float}
    tournament: str = "FIFA World Cup 2026",
    match_date: str = "",
    venue: str = "中立场地",
    top_features: list[tuple] = None,
    feature_descriptions: dict = None,
    home_form: str = "数据暂缺",
    away_form: str = "数据暂缺",
    h2h_info: str = "数据暂缺",
    pre_match_info: str = "暂无赛前阵容/伤病信息",
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
    pred_label = ['主胜', '平局', '客胜'][pred_idx]
    pred_team = home_team if pred_idx == 0 else away_team if pred_idx == 2 else '平局'

    # Edge
    edge = mp - mkt_probs
    best_edge = np.max(edge)
    best_outcome = ['主胜', '平局', '客胜'][np.argmax(edge)]
    best_odds = [odds['home'], odds['draw'], odds['away']][np.argmax(edge)]

    # Feature text
    ft_text = build_top_features(top_features or [], feature_descriptions or {})

    # Betting
    bet_text = format_betting_advice(best_edge, best_outcome, best_odds,
                                     mp[np.argmax(edge)], mkt_probs[np.argmax(edge)])

    # Prediction summary
    prediction_text = f"{pred_label}（置信度：{mp[pred_idx]:.1%}）"

    if not use_llm or not DEEPSEEK_KEY:
        # Fallback: structured text
        return f"""## {home_team} vs {away_team}

**预测**：{prediction_text}

**概率分布**：
- {home_team}赢：{model_probs['home']:.1%}（市场{mkt_probs[0]:.1%}）
- 平局：{model_probs['draw']:.1%}（市场{mkt_probs[1]:.1%}）
- {away_team}赢：{model_probs['away']:.1%}（市场{mkt_probs[2]:.1%}）

**关键因素**：
{ft_text}

**交锋**：{h2h_info}
**{home_team}近况**：{home_form}
**{away_team}近况**：{away_form}
**赛前**：{pre_match_info}

{bet_text}
"""

    # DeepSeek LLM generation
    prompt = EXPLANATION_PROMPT.format(
        tournament=tournament,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date or "近期",
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
    'elo_diff': 'ELO实力差距',
    'elo_home': '主队ELO评分',
    'elo_away': '客队ELO评分',
    'h2h_goal_diff': '历史交锋场均净胜球',
    'h2h_comp_win_home': '正式比赛交锋主队胜率',
    'h2h_matches': '历史交锋场次',
    'h2h_same_venue_pct': '同类型场地交锋占比',
    'neutral_venue': '中立场地',
    'home_wga': '主队加权场均失球',
    'away_wga': '客队加权场均失球',
    'home_wgd': '主队加权场均净胜球',
    'away_wgd': '客队加权场均净胜球',
    'home_form_ga_avg': '主队近10场场均失球',
    'away_form_ga_avg': '客队近10场场均失球',
    'home_tournament_form': '主队同赛事历史胜率',
    'away_tournament_form': '客队同赛事历史胜率',
    'fifa_points_home': '主队FIFA积分',
    'fifa_points_away': '客队FIFA积分',
    'is_wc_qualifier': '世界杯预选赛',
    'is_friendly': '友谊赛',
    'is_continental': '洲际赛事',
    'coach_win_rate_home': '主队教练胜率',
    'coach_win_rate_away': '客队教练胜率',
    'event_home_xg': '主队近期场均xG',
    'event_away_xg': '客队近期场均xG',
    'event_home_ppda': '主队压迫强度(PPDA)',
    'event_away_ppda': '客队压迫强度(PPDA)',
    'event_home_def_line_height': '主队防线高度',
    'event_away_def_line_height': '客队防线高度',
    'event_home_pass_completion': '主队传球成功率',
    'event_away_pass_completion': '客队传球成功率',
    'event_home_direct_speed': '主队直传速度',
    'event_away_direct_speed': '客队直传速度',
    'event_home_progressive_passes': '主队推进传球数',
    'event_away_progressive_passes': '客队推进传球数',
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
