"""
Chinese-English team name mapping for 500.com odds integration.
Maps Chinese team names (from 500.com) to English names (from results.csv).
"""
import pandas as pd
from pathlib import Path

# Chinese → English team name mapping for 2026 World Cup teams
CN_TO_EN = {
    # Group A
    '加拿大': 'Canada',
    '墨西哥': 'Mexico',
    '捷克': 'Czechia',
    '苏格兰': 'Scotland',
    '巴西': 'Brazil',
    # Group B
    '摩洛哥': 'Morocco',
    '西班牙': 'Spain',
    '乌拉圭': 'Uruguay',
    '挪威': 'Norway',
    '法国': 'France',
    # Group C
    '南非': 'South Africa',
    '韩国': 'South Korea',
    '瑞士': 'Switzerland',
    '哥伦比亚': 'Colombia',
    '葡萄牙': 'Portugal',
    # Group D
    '日本': 'Japan',
    '瑞典': 'Sweden',
    '土耳其': 'Turkey',
    '美国': 'USA',
    '海地': 'Haiti',
    # Group E
    '巴拉圭': 'Paraguay',
    '澳大利亚': 'Australia',
    '澳大利': 'Australia',  # truncated variant
    '厄瓜多尔': 'Ecuador',
    '德国': 'Germany',
    # Group F
    '突尼斯': 'Tunisia',
    '荷兰': 'Netherlands',
    '库拉索': 'Curaçao',
    '科特迪瓦': 'Ivory Coast',
    # Group G
    '埃及': 'Egypt',
    '伊朗': 'Iran',
    '伊拉克': 'Iraq',
    '塞内加尔': 'Senegal',
    '佛得角': 'Cape Verde',
    '沙特阿拉伯': 'Saudi Arabia',
    # Group H
    '新西兰': 'New Zealand',
    '比利时': 'Belgium',
    '克罗地亚': 'Croatia',
    '加纳': 'Ghana',
    # Additional teams (from page titles)
    '苏格兰': 'Scotland',
    # Others
    '英格兰': 'England',
    '阿根廷': 'Argentina',
    '意大利': 'Italy',
    '俄罗斯': 'Russia',
    '丹麦': 'Denmark',
    '波兰': 'Poland',
    '塞尔维亚': 'Serbia',
    '喀麦隆': 'Cameroon',
    '哥斯达黎加': 'Costa Rica',
    '卡塔尔': 'Qatar',
    '巴拿马': 'Panama',
    '约旦': 'Jordan',
    '阿尔及利亚': 'Algeria',
    '奥地利': 'Austria',
    '波黑': 'Bosnia and Herzegovina',
    '乌兹别克斯坦': 'Uzbekistan',
    '刚果民主共和国': 'DR Congo',
    'DR Congo': 'DR Congo',
}

EN_TO_CN = {v: k for k, v in CN_TO_EN.items()}


def load_500_odds(filepath: str = None) -> pd.DataFrame:
    """Load odds scraped from 500.com and map team names to English."""
    if filepath is None:
        filepath = Path(__file__).parent.parent.parent / 'data' / 'raw' / 'wc2026_odds_500.csv'

    df = pd.read_csv(filepath)

    # Map team names
    df['home_team_en'] = df['home_team'].map(CN_TO_EN)
    df['away_team_en'] = df['away_team'].map(CN_TO_EN)

    n_mapped = df['home_team_en'].notna().sum()
    n_total = len(df)
    print(f"Team name mapping: {n_mapped}/{n_total} matches mapped to English")

    unmapped = df[df['home_team_en'].isna()]
    if len(unmapped) > 0:
        print(f"  Unmapped teams: {unmapped[['home_team', 'away_team']].values.tolist()}")

    return df


def merge_500_odds_with_matches(matches_df: pd.DataFrame,
                                 odds_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Merge 500.com odds into the matches DataFrame by team name matching.

    Strategy:
    1. Exact match on English team names
    2. Fuzzy match on Chinese team names as fallback
    """
    if odds_df is None:
        odds_df = load_500_odds()

    matches = matches_df.copy()

    # Build lookup: (home_en, away_en) -> odds
    odds_lookup = {}
    for _, row in odds_df.iterrows():
        if pd.notna(row['home_team_en']) and pd.notna(row['away_team_en']):
            key = (row['home_team_en'].lower().strip(),
                   row['away_team_en'].lower().strip())
            odds_lookup[key] = {
                'odds_home': row['odds_home'],
                'odds_draw': row['odds_draw'],
                'odds_away': row['odds_away'],
            }

    # Match
    matched = 0
    odds_h, odds_d, odds_a = [], [], []
    for _, match in matches.iterrows():
        home = match['home_team'].lower().strip()
        away = match['away_team'].lower().strip()
        key = (home, away)

        if key in odds_lookup:
            odds_h.append(odds_lookup[key]['odds_home'])
            odds_d.append(odds_lookup[key]['odds_draw'])
            odds_a.append(odds_lookup[key]['odds_away'])
            matched += 1
        else:
            odds_h.append(None)
            odds_d.append(None)
            odds_a.append(None)

    matches['odds_home'] = odds_h
    matches['odds_draw'] = odds_d
    matches['odds_away'] = odds_a

    print(f"Odds matched: {matched}/{len(matches)} matches")
    return matches
