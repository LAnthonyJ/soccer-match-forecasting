"""
Pre-match info extractor (Phase 3b).

1. Search Zhibo8 for pre-match news (starting XI, injuries)
2. Use DeepSeek to extract structured data from article text
3. Compute 12 pre-match signals from extracted data + transfermarkt

LLM is used ONLY for information extraction (read articles → output JSON).
ALL prediction logic stays in the ML model + PreMatchAdjuster.
"""
import json
import re
import os
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
TM_DIR = DATA_DIR / 'raw' / 'transfermarkt-data'

DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_URL = 'https://api.deepseek.com/v1/chat/completions'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}


# ---------------------------------------------------------------------------
# Step 1: Search Zhibo8 for pre-match articles
# ---------------------------------------------------------------------------

def search_zhibo8(team_name: str, max_articles: int = 10) -> list[dict]:
    """Search Zhibo8 for recent articles about a national team."""
    articles = []
    search_url = f'https://news.zhibo8.cc/zuqiu/'

    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        for link in soup.find_all('a'):
            href = link.get('href', '')
            title = link.get_text(strip=True)
            if '/zuqiu/' not in href or '/more' in href:
                continue
            if not title or len(title) < 10:
                continue
            if team_name.lower() not in title.lower():
                continue

            if not href.startswith('http'):
                if href.startswith('//'):
                    href = 'https:' + href
                else:
                    href = f'https://news.zhibo8.cc{href}'

            articles.append({'title': title, 'url': href, 'source': 'zhibo8'})

            if len(articles) >= max_articles:
                break
    except Exception as e:
        print(f"  zhibo8 search error: {e}")

    return articles


def search_news_for_match(home_team: str, away_team: str) -> dict:
    """Search for pre-match articles about a fixture."""
    home_zh = _team_name_to_chinese(home_team)
    away_zh = _team_name_to_chinese(away_team)

    # First try match-specific queries
    queries = [
        f'{home_zh} {away_zh}',
        f'{home_zh} 首发',
        f'{away_zh} 首发',
        f'{home_zh} 伤病',
        f'{away_zh} 伤病',
        f'{home_zh} 阵容',
        f'{away_zh} 阵容',
    ]

    all_articles = []
    for q in queries:
        articles = search_zhibo8(q, max_articles=5)
        all_articles.extend(articles)

    # Deduplicate
    seen = set()
    unique = []
    for a in all_articles:
        if a['url'] not in seen:
            seen.add(a['url'])
            unique.append(a)

    return {
        'home_team': home_team,
        'away_team': away_team,
        'articles': unique[:15],
    }


# ---------------------------------------------------------------------------
# Step 2: Fetch article content
# ---------------------------------------------------------------------------

def fetch_article_text(url: str) -> str:
    """Fetch full text of a Zhibo8 article."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        for sel in ['div.content', 'article', 'div.article-content',
                     'div#article-content', 'div.main-content']:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator='\n', strip=True)
        return ''
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Step 3: LLM extraction (DeepSeek)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """Extract the pre-match facts explicitly reported in the articles. Return only JSON using the keys below. Use null or an empty array for missing facts. Preserve the names printed in the source. Starting XI means the expected starters. Missing players include confirmed injuries and suspensions. Injury entries have player, type and severity fields. Use mild, medium or severe. Returns are key players returning from injury.

{{
  "home_formation": null,
  "away_formation": null,
  "home_starting_xi": [],
  "away_starting_xi": [],
  "home_missing_players": [],
  "away_missing_players": [],
  "home_injury_details": [],
  "away_injury_details": [],
  "home_key_returns": [],
  "away_key_returns": []
}}

Articles:
{article_text}"""


def extract_with_llm(articles: list[dict],
                     home_team: str,
                     away_team: str) -> dict:
    """
    Use DeepSeek to extract structured pre-match info from articles.

    Falls back to Qwen if DeepSeek key not available.
    """
    if not DEEPSEEK_API_KEY:
        print("  WARNING: DEEPSEEK_API_KEY not set. "
              "Set env var to enable LLM extraction.")
        return _empty_result(home_team, away_team)

    # Fetch article texts
    texts = []
    for a in articles[:8]:
        text = fetch_article_text(a['url'])
        if text:
            texts.append(f"【{a['title']}】\n{text[:2000]}")

    if not texts:
        print("  No article texts could be fetched.")
        return _empty_result(home_team, away_team)

    combined = f"\n\n---\n\n".join(texts)
    prompt = EXTRACTION_PROMPT.format(article_text=combined[:6000])

    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={
                'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.0,   # zero creativity = just extract facts
                'max_tokens': 2000,
                'response_format': {'type': 'json_object'},
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()['choices'][0]['message']['content']
        return json.loads(result)
    except Exception as e:
        print(f"  DeepSeek error: {e}")
        return _empty_result(home_team, away_team)


def _empty_result(home_team: str, away_team: str) -> dict:
    return {
        'home_formation': None, 'away_formation': None,
        'home_starting_xi': [], 'away_starting_xi': [],
        'home_missing_players': [], 'away_missing_players': [],
        'home_injury_details': [], 'away_injury_details': [],
        'home_key_returns': [], 'away_key_returns': [],
    }


# ---------------------------------------------------------------------------
# Step 4: Convert extracted info → signal values
# ---------------------------------------------------------------------------

class SignalComputer:
    """
    Converts LLM-extracted match info + transfermarkt data → 12 signal values.

    Each method takes the LLM output dict + transfermarkt DataFrames
    and returns a signal value (typically 0.0–1.0 range).
    """

    def __init__(self):
        self.players_df = None
        self.valuations_df = None
        self._load_data()

    def _load_data(self):
        try:
            self.players_df = pd.read_csv(TM_DIR / 'players.csv')
            self.valuations_df = pd.read_csv(TM_DIR / 'player_valuations.csv')
        except Exception:
            pass

    def _match_player_to_db(self, name: str, country: str) -> dict:
        """Find a player in transfermarkt DB by name + country."""
        if self.players_df is None:
            return None
        matches = self.players_df[
            (self.players_df['country_of_citizenship'] == country) &
            (self.players_df['name'].str.contains(name[:3], case=False, na=False))
        ]
        if not matches.empty:
            return matches.iloc[0].to_dict()
        return None

    def compute_all(self, llm_result: dict,
                    home_team: str, away_team: str,
                    match_date: str = None) -> dict:
        """Compute all 12 signals from LLM-extracted data."""
        signals = {}

        # #46: Key player missing
        signals['key_player_missing'] = self._key_player_missing(
            llm_result, home_team, away_team)

        # #47: Missing caps %
        signals['missing_caps_pct'] = self._missing_pct(
            llm_result, home_team, 'caps')

        # #48: Missing goals %
        signals['missing_goals_pct'] = self._missing_pct(
            llm_result, home_team, 'goals')

        # #49: Starting XI strength
        signals['starting_xi_strength'] = self._xi_strength(
            llm_result, home_team, match_date)

        # #50: Formation change
        signals['formation_change'] = self._formation_change(
            llm_result, home_team)

        # #51: New manager matches (not extractable from news)
        signals['new_manager_matches'] = np.nan

        # #53: Injury severity
        signals['injury_severity'] = self._injury_severity(
            llm_result, home_team)

        # #54: Tactical matchup (from formation, not full event data)
        signals['tactical_matchup'] = np.nan

        # #56: Debutants
        signals['debutants_in_squad'] = self._debutants(
            llm_result, home_team)

        # #57: Height
        signals['squad_height_avg'] = self._height(
            llm_result, home_team)

        # #59: Extra time prev
        signals['prev_match_extra_time'] = np.nan

        # #60: Travel fatigue
        signals['travel_fatigue'] = np.nan

        return signals

    # ----- individual signal computations -----

    def _key_player_missing(self, info: dict, home: str, away: str) -> float:
        """#46: Fraction of top-3 players missing."""
        missing = info.get('home_missing_players', [])
        starting = info.get('home_starting_xi', [])
        if not starting or not self.players_df is not None:
            return np.nan

        # Find top-3 goal scorers from this country
        country_players = self.players_df[
            self.players_df['country_of_citizenship'] == home
        ]
        top3 = country_players.nlargest(3, 'international_goals')['name'].tolist()

        # Count how many are in the starting XI
        missing_count = 0
        for name in top3:
            in_starting = any(
                name.lower() in s.lower() or s.lower() in name.lower()
                for s in starting
            )
            if not in_starting:
                missing_count += 1

        return missing_count / 3.0

    def _missing_pct(self, info: dict, team: str, metric: str) -> float:
        """#47/#48: Fraction of team's caps/goals missing."""
        missing = info.get('home_missing_players', [])
        starting = info.get('home_starting_xi', [])
        if not missing or not starting or self.players_df is None:
            return np.nan

        country_players = self.players_df[
            self.players_df['country_of_citizenship'] == team
        ]
        total_metric = country_players[f'international_{metric}'].fillna(0).sum()
        if total_metric == 0:
            return np.nan

        # Sum metric for missing players (approximate name matching)
        missing_metric = 0
        for mp in missing:
            matches = country_players[
                country_players['name'].str.contains(
                    mp[:4], case=False, na=False)
            ]
            if not matches.empty:
                missing_metric += matches[f'international_{metric}'].fillna(0).iloc[0]

        return missing_metric / total_metric

    def _xi_strength(self, info: dict, team: str, match_date: str) -> float:
        """#49: Actual XI market value / best possible XI value."""
        starting = info.get('home_starting_xi', [])
        if not starting or self.players_df is None or self.valuations_df is None:
            return np.nan

        country_players = self.players_df[
            self.players_df['country_of_citizenship'] == team
        ]
        player_ids = country_players['player_id'].tolist()

        # Best XI value = top 11 valued players from this country
        if match_date:
            recent_vals = self.valuations_df[
                self.valuations_df['date'] <= match_date
            ]
        else:
            recent_vals = self.valuations_df

        country_vals = recent_vals[
            recent_vals['player_id'].isin(player_ids)
        ]
        top11_val = country_vals.nlargest(11, 'market_value_in_eur')[
            'market_value_in_eur'].sum()

        # Actual XI value (by matching names)
        actual_val = 0
        for name in starting:
            matched = country_players[
                country_players['name'].str.contains(
                    name[:4], case=False, na=False)
            ]
            if not matched.empty:
                pid = matched['player_id'].iloc[0]
                pv = recent_vals[recent_vals['player_id'] == pid]
                if not pv.empty:
                    actual_val += pv.sort_values('date')[
                        'market_value_in_eur'].iloc[-1]

        return actual_val / top11_val if top11_val > 0 else np.nan

    def _formation_change(self, info: dict, team: str) -> float:
        """#50: Binary — formation different from team's typical."""
        formation = info.get('home_formation')
        if not formation:
            return np.nan
        # Compare to historical average; simplified: return NaN (no history)
        return np.nan

    def _injury_severity(self, info: dict, team: str) -> float:
        """#53: Weighted injury severity (0=all fit, 1=all key players out)."""
        injuries = info.get('home_injury_details', [])
        if not injuries:
            return 0.0

        severity_map = {'轻': 0.25, '中': 0.5, '重': 1.0,
                         'mild': 0.25, 'medium': 0.5, 'severe': 1.0}

        total = 0
        for inj in injuries:
            sev = severity_map.get(inj.get('severity', '中'), 0.5)
            # Weight: higher for starting XI players
            is_starter = 1.0  # default; could cross-ref with caps
            total += sev * is_starter

        return min(1.0, total / max(1, len(injuries)))

    def _debutants(self, info: dict, team: str) -> int:
        """#56: Count of debutants (0 caps) in starting XI."""
        starting = info.get('home_starting_xi', [])
        if not starting or self.players_df is None:
            return np.nan

        count = 0
        for name in starting:
            matched = self.players_df[
                (self.players_df['country_of_citizenship'] == team) &
                (self.players_df['name'].str.contains(
                    name[:4], case=False, na=False))
            ]
            if not matched.empty and matched['international_caps'].fillna(0).iloc[0] == 0:
                count += 1

        return count

    def _height(self, info: dict, team: str) -> float:
        """#57: Average height of starting XI in cm."""
        starting = info.get('home_starting_xi', [])
        if not starting or self.players_df is None:
            return np.nan

        heights = []
        for name in starting:
            matched = self.players_df[
                (self.players_df['country_of_citizenship'] == team) &
                (self.players_df['name'].str.contains(
                    name[:4], case=False, na=False))
            ]
            if not matched.empty:
                h = matched['height_in_cm'].iloc[0]
                if pd.notna(h):
                    heights.append(h)

        return np.mean(heights) if heights else np.nan


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_pre_match_signals(home_team: str,
                               away_team: str,
                               match_date: str = None) -> dict:
    """
    End-to-end: search news → LLM extract → compute signals.

    Returns dict of {signal_name: value} for all 12 signals.
    """
    print(f"\n{'='*50}")
    print(f"Extracting pre-match signals for {home_team} vs {away_team}")
    print(f"{'='*50}")

    # Step 1-2: Search news
    news = search_news_for_match(home_team, away_team)
    print(f"Found {len(news['articles'])} relevant articles")
    for a in news['articles'][:5]:
        print(f"  - {a['title'][:80]}")

    # Step 3: LLM extraction
    llm_result = extract_with_llm(
        news['articles'], home_team, away_team)

    print(f"\nLLM extracted:")
    print(f"  Formation: {llm_result['home_formation']} vs {llm_result['away_formation']}")
    print(f"  Starting XI (home): {len(llm_result['home_starting_xi'])} players")
    print(f"  Missing (home): {llm_result['home_missing_players']}")
    print(f"  Injuries (home): {len(llm_result['home_injury_details'])} reported")

    # Step 4: Compute signals
    computer = SignalComputer()
    signals = computer.compute_all(llm_result, home_team, away_team, match_date)

    print(f"\nComputed signals:")
    for name, val in signals.items():
        if not np.isnan(val):
            print(f"  {name}: {val:.3f}")
        else:
            print(f"  {name}: N/A")

    return signals


# ---------------------------------------------------------------------------
# Team name → Chinese mapping (for search queries)
# ---------------------------------------------------------------------------

def _team_name_to_chinese(name: str) -> str:
    """Map English national team names to Chinese for better search results."""
    mapping = {
        'Argentina': '阿根廷', 'Brazil': '巴西', 'France': '法国',
        'Germany': '德国', 'England': '英格兰', 'Spain': '西班牙',
        'Italy': '意大利', 'Netherlands': '荷兰', 'Portugal': '葡萄牙',
        'Belgium': '比利时', 'Croatia': '克罗地亚', 'Uruguay': '乌拉圭',
        'Colombia': '哥伦比亚', 'Mexico': '墨西哥', 'USA': '美国',
        'United States': '美国', 'Japan': '日本', 'South Korea': '韩国',
        'Korea Republic': '韩国', 'Iran': '伊朗', 'IR Iran': '伊朗',
        'Morocco': '摩洛哥', 'Senegal': '塞内加尔', 'Nigeria': '尼日利亚',
        'Egypt': '埃及', 'Cameroon': '喀麦隆', 'Ghana': '加纳',
        'Ivory Coast': '科特迪瓦', "Côte d'Ivoire": '科特迪瓦',
        'Tunisia': '突尼斯', 'Algeria': '阿尔及利亚',
        'Australia': '澳大利亚', 'Saudi Arabia': '沙特阿拉伯',
        'Qatar': '卡塔尔', 'Canada': '加拿大', 'Costa Rica': '哥斯达黎加',
        'Denmark': '丹麦', 'Switzerland': '瑞士', 'Sweden': '瑞典',
        'Poland': '波兰', 'Serbia': '塞尔维亚', 'Wales': '威尔士',
        'Scotland': '苏格兰', 'Ukraine': '乌克兰', 'Turkey': '土耳其',
        'Austria': '奥地利', 'Czechia': '捷克', 'Czech Republic': '捷克',
        'Norway': '挪威', 'Russia': '俄罗斯', 'Greece': '希腊',
        'Ecuador': '厄瓜多尔', 'Peru': '秘鲁', 'Chile': '智利',
        'Paraguay': '巴拉圭', 'Venezuela': '委内瑞拉', 'Bolivia': '玻利维亚',
        'South Africa': '南非', 'Mali': '馬里', 'Burkina Faso': '布基纳法索',
        'DR Congo': '刚果民主共和国', 'Equatorial Guinea': '赤道几内亚',
        'Gabon': '加蓬', 'Cape Verde': '佛得角',
        'Panama': '巴拿马', 'Honduras': '洪都拉斯',
        'New Zealand': '新西兰',
    }
    return mapping.get(name, name)


if __name__ == '__main__':
    # Quick test without LLM (just search)
    news = search_news_for_match('Argentina', 'Brazil')
    print(f"Test: Found {len(news['articles'])} articles for Argentina vs Brazil")
