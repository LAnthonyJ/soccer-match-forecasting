"""
Extract advanced features from StatsBomb event data:
- xG (expected goals) per team per match
- PPDA (passes per defensive action) — pressing intensity
- Pass completion %, average pass length
- Direct speed (carry progression)
- Defensive line height
- Tactical style classification
"""
import json
import os
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / 'data'
EVENTS_DIR = DATA_DIR / 'raw' / 'statsbomb-open-data' / 'events'
MATCHES_DIR = DATA_DIR / 'raw' / 'statsbomb-open-data' / 'matches'


def process_match_events(match_id: int) -> dict:
    """Extract all advanced metrics for one match."""
    event_file = EVENTS_DIR / f'{match_id}.json'
    if not event_file.exists():
        return {}

    with open(event_file) as f:
        events = json.load(f)

    teams = {}
    for e in events:
        team_name = e.get('team', {}).get('name', 'Unknown')
        if team_name not in teams:
            teams[team_name] = _init_team_stats()

        t = teams[team_name]
        etype = e['type']['name']

        if etype == 'Shot':
            xg = e.get('shot', {}).get('statsbomb_xg', 0)
            t['xg_total'] += xg
            t['shots'] += 1
            if e['shot'].get('outcome', {}).get('name') == 'Goal':
                t['goals'] += 1
                t['xg_goals'] += xg

        elif etype == 'Pass':
            t['passes_total'] += 1
            p = e.get('pass', {})
            length = p.get('length', 0) or 0
            t['pass_length_sum'] += length
            outcome = p.get('outcome', {})
            if not outcome or outcome.get('name') in ('Complete', None, 'Incomplete'):
                pass  # most passes have no outcome = complete
            elif outcome.get('name') != 'Incomplete':
                t['passes_complete'] += 1
            if outcome.get('name') == 'Incomplete':
                pass
            else:
                t['passes_complete'] += 1

            # Progressive passes: forward passes in attacking half
            if 'location' in e and 'end_location' in p:
                if e['location'][0] < p['end_location'][0]:  # forward
                    t['progressive_passes'] += 1

        elif etype in ('Ball Recovery', 'Interception', 'Duel', 'Foul Committed',
                        'Block', 'Clearance'):
            t['defensive_actions'] += 1
            # Track defensive line height
            if 'location' in e:
                t['def_action_y_sum'] += e['location'][1]
                t['def_action_count'] += 1

        elif etype == 'Pressure':
            t['pressures'] += 1

        elif etype == 'Carry':
            c = e.get('carry', {})
            end_loc = c.get('end_location', [0, 0])
            if 'location' in e:
                dx = end_loc[0] - e['location'][0]
                dy = end_loc[1] - e['location'][1]
                dist = np.sqrt(dx**2 + dy**2)
                t['carry_distance'] += dist
                t['carry_count'] += 1

        # Possession tracking
        if 'possession_team' in e:
            t['possession_events'] += 1

    # Compute derived metrics
    result = {}
    for team_name, t in teams.items():
        p = t['passes_total']
        result[f'{team_name}_xg'] = round(t['xg_total'], 4)
        result[f'{team_name}_xg_per_shot'] = round(t['xg_total'] / max(1, t['shots']), 4)
        result[f'{team_name}_shots'] = t['shots']
        result[f'{team_name}_pass_completion'] = round(
            t['passes_complete'] / max(1, p), 4)
        result[f'{team_name}_avg_pass_length'] = round(
            t['pass_length_sum'] / max(1, p), 2)
        result[f'{team_name}_progressive_passes'] = t['progressive_passes']

        # PPDA = opponent passes / defensive actions
        # (lower = more intense pressing)
        da = max(1, t['defensive_actions'])
        result[f'{team_name}_ppda'] = round(p / da, 2)

        # Direct speed (meters per carry)
        result[f'{team_name}_direct_speed'] = round(
            t['carry_distance'] / max(1, t['carry_count']), 2)

        # Defensive line height (average y of defensive actions, 0-80 scale)
        if t['def_action_count'] > 0:
            result[f'{team_name}_def_line_height'] = round(
                t['def_action_y_sum'] / t['def_action_count'], 2)
        else:
            result[f'{team_name}_def_line_height'] = None

        # Pressures
        result[f'{team_name}_pressures'] = t['pressures']

        # Possession % (estimated from events)
        result[f'{team_name}_possession_events'] = t['possession_events']

    return result


def _init_team_stats():
    return {
        'xg_total': 0.0, 'xg_goals': 0.0, 'shots': 0, 'goals': 0,
        'passes_total': 0, 'passes_complete': 0, 'pass_length_sum': 0.0,
        'progressive_passes': 0,
        'defensive_actions': 0, 'def_action_y_sum': 0.0, 'def_action_count': 0,
        'pressures': 0,
        'carry_distance': 0.0, 'carry_count': 0,
        'possession_events': 0,
    }


def extract_all_event_features() -> pd.DataFrame:
    """
    Process all available event files and build a features DataFrame.
    """
    event_files = sorted(os.listdir(EVENTS_DIR))
    print(f"Processing {len(event_files)} event files...")

    all_features = []
    for i, fname in enumerate(event_files):
        match_id = int(fname.replace('.json', ''))
        feats = process_match_events(match_id)
        if feats:
            feats['match_id'] = match_id
            all_features.append(feats)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(event_files)}")

    df = pd.DataFrame(all_features)
    print(f"\nExtracted features for {len(df)} matches")
    print(f"Features: {df.columns.tolist()}")
    return df


def classify_tactical_style(event_df: pd.DataFrame) -> pd.DataFrame:
    """
    Classify each team's tactical style from event features.

    Styles:
    - Possession (high pass comp, high prog passes, high def line)
    - High Press (low PPDA, high pressures)
    - Counter-Attack (high direct speed, low possession)
    - Low Block (low def line, low PPDA, low pressures)
    """
    df = event_df.copy()

    # Get all team names from columns
    team_names = set()
    for col in df.columns:
        if '_xg' in col:
            team_names.add(col.replace('_xg', ''))

    for team in team_names:
        ppda = df.get(f'{team}_ppda', pd.Series([np.nan]))
        def_line = df.get(f'{team}_def_line_height', pd.Series([np.nan]))
        direct = df.get(f'{team}_direct_speed', pd.Series([np.nan]))
        pass_comp = df.get(f'{team}_pass_completion', pd.Series([np.nan]))
        pressures = df.get(f'{team}_pressures', pd.Series([np.nan]))

        # Style scores (higher = more of that style)
        df[f'{team}_style_possession'] = (
            (pass_comp > 0.80).astype(float) * 0.5 +
            (def_line > 40).astype(float) * 0.3 +
            (direct < 8).astype(float) * 0.2
        )
        df[f'{team}_style_highpress'] = (
            (ppda < 8).astype(float) * 0.4 +
            (pressures > 100).astype(float) * 0.4 +
            (def_line > 45).astype(float) * 0.2
        )
        df[f'{team}_style_counter'] = (
            (direct > 12).astype(float) * 0.4 +
            (pass_comp < 0.78).astype(float) * 0.3 +
            (def_line < 35).astype(float) * 0.3
        )
        df[f'{team}_style_lowblock'] = (
            (def_line < 30).astype(float) * 0.4 +
            (ppda > 12).astype(float) * 0.3 +
            (pressures < 50).astype(float) * 0.3
        )

    return df


def build_team_event_history(event_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a team-level event history: for each StatsBomb match,
    record each team's metrics. Then compute rolling averages per team.

    Returns DataFrame with columns:
      date, team, xg, xg_per_shot, pass_completion, ppda, direct_speed,
      def_line_height, pressures, progressive_passes, style_possession,
      style_highpress, style_counter, style_lowblock
    """
    import glob

    # Load StatsBomb match info
    sb_matches = []
    for f in glob.glob(str(MATCHES_DIR / '*' / '*.json')):
        with open(f) as jf:
            for m in json.load(jf):
                sb_matches.append({
                    'sb_match_id': m['match_id'],
                    'date': pd.to_datetime(m['match_date']),
                    'home_team': m['home_team']['home_team_name'],
                    'away_team': m['away_team']['away_team_name'],
                    'home_score': m['home_score'],
                    'away_score': m['away_score'],
                })
    sb_df = pd.DataFrame(sb_matches)
    sb_df = sb_df.merge(event_df, left_on='sb_match_id', right_on='match_id', how='left')

    # Normalize team names
    team_name_map = {
        'United States': 'USA', 'Korea Republic': 'South Korea',
        'IR Iran': 'Iran', "Côte d'Ivoire": 'Ivory Coast',
    }

    records = []
    for _, row in sb_df.iterrows():
        date = row['date']
        home = team_name_map.get(row['home_team'], row['home_team'])
        away = team_name_map.get(row['away_team'], row['away_team'])

        for team, prefix in [(home, home), (away, away)]:
            rec = {'date': date, 'team': team}
            for metric in ['xg', 'xg_per_shot', 'shots', 'pass_completion',
                           'avg_pass_length', 'progressive_passes', 'ppda',
                           'direct_speed', 'def_line_height', 'pressures']:
                col = f'{prefix}_{metric}'
                if col in row and pd.notna(row[col]):
                    rec[metric] = row[col]
            for style in ['possession', 'highpress', 'counter', 'lowblock']:
                col = f'{prefix}_style_{style}'
                if col in row and pd.notna(row[col]):
                    rec[f'style_{style}'] = row[col]
            records.append(rec)

    history = pd.DataFrame(records)
    history = history.sort_values('date')
    print(f"  Team event history: {len(history)} rows, {history['team'].nunique()} teams")
    return history


def compute_rolling_event_features(results_df: pd.DataFrame,
                                     team_history: pd.DataFrame,
                                     n_matches: int = 10) -> pd.DataFrame:
    """
    For each match in results_df, attach the HOME and AWAY team's recent
    event metrics (rolling average of last n_matches).

    This gives every match access to each team's recent xG, PPDA, style, etc.
    """
    df = results_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    history = team_history.sort_values('date')

    # Metrics to attach
    metrics = ['xg', 'xg_per_shot', 'pass_completion', 'ppda',
               'direct_speed', 'def_line_height', 'pressures', 'progressive_passes']
    styles = ['style_possession', 'style_highpress', 'style_counter', 'style_lowblock']

    for m in metrics:
        for side in ['home', 'away']:
            df[f'event_{side}_{m}'] = np.nan
    for s in styles:
        for side in ['home', 'away']:
            df[f'event_{side}_{s}'] = np.nan

    matched = 0
    for _, row in df.iterrows():
        match_date = row['date']
        for side in ['home', 'away']:
            team = row[f'{side}_team']

            # Get this team's recent matches before match_date
            team_hist = history[
                (history['team'] == team) &
                (history['date'] < match_date)
            ].tail(n_matches)

            if len(team_hist) >= 3:  # need at least 3 matches for meaningful stats
                for m in metrics:
                    if m in team_hist.columns:
                        df.at[_, f'event_{side}_{m}'] = team_hist[m].mean()
                for s in styles:
                    if s in team_hist.columns:
                        df.at[_, f'event_{side}_{s}'] = team_hist[s].mean()
                matched += 1

    matched_rows = df['event_home_xg'].notna().sum()
    print(f"  Rolling event features attached: {matched_rows} matches")
    return df


def build_event_features_pipeline(results_path: str = None) -> pd.DataFrame:
    """
    Full pipeline: extract events → build team history → merge to results.
    """
    if results_path is None:
        results_path = str(DATA_DIR / 'raw' / 'results.csv')

    print("Step 1: Extract event features...")
    event_df = extract_all_event_features()
    event_df = classify_tactical_style(event_df)

    print("\nStep 2: Build team event history...")
    team_history = build_team_event_history(event_df)

    print("\nStep 3: Attach rolling event features to results...")
    res = pd.read_csv(results_path)
    res = compute_rolling_event_features(res, team_history)

    out_path = DATA_DIR / 'processed' / 'results_with_event_features.csv'
    res.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")

    # Show coverage
    event_cols = [c for c in res.columns if c.startswith('event_')]
    for col in sorted(event_cols)[:6]:
        n = res[col].notna().sum()
        print(f"  {col}: {n}/{len(res)}")

    return res


if __name__ == '__main__':
    build_event_features_pipeline()
