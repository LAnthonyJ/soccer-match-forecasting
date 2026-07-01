"""
500.com odds scraper for World Cup matches.
Extracts match info + 竞彩/European odds from 500.com HTML pages.
"""
import requests
import re
import time
import pandas as pd
from bs4 import BeautifulSoup
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'raw'


def scrape_wc_match_list() -> pd.DataFrame:
    """
    Scrape the 2026 World Cup match listing from 500.com.
    Returns DataFrame with [match_id, home_team, away_team].
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    url = 'http://odds.500.com/index_worldcup.shtml'
    r = session.get(url, timeout=15)
    r.encoding = 'gb2312'

    html = r.text

    # Extract match info from title attributes
    # Pattern: title="瑞典VS加拿大数据分析" in shuju links
    pattern = re.compile(
        r'title="([^"]+)"[^<]*>.*?shuju-(\d+)\.shtml',
        re.DOTALL
    )
    matches_raw = pattern.findall(html)

    matches = []
    for title, mid in matches_raw:
        # Parse "TeamAVSTeamB数据分析" or "TeamAVSTeamB赔率对比"
        title_clean = title.replace('数据分析', '').replace('赔率对比', '').replace('百家欧赔', '')
        if 'VS' in title_clean:
            home, rest = title_clean.split('VS', 1)
            away = rest.rstrip('欧亚析对比据数率赔家百')
            # Clean team names
            home = home.strip()
            away = away.strip()
            if home and away:
                matches.append({
                    'match_id': int(mid),
                    'home_team': home,
                    'away_team': away,
                })

    df = pd.DataFrame(matches)
    if len(df) == 0:
        print("No matches found on 500.com index page.")
        return df

    # Save with proper encoding for display
    output_path = DATA_DIR / 'wc2026_matches_500.csv'
    df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"Found {len(df)} matches, saved to {output_path}")

    return df


def scrape_match_odds(match_id: int) -> dict:
    """
    Scrape odds data for a single match from 500.com.

    Returns dict with:
        home_odds, draw_odds, away_odds (竞彩/decimal format)
        avg_home, avg_draw, avg_away (average European odds)
        bet365_h, bet365_d, bet365_a (bet365 odds)
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    result = {'match_id': match_id}

    # Try the European odds page (ouzhi)
    url = f'http://odds.500.com/fenxi/ouzhi-{match_id}.shtml'
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200 and len(r.text) > 5000:
            r.encoding = 'gb2312'
            result.update(_parse_ouzhi_page(r.text, match_id))
    except Exception as e:
        print(f"  ouzhi page error for {match_id}: {e}")

    # Also try the stats page for 竞彩 specific odds
    if 'avg_home' not in result:
        url2 = f'http://odds.500.com/fenxi/shuju-{match_id}.shtml'
        try:
            r2 = session.get(url2, timeout=15)
            if r2.status_code == 200 and len(r2.text) > 5000:
                r2.encoding = 'gb2312'
                result.update(_parse_shuju_page(r2.text, match_id))
        except Exception as e:
            print(f"  shuju page error for {match_id}: {e}")

    return result


def _parse_ouzhi_page(html: str, match_id: int) -> dict:
    """
    Parse the European odds (欧指) page.

    500.com ouzhi page structure:
    - Each bookmaker has 4 rows: [opening, closing, opening_prob, closing_prob]
    - Opening/closing contain: home, draw, away (decimal odds)
    - Prob rows contain: home%, draw%, away% (0-1 range)
    """
    result = {}

    # Extract title for team names
    title_m = re.search(r'<title>(.*?)VS(.*?)\(', html)
    if title_m:
        result['home_team'] = title_m.group(1).strip()
        result['away_team'] = title_m.group(2).strip()

    # Extract all td text that looks like odds (X.XX format)
    all_tds = re.findall(r'<td[^>]*>\s*(\d+\.\d{2})\s*</td>', html)
    if not all_tds:
        return result

    # Group into triplets and filter:
    # - min > 1.0 (exclude probability rows which have values < 1.0)
    # - max > 2.0 (exclude rows where all values are near 1.0)
    # - max < 50 (exclude obvious errors)
    valid_groups = []
    for i in range(0, len(all_tds) - 2, 3):
        try:
            vals = [float(all_tds[i]), float(all_tds[i + 1]), float(all_tds[i + 2])]
            if min(vals) > 1.0 and max(vals) > 2.0 and max(vals) < 50:
                valid_groups.append(vals)
        except ValueError:
            continue

    if len(valid_groups) < 2:
        return result

    # Structure: [opening1, closing1, opening2, closing2, ...]
    closing_odds = [valid_groups[i + 1] for i in range(0, len(valid_groups) - 1, 2)]
    opening_odds = [valid_groups[i] for i in range(0, len(valid_groups) - 1, 2)]

    if closing_odds:
        result['odds_home'] = round(sum(c[0] for c in closing_odds) / len(closing_odds), 2)
        result['odds_draw'] = round(sum(c[1] for c in closing_odds) / len(closing_odds), 2)
        result['odds_away'] = round(sum(c[2] for c in closing_odds) / len(closing_odds), 2)
        result['n_bookmakers'] = len(closing_odds)

    if opening_odds:
        result['init_home'] = round(sum(c[0] for c in opening_odds) / len(opening_odds), 2)
        result['init_draw'] = round(sum(c[1] for c in opening_odds) / len(opening_odds), 2)
        result['init_away'] = round(sum(c[2] for c in opening_odds) / len(opening_odds), 2)

    return result


def _parse_shuju_page(html: str, match_id: int) -> dict:
    """
    Parse the data analysis (数据) page as fallback.
    Uses same logic as ouzhi page since structure is similar.
    """
    return _parse_ouzhi_page(html, match_id)


def scrape_all_wc_odds() -> pd.DataFrame:
    """
    Main function: scrape match list + odds for all WC 2026 matches.
    Saves to data/raw/wc2026_odds_500.csv.
    """
    print("Scraping 2026 World Cup match list from 500.com...")
    matches = scrape_wc_match_list()

    if len(matches) == 0:
        return matches

    print(f"\nScraping odds for {len(matches)} matches...")
    odds_records = []
    for _, row in matches.iterrows():
        mid = row['match_id']
        print(f"  Match {mid}...")
        odds = scrape_match_odds(mid)

        # Use page title team names (more reliable), fall back to index page
        home = odds.get('home_team', row['home_team'])
        away = odds.get('away_team', row['away_team'])

        odds_records.append({
            'match_id': mid,
            'home_team': home,
            'away_team': away,
            'odds_home': odds.get('odds_home'),
            'odds_draw': odds.get('odds_draw'),
            'odds_away': odds.get('odds_away'),
            'init_home': odds.get('init_home'),
            'init_draw': odds.get('init_draw'),
            'init_away': odds.get('init_away'),
            'n_bookmakers': odds.get('n_bookmakers', 0),
        })
        time.sleep(0.5)  # Be respectful

    odds_df = pd.DataFrame(odds_records)
    odds_df = odds_df.dropna(subset=['odds_home'])
    print(f"\nSuccessfully scraped odds for {len(odds_df)}/{len(matches)} matches")

    # Save
    output_path = DATA_DIR / 'wc2026_odds_500.csv'
    odds_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"Saved to {output_path}")

    return odds_df


if __name__ == '__main__':
    result = scrape_all_wc_odds()
    if len(result) > 0:
        print("\n=== ODDS SUMMARY ===")
        for _, row in result.iterrows():
            print(f"{row['home_team']:15} vs {row['away_team']:15} | "
                  f"H:{row['odds_home']:.2f} D:{row['odds_draw']:.2f} A:{row['odds_away']:.2f}")
