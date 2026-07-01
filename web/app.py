"""
Phase 5: Streamlit Web UI — conversational football prediction assistant.

Usage: streamlit run web/app.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import numpy as np
import pickle
from datetime import datetime

from src.data.cleaner import load_and_clean
from src.features.build_features import build_all_features
from src.models.train import predict_elo_baseline, prepare_features, FEATURE_COLS
from src.models.score_models import (
    ScoreMatrix, load_score_models, elo_poisson_score_matrix,
)
from src.llm.explainer import generate_explanation, FEATURE_DESCRIPTIONS

st.set_page_config(
    page_title="Football AI Predictor",
    page_icon="⚽",
    layout="wide",
)

# ---- Load model once ----
@st.cache_resource
def load_model():
    with open('outputs/models/full_model.pkl', 'rb') as f:
        m = pickle.load(f)
    return m

@st.cache_resource
def load_score_models_cached():
    try:
        return load_score_models()
    except Exception:
        return None

# ---- Load features once ----
@st.cache_data
def load_data():
    df = load_and_clean('data/raw/results.csv')
    full_df = build_all_features(df.copy())  # Include unplayed for prediction

    # Event features
    evt = pd.read_csv('data/processed/results_with_event_features.csv')
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
    return full_df


ZH_TO_EN = {
    '阿根廷': 'Argentina', '巴西': 'Brazil', '法国': 'France', '德国': 'Germany',
    '英格兰': 'England', '西班牙': 'Spain', '意大利': 'Italy', '荷兰': 'Netherlands',
    '葡萄牙': 'Portugal', '比利时': 'Belgium', '克罗地亚': 'Croatia', '乌拉圭': 'Uruguay',
    '哥伦比亚': 'Colombia', '墨西哥': 'Mexico', '美国': 'USA', '日本': 'Japan',
    '韩国': 'South Korea', '伊朗': 'Iran', '摩洛哥': 'Morocco', '塞内加尔': 'Senegal',
    '埃及': 'Egypt', '喀麦隆': 'Cameroon', '加纳': 'Ghana', '突尼斯': 'Tunisia',
    '澳大利亚': 'Australia', '沙特': 'Saudi Arabia', '卡塔尔': 'Qatar', '加拿大': 'Canada',
    '丹麦': 'Denmark', '瑞士': 'Switzerland', '瑞典': 'Sweden', '波兰': 'Poland',
    '塞尔维亚': 'Serbia', '土耳其': 'Turkey', '奥地利': 'Austria', '捷克': 'Czechia',
    '挪威': 'Norway', '厄瓜多尔': 'Ecuador', '秘鲁': 'Peru', '智利': 'Chile',
    '巴拉圭': 'Paraguay', '新西兰': 'New Zealand', '南非': 'South Africa',
    '伊拉克': 'Iraq', '佛得角': 'Cape Verde', '海地': 'Haiti', '苏格兰': 'Scotland',
    '波黑': 'Bosnia and Herzegovina', '牙买加': 'Jamaica', '巴拿马': 'Panama',
    '尼日利亚': 'Nigeria', '阿尔及利亚': 'Algeria', '科特迪瓦': 'Ivory Coast',
    '哥斯达黎加': 'Costa Rica', '洪都拉斯': 'Honduras', '俄罗斯': 'Russia',
    '乌克兰': 'Ukraine', '希腊': 'Greece', '爱尔兰': 'Ireland', '罗马尼亚': 'Romania',
    '斯洛伐克': 'Slovakia', '芬兰': 'Finland', '匈牙利': 'Hungary', '冰岛': 'Iceland',
    '北爱尔兰': 'Northern Ireland', '保加利亚': 'Bulgaria', '斯洛文尼亚': 'Slovenia',
    '黑山': 'Montenegro', '阿尔巴尼亚': 'Albania', '格鲁吉亚': 'Georgia',
    '北马其顿': 'North Macedonia', '亚美尼亚': 'Armenia', '白俄罗斯': 'Belarus',
    '卢森堡': 'Luxembourg', '立陶宛': 'Lithuania', '拉脱维亚': 'Latvia', '爱沙尼亚': 'Estonia',
    '摩尔多瓦': 'Moldova', '阿塞拜疆': 'Azerbaijan', '塞浦路斯': 'Cyprus',
    '法罗群岛': 'Faroe Islands', '马耳他': 'Malta', '安道尔': 'Andorra',
    '圣马力诺': 'San Marino', '列支敦士登': 'Liechtenstein', '直布罗陀': 'Gibraltar',
    '委内瑞拉': 'Venezuela', '玻利维亚': 'Bolivia', '库拉索': 'Curaçao',
    '约旦': 'Jordan', '乌兹别克斯坦': 'Uzbekistan', '民主刚果': 'DR Congo',
    '巴拿马': 'Panama', '英格兰': 'England', '克罗地亚': 'Croatia', '加纳': 'Ghana',
    '哥伦比亚': 'Colombia', '葡萄牙': 'Portugal', '阿尔及利亚': 'Algeria',
    '奥地利': 'Austria', '阿根廷': 'Argentina',
    '沙特阿拉伯': 'Saudi Arabia', '阿联酋': 'United Arab Emirates',
}


def swap_home_away_features(df):
    """Swap all home/away directional features for reversed matches."""
    result = df.copy()
    # ELO
    if 'elo_home' in result.columns and 'elo_away' in result.columns:
        result['elo_home'], result['elo_away'] = result['elo_away'].copy(), result['elo_home'].copy()
    if 'elo_diff' in result.columns:
        result['elo_diff'] = -result['elo_diff']
    if 'fifa_points_home' in result.columns and 'fifa_points_away' in result.columns:
        result['fifa_points_home'], result['fifa_points_away'] = result['fifa_points_away'].copy(), result['fifa_points_home'].copy()

    # Swap all home_* / away_* prefixed columns
    home_cols = [c for c in result.columns if c.startswith('home_') and c.replace('home_', 'away_') in result.columns]
    for hc in home_cols:
        ac = hc.replace('home_', 'away_')
        result[hc], result[ac] = result[ac].copy(), result[hc].copy()

    # Negate difference columns
    for c in result.columns:
        if c.endswith('_diff') or c in ['rest_advantage', 'h2h_goal_diff']:
            if c in result.columns:
                result[c] = -result[c]
        # Negate matchup advantage
        if c == 'tactical_matchup_adv':
            result[c] = -result[c]

    # Swap event features
    evt_home = [c for c in result.columns if c.startswith('event_home_')]
    for hc in evt_home:
        ac = hc.replace('event_home_', 'event_away_')
        if ac in result.columns:
            result[hc], result[ac] = result[ac].copy(), result[hc].copy()

    # Swap H2H directional
    for hc, ac in [('h2h_home_win_pct', 'h2h_away_win_pct'),
                    ('h2h_recent_win_home', 'h2h_recent_win_away'),
                    ('h2h_comp_win_home', 'h2h_comp_win_away')]:
        if hc in result.columns and ac in result.columns:
            result[hc], result[ac] = result[ac].copy(), result[hc].copy()

    return result


def compute_score_predictions(home_team, away_team, full_df, match_row, feature_cols, reversed_match=False):
    """Compute score predictions using all available score models."""
    score_models = load_score_models_cached()
    elo_diff = match_row['elo_diff'].values[0] if 'elo_diff' in match_row.columns else 0
    if pd.isna(elo_diff):
        elo_diff = 0
    # If match was found via reverse search, swap all directional features + negate elo_diff
    if reversed_match:
        match_row = swap_home_away_features(match_row)
        elo_diff = -elo_diff

    # WC matches are always neutral venue
    neutral = True

    results = {}

    # ELO baseline
    results['elo'] = elo_poisson_score_matrix(
        elo_diff if not pd.isna(elo_diff) else 0, neutral)

    if score_models:
        dc = score_models.get('dixon_coles')
        ml = score_models.get('ml_poisson')
        xg = score_models.get('xg_poisson')
        ens = score_models.get('score_ensemble')

        # Dixon-Coles
        if dc:
            try:
                results['dixon_coles'] = dc.predict_score_matrix(
                    home_team, away_team, neutral=neutral)
            except Exception:
                results['dixon_coles'] = results['elo']

        # ML-Poisson
        if ml:
            try:
                available = [c for c in feature_cols if c in match_row.columns]
                for c in feature_cols:
                    if c not in match_row.columns:
                        match_row[c] = 0.0
                X = np.nan_to_num(match_row[feature_cols].values, nan=0.0, posinf=0.0, neginf=0.0)
                results['ml_poisson'] = ml.predict_score_matrix(X.flatten())
            except Exception:
                results['ml_poisson'] = results['elo']

        # xG-Poisson
        if xg:
            try:
                if xg.is_available(home_team, away_team):
                    results['xg_poisson'] = xg.predict_score_matrix(home_team, away_team)
                else:
                    results['xg_poisson'] = results['elo']
            except Exception:
                results['xg_poisson'] = results['elo']

        # Ensemble
        if ens and len(ens.model_names_) > 0:
            try:
                ens_input = []
                for name in ens.model_names_:
                    key = name.lower().replace('dixoncoles', 'dixon_coles').replace('mlpoisson', 'ml_poisson').replace('xgpoisson', 'xg_poisson')
                    ens_input.append(results.get(key, results['elo']))
                results['ensemble'] = ens.predict(ens_input)
            except Exception:
                results['ensemble'] = results['ml_poisson'] if 'ml_poisson' in results else results['elo']
        else:
            results['ensemble'] = results.get('ml_poisson', results['elo'])

    return results


def predict_match(home_team, away_team, odds_h, odds_d, odds_a):
    """Run full ensemble prediction for one match."""
    home_team = ZH_TO_EN.get(home_team, home_team)
    away_team = ZH_TO_EN.get(away_team, away_team)
    full_df = load_data()
    m = load_model()
    ensemble = m['ensemble']; xgb_m = m['xgb_models']
    lgb_m = m['lgb_models']; elo_m = m['elo_model']; cols = m['feature_cols']

    reversed_match = False

    # Find match — search all years, most recent first
    match = full_df[(full_df['home_team'] == home_team) &
                    (full_df['away_team'] == away_team)]
    if len(match) > 1:
        match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        match = full_df[(full_df['home_team'] == away_team) &
                        (full_df['away_team'] == home_team)]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
        if not match.empty:
            reversed_match = True
    if match.empty:
        match = full_df[(full_df['home_team'].str.contains(home_team[:4], na=False)) &
                        (full_df['away_team'].str.contains(away_team[:4], na=False))]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
    if match.empty:
        match = full_df[(full_df['home_team'].str.contains(away_team[:4], na=False)) &
                        (full_df['away_team'].str.contains(home_team[:4], na=False))]
        if len(match) > 1:
            match = match.sort_values('date', ascending=False).head(1)
        if not match.empty:
            reversed_match = True

    if match.empty:
        # Fallback: still run Dixon-Coles (no features needed)
        score_models = load_score_models_cached()
        if score_models:
            dc = score_models.get('dixon_coles')
            if dc:
                sm = dc.predict_score_matrix(home_team, away_team, neutral=True)
                hda = sm.match_outcome_probs()
                probs = {'home': hda['home'], 'draw': hda['draw'], 'away': hda['away'],
                         'home_dc': hda['home'], 'draw_dc': hda['draw'], 'away_dc': hda['away']}
                score_results = {'elo': sm, 'dixon_coles': sm, 'xg_poisson': sm,
                                 'ml_poisson': sm, 'ensemble': sm}
                return probs, score_results, None
        return None, None, f"未找到 {home_team} vs {away_team} 的特征数据，且比分模型未加载"

    # Fix all directional features when match was found reversed
    if reversed_match:
        match = swap_home_away_features(match)

    match = predict_elo_baseline(match, elo_m)
    available_cols = [c for c in cols if c in match.columns]
    for c in cols:
        if c not in match.columns:
            match[c] = 0.0
    X = np.nan_to_num(match[cols].values, nan=0.0)

    xgb_p = {o: xgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    lgb_p = {o: lgb_m[o].predict_proba(X)[:, 1][0] for o in ['home', 'draw', 'away']}
    elo_p_arr = {
        'home': np.array([match['prob_h_elo'].values[0]]),
        'draw': np.array([match['prob_d_elo'].values[0]]),
        'away': np.array([match['prob_a_elo'].values[0]]),
    }
    xgb_arr = {o: np.array([xgb_p[o]]) for o in ['home', 'draw', 'away']}
    lgb_arr = {o: np.array([lgb_p[o]]) for o in ['home', 'draw', 'away']}
    ens_probs = ensemble.predict({'elo': elo_p_arr, 'xgb': xgb_arr, 'lgb': lgb_arr})

    probs = {
        'home': ens_probs['home'][0], 'draw': ens_probs['draw'][0], 'away': ens_probs['away'][0],
    }

    # Score predictions (features already swapped above if reversed_match)
    score_results = compute_score_predictions(home_team, away_team, full_df, match, cols,
                                               reversed_match=False)

    # For neutral matches, use DC-derived H/D/A as primary (symmetric, more accurate)
    if score_results and 'dixon_coles' in score_results:
        dc_hda = score_results['dixon_coles'].match_outcome_probs()
        probs['home_dc'] = dc_hda['home']
        probs['draw_dc'] = dc_hda['draw']
        probs['away_dc'] = dc_hda['away']

    return probs, score_results, None


# ---- UI ----
st.title("⚽ Football AI Predictor")
st.caption("National team match prediction engine — powered by Ensemble ML + DeepSeek LLM")

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 预测 Prediction", "⚽ 比分 Score", "📈 数据面板 Dashboard", "📋 历史记录"
])

with tab1:
    st.header("比赛预测")

    col1, col2, col3 = st.columns(3)
    with col1:
        home = st.text_input("主队 Home Team", value="Argentina")
    with col2:
        away = st.text_input("客队 Away Team", value="Brazil")
    with col3:
        tourney = st.selectbox("赛事", ["FIFA World Cup 2026", "UEFA Euro", "Copa América",
                                         "African Cup of Nations", "AFC Asian Cup", "Friendly"])

    st.markdown("---")
    st.subheader("市场赔率 Market Odds（可选）")

    col_o1, col_o2, col_o3 = st.columns(3)
    with col_o1:
        odds_h = st.number_input("主胜赔率", value=2.50, min_value=1.01, step=0.01)
    with col_o2:
        odds_d = st.number_input("平局赔率", value=3.20, min_value=1.01, step=0.01)
    with col_o3:
        odds_a = st.number_input("客胜赔率", value=2.80, min_value=1.01, step=0.01)

    if st.button("🔮 生成预测", type="primary", use_container_width=True):
        with st.spinner("模型计算中..."):
            probs, score_results, error = predict_match(home, away, odds_h, odds_d, odds_a)

        if error:
            st.error(error)
        else:
            # Show probability bars
            st.subheader(f"📊 {home} vs {away}")

            # --- Dixon-Coles H/D/A (primary) ---
            dc_home = probs.get('home_dc', probs['home'])
            dc_draw = probs.get('draw_dc', probs['draw'])
            dc_away = probs.get('away_dc', probs['away'])

            st.markdown("**比分模型 Dixon-Coles（推荐参考）**")
            pcol1, pcol2, pcol3 = st.columns(3)
            outcomes_dc = [
                (f"{home}胜", dc_home),
                ("平局 Draw", dc_draw),
                (f"{away}胜", dc_away),
            ]
            for col, (label, prob) in zip([pcol1, pcol2, pcol3], outcomes_dc):
                with col:
                    st.metric(label, f"{prob:.1%}")
                    st.progress(float(prob))

            st.markdown("---")
            st.caption("HDA Ensemble (XGB+LGB) — 可能含主场偏差")
            pcol4, pcol5, pcol6 = st.columns(3)
            outcomes = [
                (f"{home}胜", probs['home'], odds_h),
                ("平局 Draw", probs['draw'], odds_d),
                (f"{away}胜", probs['away'], odds_a),
            ]

            # Market probs
            mkt_raw = np.array([1 / odds_h, 1 / odds_d, 1 / odds_a])
            mkt_p = mkt_raw / mkt_raw.sum()

            for col, (label, prob, odd) in zip([pcol4, pcol5, pcol6], outcomes):
                with col:
                    st.metric(label, f"{prob:.1%}", f"赔率 {odd:.2f}")
                    st.progress(float(prob), text=f"市场 {mkt_p[outcomes.index((label, prob, odd))]:.1%}")

            # Score summary below H/D/A bars
            if score_results and 'ensemble' in score_results:
                sm = score_results['ensemble']
                e_h, e_a = sm.expected_goals()
                over_p, under_p = sm.over_under_probability(2.5)
                btts_p, _ = sm.btts_probability()
                top3 = sm.top_scorelines(3)

                st.markdown("---")
                scol1, scol2, scol3, scol4 = st.columns(4)
                scol1.metric("预期进球 E[Home]", f"{e_h:.2f}")
                scol2.metric("预期进球 E[Away]", f"{e_a:.2f}")
                scol3.metric("Over 2.5", f"{over_p:.1%}")
                scol4.metric("BTTS", f"{btts_p:.1%}")

                st.caption("最可能比分: " + " | ".join(
                    f"{h}-{a} ({p:.1%})" for h, a, p in top3
                ))

            # Edge indicators (use DC-based probabilities)
            dc_edge = np.array([dc_home, dc_draw, dc_away]) - mkt_p
            best_idx = np.argmax(dc_edge)
            best_label = [f"{home}胜", "平局", f"{away}胜"][best_idx]
            best_edge = dc_edge[best_idx]

            if best_edge >= 0.10:
                st.success(f"🔥 发现投注价值！{best_label} Edge={best_edge:+.1%}（≥10%阈值）")
            elif best_edge >= 0.05:
                st.warning(f"💡 {best_label}存在边缘机会 Edge={best_edge:+.1%}（未达10%阈值）")
            else:
                st.info(f"无明显套利空间（最大Edge={best_edge:+.1%}）")

            # LLM Explanation
            st.markdown("---")
            st.subheader("🤖 AI 分析报告")

            with st.spinner("AI 生成分析中..."):
                explanation = generate_explanation(
                    home_team=home, away_team=away,
                    model_probs={'home': dc_home, 'draw': dc_draw, 'away': dc_away},
                    odds={'home': odds_h, 'draw': odds_d, 'away': odds_a},
                    tournament=tourney,
                    match_date=datetime.now().strftime('%Y-%m-%d'),
                    use_llm=True,
                )
            st.markdown(explanation)

            # Betting recommendation box (DC-based)
            if best_edge >= 0.10:
                odds_best = [odds_h, odds_d, odds_a][best_idx]
                prob_best = [dc_home, dc_draw, dc_away][best_idx]
                mkt_best = mkt_p[best_idx]
                b = odds_best - 1
                kelly = max(0, (prob_best * b - (1 - prob_best)) / b) if b > 0 else 0
                stake_frac = min(kelly * 0.25, 0.05)

                st.markdown("---")
                st.subheader("💰 投注建议 (Dixon-Coles)")
                bet_col1, bet_col2, bet_col3 = st.columns(3)
                bet_col1.metric("推荐投注", f"{best_label}", f"赔率 {odds_best:.2f}")
                bet_col2.metric("建议金额", f"本金 × {stake_frac:.1%}",
                              f"Kelly {kelly:.1%}", delta=f"Edge {best_edge:+.1%}")
                bet_col3.metric("模型 vs 市场", f"{prob_best:.1%}", f"市场 {mkt_best:.1%}")


with tab2:
    st.header("⚽ 比分预测 Score Prediction")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        score_home = st.text_input("主队", value="Argentina", key="score_home")
    with col_s2:
        score_away = st.text_input("客队", value="Brazil", key="score_away")

    if st.button("🔮 生成比分预测", type="primary", use_container_width=True, key="score_btn"):
        with st.spinner("计算比分分布中..."):
            shome = ZH_TO_EN.get(score_home, score_home)
            saway = ZH_TO_EN.get(score_away, score_away)
            full_df = load_data()
            m = load_model()
            cols = m['feature_cols']

            # Find match for features
            score_reversed = False
            smatch = full_df[(full_df['home_team'] == shome) &
                            (full_df['away_team'] == saway)]
            if len(smatch) > 1:
                smatch = smatch.sort_values('date', ascending=False).head(1)
            if smatch.empty:
                smatch = full_df[(full_df['home_team'] == saway) &
                                (full_df['away_team'] == shome)]
                if len(smatch) > 1:
                    smatch = smatch.sort_values('date', ascending=False).head(1)
                if not smatch.empty:
                    score_reversed = True
            if smatch.empty:
                smatch = full_df[(full_df['home_team'].str.contains(shome[:4], na=False)) &
                                (full_df['away_team'].str.contains(saway[:4], na=False))]
                if len(smatch) > 1:
                    smatch = smatch.sort_values('date', ascending=False).head(1)
            if smatch.empty:
                smatch = full_df[(full_df['home_team'].str.contains(saway[:4], na=False)) &
                                (full_df['away_team'].str.contains(shome[:4], na=False))]
                if len(smatch) > 1:
                    smatch = smatch.sort_values('date', ascending=False).head(1)
                if not smatch.empty:
                    score_reversed = True

            if smatch.empty:
                st.error(f"未找到 {shome} vs {saway} 的特征数据")
            else:
                if score_reversed:
                    smatch = swap_home_away_features(smatch)
                score_results = compute_score_predictions(shome, saway, full_df, smatch, cols,
                                                           reversed_match=False)  # already swapped above

                if score_results and 'ensemble' in score_results:
                    sm = score_results['ensemble']
                    from web.score_components import (
                        scoreline_heatmap, top_scorelines_table,
                        over_under_gauge, btts_indicator,
                        score_distribution_chart, expected_goals_display,
                    )

                    st.markdown("---")
                    st.subheader(f"📊 {score_home} vs {score_away}")

                    # Expected goals
                    expected_goals_display(sm, score_home, score_away)

                    st.markdown("---")

                    # Heatmap + top scorelines side by side
                    hcol1, hcol2 = st.columns([1, 1])
                    with hcol1:
                        st.plotly_chart(scoreline_heatmap(sm, score_home, score_away),
                                       use_container_width=True)
                    with hcol2:
                        st.subheader("最可能比分 Top Scorelines")
                        st.dataframe(
                            top_scorelines_table(sm, n=15),
                            use_container_width=True,
                            hide_index=True,
                        )

                    st.markdown("---")

                    # O/U and BTTS
                    gcol1, gcol2, gcol3 = st.columns([1, 1, 1])
                    over_p, under_p = sm.over_under_probability(2.5)
                    btts_p, _ = sm.btts_probability()

                    with gcol1:
                        st.plotly_chart(over_under_gauge(over_p), use_container_width=True)
                        # Edge vs market if odds available
                        try:
                            mkt_over = (1 / 2.0) / (1 / 2.0 + 1 / 1.8)  # placeholder
                            edge_ou = over_p - mkt_over
                            st.metric("O/U Edge vs Market", f"{edge_ou:+.1%}")
                        except Exception:
                            pass
                    with gcol2:
                        st.plotly_chart(btts_indicator(btts_p), use_container_width=True)
                    with gcol3:
                        st.plotly_chart(score_distribution_chart(sm),
                                       use_container_width=True)

                    # Model breakdown
                    st.markdown("---")
                    st.subheader("模型分解 Model Breakdown")

                    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
                    model_labels = {
                        'elo': 'ELO基线',
                        'dixon_coles': 'Dixon-Coles',
                        'ml_poisson': 'ML-Poisson',
                        'xg_poisson': 'xG-Poisson',
                        'ensemble': 'Ensemble',
                    }
                    for idx, (key, label) in enumerate(model_labels.items()):
                        if key in score_results:
                            with [mcol1, mcol2, mcol3, mcol4][idx % 4]:
                                m_sm = score_results[key]
                                e_h, e_a = m_sm.expected_goals()
                                o_p, _ = m_sm.over_under_probability(2.5)
                                st.metric(label, f"{e_h:.1f} - {e_a:.1f}")
                                st.caption(f"O2.5: {o_p:.1%}")
                else:
                    st.warning("比分预测模型未加载。请先运行训练。")

with tab3:
    st.header("📈 模型性能")

    try:
        hist = pd.read_csv('data/predictions/wc2026_predictions.csv')
        done = hist[hist['actual'].notna()]

        if len(done) > 0:
            eps = 1e-15
            model_lls = []; mkt_lls = []
            for _, r in done.iterrows():
                mp = np.array([r['model_H'], r['model_D'], r['model_A']])
                mk = np.array([r['mkt_H'], r['mkt_D'], r['mkt_A']])
                act_idx = {'H': 0, 'D': 1, 'A': 2}[r['actual']]
                model_lls.append(-np.log(np.clip(mp[act_idx], eps, 1)))
                mkt_lls.append(-np.log(np.clip(mk[act_idx], eps, 1)))

            m1, m2, m3 = st.columns(3)
            m1.metric("已完成预测", f"{len(done)} 场")
            m2.metric("模型 Avg LogLoss", f"{np.mean(model_lls):.4f}")
            m3.metric("市场 Avg LogLoss", f"{np.mean(mkt_lls):.4f}",
                      delta=f"{(np.mean(model_lls)-np.mean(mkt_lls)):.4f}")

            # Recent results table
            st.subheader("最近预测记录")
            st.dataframe(
                done[['date', 'match', 'actual', 'model_H', 'mkt_H', 'best_edge']]
                .sort_values('date', ascending=False)
                .head(10),
                use_container_width=True,
            )
    except Exception:
        st.info("暂无历史预测数据")

with tab4:
    st.header("📋 投注记录")
    try:
        hist = pd.read_csv('data/predictions/wc2026_predictions.csv')
        bets = hist[(hist['bet_amount'] > 0) & (hist['actual'].notna())]
        if len(bets) > 0:
            total_stake = bets['bet_amount'].sum()
            total_return = 0
            for _, b_row in bets.iterrows():
                won = b_row['actual'] == b_row['best_edge_outcome']
                if won:
                    total_return += b_row['bet_amount'] * b_row['bet_odds']
            profit = total_return - total_stake

            c1, c2, c3 = st.columns(3)
            c1.metric("总投注", f"{len(bets)} 笔")
            c2.metric("总投入", f"¥{total_stake:.0f}")
            c3.metric("总盈亏", f"¥{profit:+.0f}", delta=f"{profit/total_stake*100:+.1f}%")

            st.dataframe(bets[['date', 'match', 'best_edge_outcome', 'bet_amount',
                                'bet_odds', 'actual']], use_container_width=True)
        else:
            st.info("暂无投注记录。以 Edge ≥ 10% 为标准，符合条件的下注将在此显示。")
    except Exception:
        st.info("暂无投注记录")


st.markdown("---")
st.caption("Powered by XGBoost + LightGBM + ELO Ensemble | DeepSeek LLM | Phase 5")
