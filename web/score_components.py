"""
Phase 7: Reusable Streamlit components for score prediction display.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def scoreline_heatmap(score_matrix, home_team: str = "Home", away_team: str = "Away"):
    """Plotly heatmap of P(home_score, away_score)."""
    z = score_matrix.matrix[:6, :6]  # Focus on 0-5 goals (most relevant)

    fig = go.Figure(data=go.Heatmap(
        z=z.T,  # transpose: rows=away, cols=home
        x=[str(i) for i in range(6)],
        y=[str(i) for i in range(6)],
        colorscale='Blues',
        text=[[f'{z[i][j]:.1%}' for j in range(6)] for i in range(6)],
        texttemplate='%{text}',
        textfont={"size": 11},
        hoverongaps=False,
        colorbar=dict(title="Probability", tickformat='.0%'),
    ))

    fig.update_layout(
        title=f"Score Probability Heatmap: {home_team} vs {away_team}",
        xaxis_title=f"{home_team} Goals",
        yaxis_title=f"{away_team} Goals",
        width=500,
        height=450,
    )
    return fig


def top_scorelines_table(score_matrix, n: int = 15):
    """Streamlit dataframe of top N exact scores with visual bars."""
    top = score_matrix.top_scorelines(n)
    df = pd.DataFrame(top, columns=['主队进球', '客队进球', '概率'])
    df['比分'] = df['主队进球'].astype(str) + ' - ' + df['客队进球'].astype(str)
    df['概率'] = df['概率'].apply(lambda x: f'{x:.2%}')
    df['bar'] = df['概率'].apply(
        lambda x: '█' * int(float(x.strip('%')) / 2)
    )
    return df[['比分', '概率', 'bar']]


def over_under_gauge(over_prob: float, threshold: float = 2.5):
    """Visual gauge showing P(Over) vs P(Under)."""
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=over_prob * 100,
        title={'text': f"P(Over {threshold})"},
        number={'suffix': '%', 'font': {'size': 28}},
        gauge={
            'axis': {'range': [0, 100], 'tickwidth': 1},
            'bar': {'color': "#FF6B6B"},
            'steps': [
                {'range': [0, 40], 'color': "#E8F5E9"},
                {'range': [40, 60], 'color': "#FFF9C4"},
                {'range': [60, 100], 'color': "#FFEBEE"},
            ],
            'threshold': {
                'line': {'color': "black", 'width': 2},
                'thickness': 0.8,
                'value': 50,
            },
        },
    ))
    fig.update_layout(height=250)
    return fig


def btts_indicator(btts_prob: float):
    """Visual gauge for BTTS probability."""
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=btts_prob * 100,
        title={'text': "Both Teams to Score"},
        number={'suffix': '%', 'font': {'size': 28}},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': "#4ECDC4"},
            'steps': [
                {'range': [0, 40], 'color': "#E8F5E9"},
                {'range': [40, 60], 'color': "#FFF9C4"},
                {'range': [60, 100], 'color': "#E0F2F1"},
            ],
        },
    ))
    fig.update_layout(height=250)
    return fig


def edge_vs_market_display(model_prob: float, market_prob: float,
                            label: str = "Over 2.5"):
    """Show model probability vs market-implied probability with edge."""
    edge = model_prob - market_prob

    col1, col2, col3 = st.columns(3)
    col1.metric("模型概率 Model", f"{model_prob:.1%}")
    col2.metric("市场概率 Market", f"{market_prob:.1%}")
    col3.metric("Edge", f"{edge:+.1%}",
                delta=f"{edge:+.1%}" if abs(edge) > 0.03 else None)

    if edge >= 0.10:
        st.success(f"🔥 发现 {label} 投注价值！Edge={edge:+.1%}（≥10%阈值）")
    elif edge >= 0.05:
        st.warning(f"💡 {label} 存在边缘机会 Edge={edge:+.1%}")
    else:
        st.info(f"{label} 无明显套利空间（Edge={edge:+.1%}）")


def score_distribution_chart(score_matrix):
    """Bar chart showing total goals probability distribution."""
    dist = score_matrix.total_goals_distribution()
    max_show = min(len(dist), 12)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[str(i) for i in range(max_show)],
        y=dist[:max_show],
        text=[f'{p:.1%}' for p in dist[:max_show]],
        textposition='outside',
        marker_color='#3498db',
    ))
    fig.update_layout(
        title="Total Goals Distribution",
        xaxis_title="Total Goals",
        yaxis_title="Probability",
        yaxis_tickformat='.0%',
        height=300,
    )
    return fig


def expected_goals_display(score_matrix, home_team: str, away_team: str):
    """Display expected goals comparison."""
    e_h, e_a = score_matrix.expected_goals()

    col1, col2, col3 = st.columns(3)
    col1.metric(f"E[{home_team} Goals]", f"{e_h:.2f}")
    col2.metric(f"E[{away_team} Goals]", f"{e_a:.2f}")
    col3.metric("E[Total Goals]", f"{e_h + e_a:.2f}")
