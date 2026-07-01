"""
Pre-match news scraper for Chinese football sources.
Targets: 懂球帝 (dongqiudi.com), 直播吧 (zhibo8.cc).
Extracts: injuries, predicted lineups, team news, sentiment.

All sources are accessible from China without VPN.
"""
import re
import time
import hashlib
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
NEWS_DIR = DATA_DIR / 'processed' / 'news'
NEWS_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# Keywords that indicate pre-match information
INJURY_KEYWORDS = [
    '受伤', '伤病', '缺席', '伤缺', '无缘', '缺阵', '伤停', '骨折',
    '拉伤', '扭伤', '肌肉', '手术', '恢复', '复出', '伤愈',
    'injury', 'injured', 'out', 'doubt', 'unavailable', 'missing',
]

LINEUP_KEYWORDS = [
    '首发', '阵容', '名单', '大名单', '出战', '先发', '十一人',
    '首发名单', '预计首发', '可能首发', '阵容预测',
    'lineup', 'starting XI', 'squad', 'formation',
]

TACTICS_KEYWORDS = [
    '战术', '阵型', '打法', '策略', '进攻', '防守', '反击',
    '高位逼抢', '控球', '长传', '短传', '433', '442', '352', '4231',
    'formation', 'tactic', 'strategy', 'pressing',
]

MOTIVATION_KEYWORDS = [
    '出线', '晋级', '淘汰', '生死战', '关键战', '必须赢', '保平',
    '荣誉之战', '复仇', '德比', '宿敌',
    'must-win', 'crucial', 'decider', 'qualification', 'knockout',
]


class DongQiuDiScraper:
    """Scraper for 懂球帝 (dongqiudi.com) - China's largest football community."""

    BASE_URL = 'https://www.dongqiudi.com'

    def search_team_news(self, team_name: str, days_back: int = 7) -> list:
        """Search for recent news about a national team."""
        articles = []
        search_url = f'{self.BASE_URL}/search?search={team_name}&type=news'

        try:
            resp = requests.get(search_url, headers=HEADERS, timeout=15)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Parse article listings
            for item in soup.select('a[href*="/archives/"]')[:20]:
                title = item.get_text(strip=True)
                url = item.get('href', '')
                if url and not url.startswith('http'):
                    url = self.BASE_URL + url

                if title and self._is_match_relevant(title, team_name):
                    articles.append({
                        'title': title,
                        'url': url,
                        'source': 'dongqiudi',
                        'team': team_name,
                        'scraped_at': datetime.now().isoformat(),
                    })
        except Exception as e:
            print(f"  懂球帝 search error for '{team_name}': {e}")

        return articles

    def get_article_content(self, url: str) -> dict:
        """Fetch full article text and parse for pre-match signals."""
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract main content
            content_div = soup.select_one('div.article-content, div.detail-content, article')
            if not content_div:
                content_div = soup.select_one('div.content')

            text = content_div.get_text(separator='\n', strip=True) if content_div else ''

            # Extract timestamp
            time_el = soup.select_one('time, span.time, .article-time')
            pub_time = time_el.get_text(strip=True) if time_el else None

            return {
                'text': text,
                'pub_time': pub_time,
                'has_injury_info': bool(self._find_keywords(text, INJURY_KEYWORDS)),
                'has_lineup_info': bool(self._find_keywords(text, LINEUP_KEYWORDS)),
                'has_tactics_info': bool(self._find_keywords(text, TACTICS_KEYWORDS)),
                'has_motivation_info': bool(self._find_keywords(text, MOTIVATION_KEYWORDS)),
            }
        except Exception as e:
            print(f"  Error fetching article {url}: {e}")
            return {'text': '', 'pub_time': None}

    def _is_match_relevant(self, title: str, team_name: str) -> bool:
        """Check if article title is relevant to team's upcoming match."""
        title_lower = title.lower()
        return (
            team_name.lower() in title_lower
            and any(kw in title_lower for kw in
                    ['比赛', '对阵', '迎战', '挑战', '预赛', '小组',
                     'match', 'game', 'fixture', 'preview',
                     '预测', '前瞻', '分析', '阵容', '伤病',
                     'predict', 'preview', 'analysis'])
        )

    @staticmethod
    def _find_keywords(text: str, keywords: list) -> list:
        """Return list of matching keywords found in text."""
        text_lower = text.lower()
        return [kw for kw in keywords if kw.lower() in text_lower]


class Zhibo8Scraper:
    """Scraper for 直播吧 (zhibo8.cc) - major Chinese sports news aggregator.
    URL pattern: https://news.zhibo8.cc/zuqiu/YYYY-MM-DD/{id}native.htm
    """

    BASE_URL = 'https://news.zhibo8.cc'

    def get_match_previews(self, team_name: str = None) -> list:
        """Scrape match preview articles from the football news section."""
        articles = []
        news_urls = [
            f'{self.BASE_URL}/zuqiu/',  # Football news main page
        ]

        for url in news_urls:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.encoding = 'utf-8'
                soup = BeautifulSoup(resp.text, 'html.parser')

                for link in soup.find_all('a'):
                    href = link.get('href', '')
                    title = link.get_text(strip=True)

                    # Article URLs: /zuqiu/YYYY-MM-DD/...native.htm
                    if '/zuqiu/' not in href or '/more' in href:
                        continue
                    if not title or len(title) < 10:
                        continue

                    if not href.startswith('http'):
                        if href.startswith('//'):
                            href = 'https:' + href
                        elif href.startswith('/'):
                            href = f'https://news.zhibo8.cc{href}'

                    if team_name is None or team_name.lower() in title.lower():
                        articles.append({
                            'title': title,
                            'url': href,
                            'source': 'zhibo8',
                            'team': team_name,
                            'scraped_at': datetime.now().isoformat(),
                        })
            except Exception as e:
                print(f"  直播吧 error: {e}")

        return articles

    def get_article_content(self, url: str) -> dict:
        """Fetch article text from 直播吧."""
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Try multiple content selectors
            for selector in ['div.content', 'article', 'div.article-body',
                             'div.article-content', 'div#article-content',
                             'div.main-content', 'div.detail-content']:
                content = soup.select_one(selector)
                if content:
                    break

            text = content.get_text(separator='\n', strip=True) if content else ''

            return {
                'text': text,
                'pub_time': None,
                'has_injury_info': bool(DongQiuDiScraper._find_keywords(text, INJURY_KEYWORDS)),
                'has_lineup_info': bool(DongQiuDiScraper._find_keywords(text, LINEUP_KEYWORDS)),
                'has_tactics_info': bool(DongQiuDiScraper._find_keywords(text, TACTICS_KEYWORDS)),
                'has_motivation_info': bool(DongQiuDiScraper._find_keywords(text, MOTIVATION_KEYWORDS)),
            }
        except Exception as e:
            return {'text': ''}


# --- Sentiment Analysis ---

def analyze_sentiment_vader(text: str) -> float:
    """Simple rule-based sentiment: positive/negative keyword ratio.
    Falls back to SnowNLP for Chinese text if available."""
    positive_words = [
        '强势', '大胜', '状态好', '出色', '优秀', '稳定', '优势',
        '复出', '回归', '增强', '提升', '进步', '信心', '气势',
        'strong', 'excellent', 'confident', 'boost', 'return',
    ]
    negative_words = [
        '低迷', '惨败', '状态差', '糟糕', '下滑', '劣势', '隐患',
        '缺席', '受伤', '停赛', '危机', '压力', '质疑', '球迷不满',
        'weak', 'poor', 'crisis', 'injury', 'suspension', 'pressure',
    ]

    text_lower = text.lower()
    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)

    if pos_count + neg_count == 0:
        return 0.0  # neutral

    return (pos_count - neg_count) / (pos_count + neg_count)


def analyze_sentiment_snownlp(text: str) -> float:
    """Use SnowNLP for Chinese text sentiment (0=negative, 1=positive)."""
    try:
        from snownlp import SnowNLP
        s = SnowNLP(text)
        return s.sentiments  # returns float 0-1
    except ImportError:
        return analyze_sentiment_vader(text)


# --- Main News Collection ---

def collect_pre_match_news(home_team: str, away_team: str,
                            match_date: str = None) -> dict:
    """
    Collect all pre-match news for a given fixture.
    Returns structured data with injury, lineup, and sentiment signals.
    """
    dongqiudi = DongQiuDiScraper()
    zhibo8 = Zhibo8Scraper()

    result = {
        'home_team': home_team,
        'away_team': away_team,
        'match_date': match_date,
        'home_news': [],
        'away_news': [],
        'signals': defaultdict(list),
    }

    # Search for each team's news
    for team, key in [(home_team, 'home_news'), (away_team, 'away_news')]:
        # 懂球帝
        articles = dongqiudi.search_team_news(team)
        # 直播吧
        articles += zhibo8.get_match_previews(team)

        # Deduplicate
        seen_urls = set()
        unique_articles = []
        for a in articles:
            if a['url'] not in seen_urls:
                seen_urls.add(a['url'])
                unique_articles.append(a)

        result[key] = unique_articles[:10]  # top 10 most recent

    # Extract signals from articles
    for team, articles in [('home', result['home_news']), ('away', result['away_news'])]:
        for article in articles[:5]:  # analyze top 5
            content = dongqiudi.get_article_content(article['url'])
            if not content['text']:
                content = zhibo8.get_article_content(article['url'])

            if content['text']:
                sentiment = analyze_sentiment_snownlp(content['text'][:500])
                result['signals'][f'{team}_sentiment'].append(sentiment)

                if content['has_injury_info']:
                    result['signals'][f'{team}_has_injury_news'] = 1
                if content['has_lineup_info']:
                    result['signals'][f'{team}_has_lineup_news'] = 1
                if content['has_tactics_info']:
                    result['signals'][f'{team}_has_tactics_news'] = 1
                if content['has_motivation_info']:
                    result['signals'][f'{team}_has_motivation_news'] = 1

            time.sleep(0.3)  # rate limiting

    # Aggregate signals
    signals_out = {}
    for key, values in result['signals'].items():
        if key.endswith('_sentiment'):
            signals_out[key] = sum(values) / len(values) if values else 0.0
        else:
            signals_out[key] = max(values) if values else 0

    return {
        'home_team': home_team,
        'away_team': away_team,
        'signals': signals_out,
        'articles_count': len(result['home_news']) + len(result['away_news']),
    }


def build_news_features(upcoming_matches: pd.DataFrame) -> pd.DataFrame:
    """
    For each upcoming match, collect news and extract features.
    This is the pre-match signal generator for the Bayesian adjustment layer.
    """
    features_list = []

    for _, match in upcoming_matches.iterrows():
        print(f"Collecting news for {match['home_team']} vs {match['away_team']}...")
        news_data = collect_pre_match_news(
            match['home_team'],
            match['away_team'],
            str(match.get('date', '')),
        )

        row = {
            'home_team': match['home_team'],
            'away_team': match['away_team'],
            'date': match.get('date'),
            **news_data['signals'],
            'articles_count': news_data['articles_count'],
        }
        features_list.append(row)

    return pd.DataFrame(features_list)


if __name__ == '__main__':
    # Quick test: search for news about a national team
    scraper = DongQiuDiScraper()
    articles = scraper.search_team_news('阿根廷')
    print(f"Found {len(articles)} articles about 阿根廷")
    for a in articles[:5]:
        print(f"  - {a['title'][:80]}")
        print(f"    {a['url']}")
