"""
Phase 3b: End-to-end pre-match signal extraction pipeline.

Flow:
  1. Scrape Zhibo8 for latest football news
  2. For a given match, find relevant articles
  3. DeepSeek extracts: starting XI, injuries, formation, key missing players
  4. Compute 6 signals from extracted data using transfermarkt player DB
  5. Return signals ready for PreMatchAdjuster
"""
import sys, os, json, re, ast
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
TM_DIR = DATA_DIR / 'raw' / 'transfermarkt-data'

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}


# ---------------------------------------------------------------------------
# Step 1: Scrape latest news
# ---------------------------------------------------------------------------

def scrape_latest_news(max_articles: int = 200) -> list[dict]:
    """Scrape latest football news from Zhibo8."""
    url = 'https://news.zhibo8.cc/zuqiu/'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        articles = []
        for link in soup.find_all('a'):
            href = link.get('href', '')
            title = link.get_text(strip=True)
            if '/zuqiu/' not in href or '/more' in href:
                continue
            if len(title) < 10:
                continue
            if not href.startswith('http'):
                href = 'https:' + href if href.startswith('//') else f'https://news.zhibo8.cc{href}'
            articles.append({'title': title, 'url': href})

        seen = set()
        unique = []
        for a in articles:
            if a['url'] not in seen:
                seen.add(a['url'])
                unique.append(a)
        return unique[:max_articles]
    except Exception as e:
        print(f"  Scrape error: {e}")
        return []


# ---------------------------------------------------------------------------
# Step 2: Fetch article content
# ---------------------------------------------------------------------------

def fetch_article(url: str) -> str:
    """Fetch full text of a Zhibo8 article."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        el = soup.select_one('div.content') or soup.select_one('article')
        return el.get_text(separator='\n', strip=True) if el else ''
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Step 3: Find relevant articles for a match
# ---------------------------------------------------------------------------

TEAM_NAME_ZH = {
    'Argentina': '阿根廷', 'Brazil': '巴西', 'France': '法国',
    'Germany': '德国', 'England': '英格兰', 'Spain': '西班牙',
    'Italy': '意大利', 'Netherlands': '荷兰', 'Portugal': '葡萄牙',
    'Belgium': '比利时', 'Croatia': '克罗地亚', 'Uruguay': '乌拉圭',
    'Colombia': '哥伦比亚', 'Mexico': '墨西哥', 'United States': '美国',
    'Japan': '日本', 'South Korea': '韩国', 'Iran': '伊朗',
    'Morocco': '摩洛哥', 'Senegal': '塞内加尔', 'Nigeria': '尼日利亚',
    'Egypt': '埃及', 'Cameroon': '喀麦隆', 'Ghana': '加纳',
    'Australia': '澳大利亚', 'Saudi Arabia': '沙特阿拉伯', 'Qatar': '卡塔尔',
    'Canada': '加拿大', 'Denmark': '丹麦', 'Switzerland': '瑞士',
    'Sweden': '瑞典', 'Poland': '波兰', 'Serbia': '塞尔维亚',
    'Wales': '威尔士', 'Ukraine': '乌克兰', 'Turkey': '土耳其',
    'Austria': '奥地利', 'Czech Republic': '捷克', 'Norway': '挪威',
    'Ecuador': '厄瓜多尔', 'Peru': '秘鲁', 'Chile': '智利',
    'Paraguay': '巴拉圭', 'Costa Rica': '哥斯达黎加', 'Tunisia': '突尼斯',
    'Algeria': '阿尔及利亚', 'South Africa': '南非',
}


def find_match_articles(home_team: str, away_team: str,
                          all_articles: list[dict]) -> list[dict]:
    """Filter articles relevant to a specific match."""
    home_zh = TEAM_NAME_ZH.get(home_team, home_team)
    away_zh = TEAM_NAME_ZH.get(away_team, away_team)

    match_keywords = [home_zh, away_zh, '首发', '阵容', '名单', '伤病',
                      '缺席', '缺阵', '伤停', '预测', '前瞻', '前瞻']
    relevant = []
    for a in all_articles:
        score = sum(1 for kw in match_keywords if kw in a['title'])
        if score >= 1:
            a['relevance'] = score
            relevant.append(a)

    relevant.sort(key=lambda x: x['relevance'], reverse=True)
    return relevant[:10]


# ---------------------------------------------------------------------------
# Step 4: LLM extraction
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """Extract the pre-match facts explicitly reported in the articles for {home} vs {away}. Return only JSON using the keys below. Use null or an empty array for missing facts. Preserve the names printed in the source. Starting XI means the expected starters. Missing players include confirmed injuries and suspensions. Injury entries have player, type and severity fields. Use mild, medium or severe. Returns are key players returning from injury.

{{
  "home_formation": null,
  "away_formation": null,
  "home_starting_xi": [],
  "away_starting_xi": [],
  "home_missing_players": [],
  "away_missing_players": [],
  "home_injuries": [],
  "away_injuries": [],
  "home_returns": [],
  "away_returns": [],
  "match_context": null
}}

Articles:
{articles}"""


def extract_with_deepseek(articles: list[dict], home_team: str,
                            away_team: str) -> dict:
    """Use DeepSeek to extract structured pre-match info from news articles."""
    if not DEEPSEEK_KEY:
        print("  No DeepSeek key. Using empty result.")
        return _empty_result()

    # Combine article texts (up to 6000 chars)
    texts = []
    for a in articles[:8]:
        text = a.get('text', '') or fetch_article(a['url'])
        if text:
            texts.append(f"【{a['title']}】\n{text[:800]}")

    if not texts:
        print("  No article texts available.")
        return _empty_result()

    combined = "\n\n---\n\n".join(texts)
    prompt = EXTRACTION_PROMPT.format(home=home_team, away=away_team,
                                       articles=combined[:5000])

    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {DEEPSEEK_KEY}',
                     'Content-Type': 'application/json'},
            json={'model': 'deepseek-chat',
                  'messages': [{'role': 'user', 'content': prompt}],
                  'temperature': 0.0, 'max_tokens': 1500,
                  'response_format': {'type': 'json_object'}},
            timeout=30,
        )
        resp.raise_for_status()
        result = json.loads(resp.json()['choices'][0]['message']['content'])
        return result
    except Exception as e:
        print(f"  DeepSeek error: {e}")
        return _empty_result()


def _empty_result() -> dict:
    return {
        'home_formation': None, 'away_formation': None,
        'home_starting_xi': [], 'away_starting_xi': [],
        'home_missing_players': [], 'away_missing_players': [],
        'home_injuries': [], 'away_injuries': [],
        'home_returns': [], 'away_returns': [],
        'match_context': None,
    }


# ---------------------------------------------------------------------------
# Step 5: Compute signals from extracted data
# ---------------------------------------------------------------------------

def compute_signals(llm_result: dict, home_team: str, away_team: str) -> dict:
    """Convert LLM-extracted data into PreMatchAdjuster signal values."""
    signals = {}
    tm_players = None

    try:
        tm_players = pd.read_csv(TM_DIR / 'players.csv')
    except Exception:
        pass

    def team_signals(starting_xi, missing, injuries, team_name, prefix):
        n_xi = len(starting_xi)
        n_missing = len(missing)

        # #46: Key player missing (simplified: count of missing starters)
        if n_xi > 0:
            signals[f'{prefix}_key_missing'] = n_missing / max(1, n_xi + n_missing)
        else:
            signals[f'{prefix}_key_missing'] = np.nan

        # #53: Injury severity
        if injuries:
            sev_map = {'轻': 0.25, '中': 0.5, '重': 1.0,
                       'mild': 0.25, 'medium': 0.5, 'severe': 1.0}
            severity = sum(sev_map.get(i.get('severity', '中'), 0.5)
                           for i in injuries)
            signals[f'{prefix}_injury_severity'] = min(1.0, severity / max(1, len(injuries)))
        else:
            signals[f'{prefix}_injury_severity'] = 0.0

        # #49: Starting XI strength (from transfermarkt)
        if tm_players is not None and n_xi > 0:
            country_players = tm_players[
                tm_players['country_of_citizenship'].str.lower() == team_name.lower()
            ]
            if len(country_players) > 0:
                # Sum market values of matched starting XI players
                xi_value = 0
                for name in starting_xi:
                    matched = country_players[
                        country_players['name'].str.contains(
                            name[:4], case=False, na=False)
                    ]
                    if not matched.empty:
                        xi_value += matched['market_value_in_eur'].fillna(0).iloc[0]

                best11_value = country_players.nlargest(11, 'market_value_in_eur')[
                    'market_value_in_eur'].fillna(0).sum()
                if best11_value > 0:
                    signals[f'{prefix}_xi_strength'] = xi_value / best11_value
                else:
                    signals[f'{prefix}_xi_strength'] = np.nan
            else:
                signals[f'{prefix}_xi_strength'] = np.nan
        else:
            signals[f'{prefix}_xi_strength'] = np.nan

        # #56: Debutants (players not in transfermarkt DB)
        if tm_players is not None and n_xi > 0:
            newbies = 0
            for name in starting_xi:
                matched = tm_players[
                    (tm_players['country_of_citizenship'].str.lower() == team_name.lower()) &
                    (tm_players['name'].str.contains(name[:4], case=False, na=False))
                ]
                if matched.empty:
                    newbies += 1
            signals[f'{prefix}_debutants'] = newbies
        else:
            signals[f'{prefix}_debutants'] = np.nan

        # #50: Formation change (just record formation)
        signals[f'{prefix}_formation'] = 1 if n_xi > 0 else 0

    team_signals(llm_result.get('home_starting_xi', []),
                 llm_result.get('home_missing_players', []),
                 llm_result.get('home_injuries', []),
                 home_team, 'home')
    team_signals(llm_result.get('away_starting_xi', []),
                 llm_result.get('away_missing_players', []),
                 llm_result.get('away_injuries', []),
                 away_team, 'away')

    return signals


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_for_match(home_team: str, away_team: str) -> dict:
    """Run the full Phase 3b pipeline for one match.

    Returns: {'llm_result': {...}, 'signals': {...}}
    """
    print(f"\n{'='*50}")
    print(f"Phase 3b: {home_team} vs {away_team}")
    print(f"{'='*50}")

    # 1. Scrape news
    print("\n[1/3] Scraping latest news...")
    all_articles = scrape_latest_news()

    # 2. Find relevant
    print(f"[2/3] Finding relevant articles among {len(all_articles)}...")
    relevant = find_match_articles(home_team, away_team, all_articles)
    print(f"  Found {len(relevant)} relevant articles")

    # Fetch content for top articles
    for a in relevant[:5]:
        a['text'] = fetch_article(a['url'])
        print(f"  [{a['relevance']}] {a['title'][:70]} ({len(a['text'])} chars)")

    # 3. LLM extraction
    print(f"\n[3/3] DeepSeek extraction...")
    llm_result = extract_with_deepseek(relevant, home_team, away_team)

    print(f"  Formation: {llm_result.get('home_formation')} vs {llm_result.get('away_formation')}")
    print(f"  Home XI: {len(llm_result.get('home_starting_xi', []))} players")
    print(f"  Away XI: {len(llm_result.get('away_starting_xi', []))} players")
    print(f"  Home missing: {llm_result.get('home_missing_players', [])}")
    print(f"  Away missing: {llm_result.get('away_missing_players', [])}")
    print(f"  Context: {llm_result.get('match_context', 'N/A')}")

    # 4. Compute signals
    signals = compute_signals(llm_result, home_team, away_team)
    print(f"\n  Computed signals:")
    for k, v in signals.items():
        if not isinstance(v, float) or not np.isnan(v):
            print(f"    {k}: {v}")

    return {'llm_result': llm_result, 'signals': signals}


if __name__ == '__main__':
    # Test with a WC 2026 match
    result = extract_for_match('Argentina', 'Brazil')
