"""
Tactical matchup feature (#54): style-vs-style historical win rate.

Classifies each team into 4 styles from event data:
  - Possession (high pass_completion + low direct_speed + high def_line)
  - High Press (low PPDA + high pressures + high def_line)
  - Counter (high direct_speed + low possession + low def_line)
  - Low Block (low def_line + low pressures + high PPDA)

Then computes: for style_A (home) vs style_B (away), what's the historical
home win rate deviation from baseline?
"""
import json, glob
import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
EVENTS_DIR = DATA_DIR / 'raw' / 'statsbomb-open-data' / 'events'
MATCHES_DIR = DATA_DIR / 'raw' / 'statsbomb-open-data' / 'matches'


def classify_style(row: dict, team_name: str) -> str:
    """
    Classify a team's style from event metrics using percentile thresholds
    across all teams, not absolute cutoffs.
    The style is the one where the team scores HIGHEST relative to the median.
    """
    metrics = {
        'possession': ['pass_completion', 'def_line_height'],
        'highpress':  ['ppda_inv', 'pressures'],
        'counter':    ['direct_speed'],
        'lowblock':   ['def_line_height_inv', 'ppda', 'pressures_inv'],
    }
    # Simple: use PPDA, pass_completion, direct_speed, def_line
    # PPDA (lower = more pressing), Pass comp (higher = possession)
    # Direct speed (higher = counter), Def line (higher = high press)
    scores = {}
    ppda = row.get(f'{team_name}_ppda', 15)
    pass_comp = row.get(f'{team_name}_pass_completion', 0.78)
    direct = row.get(f'{team_name}_direct_speed', 10)
    def_line = row.get(f'{team_name}_def_line_height', 40) or 40
    pressures = row.get(f'{team_name}_pressures', 80) or 80

    scores['possession'] = (pass_comp / 0.85) * 0.6 + (def_line / 50) * 0.4
    scores['highpress'] = (10 / max(ppda, 1)) * 0.5 + (min(pressures, 300) / 300) * 0.5
    scores['counter'] = (direct / 15) * 0.6 + (1 - pass_comp) * 0.4
    scores['lowblock'] = (1 - def_line / 80) * 0.5 + (ppda / 20) * 0.3 + (1 - pressures / 300) * 0.2

    return max(scores, key=scores.get)


def compute_matchup_table(match_df: pd.DataFrame) -> dict:
    """
    From historical matches with style labels, compute: for each (home_style, away_style)
    pair, the home win rate deviation from 40% baseline.
    """
    matchup = defaultdict(list)
    for _, row in match_df.iterrows():
        hs = row.get('home_style', 'unknown')
        aws = row.get('away_style', 'unknown')
        if hs == 'unknown' or aws == 'unknown':
            continue
        home_won = 1 if row['home_score'] > row['away_score'] else 0
        drew = 1 if row['home_score'] == row['away_score'] else 0
        matchup[(hs, aws)].append({'home_win': home_won, 'draw': drew})

    result = {}
    for (hs, aws), results in matchup.items():
        n = len(results)
        if n < 2:
            continue
        hw_rate = sum(r['home_win'] for r in results) / n
        draw_rate = sum(r['draw'] for r in results) / n
        # Deviation from 40% home win baseline (neutral tournament)
        advantage = hw_rate - 0.40
        result[(hs, aws)] = {
            'n': n, 'home_win_rate': hw_rate, 'draw_rate': draw_rate,
            'advantage': advantage,
        }

    return result


def build_matchup_features() -> pd.DataFrame:
    """
    1. Load StatsBomb event features
    2. Classify each team's style per match
    3. Compute matchup table
    4. Attach matchup features to all matches in results.csv
    """
    # Load event features
    evt = pd.read_csv(DATA_DIR / 'processed' / 'event_features.csv')

    # Load match info
    sb_matches = []
    for f in glob.glob(str(MATCHES_DIR / '*' / '*.json')):
        with open(f) as jf:
            for m in json.load(jf):
                sb_matches.append({
                    'sb_match_id': m['match_id'],
                    'date': pd.to_datetime(m['match_date']),
                    'home_team': m['home_team']['home_team_name'],
                    'away_team': m['away_team']['away_team_name'],
                    'home_score': m['home_score'], 'away_score': m['away_score'],
                })
    sb_df = pd.DataFrame(sb_matches)
    merged = sb_df.merge(evt, left_on='sb_match_id', right_on='match_id', how='inner')

    # Classify styles
    merged['home_style'] = merged.apply(
        lambda r: classify_style(r, r['home_team']), axis=1)
    merged['away_style'] = merged.apply(
        lambda r: classify_style(r, r['away_team']), axis=1)

    print(f"Style distribution (home):")
    print(merged['home_style'].value_counts().to_string())
    print(f"\nStyle distribution (away):")
    print(merged['away_style'].value_counts().to_string())

    # Compute matchup table
    matchup = compute_matchup_table(merged)
    print(f"\nMatchup table ({len(matchup)} pairs):")
    for (hs, aws), stats in sorted(matchup.items(), key=lambda x: -x[1]['n']):
        print(f"  {hs:12s} vs {aws:12s}: n={stats['n']:3d}  "
              f"H-win={stats['home_win_rate']:.0%}  Draw={stats['draw_rate']:.0%}  "
              f"Adv={stats['advantage']:+.0%}")

    # Build per-match features for results.csv
    # Match each results.csv row to a matchup advantage
    res = pd.read_csv(DATA_DIR / 'raw' / 'results.csv')
    res['date'] = pd.to_datetime(res['date'])

    # For each team, compute their dominant style from ALL their event matches
    team_styles = {}
    for _, row in merged.iterrows():
        for side in ['home', 'away']:
            team = row[f'{side}_team']
            style = row[f'{side}_style']
            team_styles.setdefault(team, []).append(style)

    team_dominant = {}
    for team, styles in team_styles.items():
        from collections import Counter
        team_dominant[team] = Counter(styles).most_common(1)[0][0]

    # Attach matchup features
    res['tactical_matchup_adv'] = np.nan
    res['tactical_matchup_n'] = np.nan

    n_matched = 0
    for idx, row in res.iterrows():
        home = row['home_team']
        away = row['away_team']
        hs = team_dominant.get(home)
        aws = team_dominant.get(away)
        if hs and aws and (hs, aws) in matchup:
            res.at[idx, 'tactical_matchup_adv'] = matchup[(hs, aws)]['advantage']
            res.at[idx, 'tactical_matchup_n'] = matchup[(hs, aws)]['n']
            n_matched += 1

    print(f"\nMatched {n_matched}/{len(res)} rows with tactical matchup features")
    print(f"Teams with style: {len(team_dominant)}")

    # Save
    out = DATA_DIR / 'processed' / 'results_with_matchup.csv'
    res.to_csv(out, index=False)
    print(f"Saved to {out}")
    return res


if __name__ == '__main__':
    build_matchup_features()
