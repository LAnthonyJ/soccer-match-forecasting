"""
Continuous style matchup features.

Instead of classifying teams into 4 discrete styles (which was too crude),
compute the DIFFERENCE between home and away team's event metrics.
XGBoost learns the interaction automatically.

Features:
  matchup_ppda_diff, matchup_pass_diff, matchup_speed_diff,
  matchup_defline_diff, matchup_pressure_diff, matchup_progpass_diff
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / 'data'


def build_style_profiles() -> pd.DataFrame:
    """
    For each team that has StatsBomb event data, compute their average style
    profile across all matches. Returns (team, metric_avg_1, ..., metric_avg_n).
    """
    evt = pd.read_csv(DATA_DIR / 'processed' / 'event_features.csv')

    # Get team names from columns
    team_metrics = {}
    for col in evt.columns:
        if '_xg' in col and '_xg_' not in col:
            team = col.replace('_xg', '')
            if team not in team_metrics:
                team_metrics[team] = []

    metrics = ['ppda', 'pass_completion', 'direct_speed', 'def_line_height',
               'pressures', 'progressive_passes']

    for _, row in evt.iterrows():
        for team in team_metrics:
            vals = {}
            for m in metrics:
                col = f'{team}_{m}'
                if col in row and pd.notna(row[col]):
                    vals[m] = float(row[col])
            if len(vals) == len(metrics):
                team_metrics[team].append(vals)

    # Average per team
    profiles = {}
    for team, recs in team_metrics.items():
        if len(recs) < 3:
            continue
        avg = {}
        for m in metrics:
            avg[m] = np.mean([r[m] for r in recs])
        profiles[team] = avg

    print(f"Style profiles for {len(profiles)} teams")
    return profiles


def attach_matchup_features(results_df: pd.DataFrame,
                              profiles: dict) -> pd.DataFrame:
    """
    For each match in results_df, compute style difference features.
    If a team doesn't have a profile, leave NaN.
    """
    df = results_df.copy()

    metrics = ['ppda', 'pass_completion', 'direct_speed', 'def_line_height',
               'pressures', 'progressive_passes']

    for m in metrics:
        col_name = f'matchup_{m}_diff'
        df[col_name] = np.nan

    matched = 0
    for idx, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']

        if home not in profiles or away not in profiles:
            continue

        hp = profiles[home]
        ap = profiles[away]

        for m in metrics:
            if m in hp and m in ap:
                # Normalize by the range to make metrics comparable
                # PPDA: lower = more pressing. Flip so higher = better for pressing team
                if m == 'ppda':
                    diff = ap[m] - hp[m]  # positive = home presses more (lower ppda)
                else:
                    diff = hp[m] - ap[m]  # positive = home advantage

                df.at[idx, f'matchup_{m}_diff'] = diff
                matched += 1

    n = df[f'matchup_ppda_diff'].notna().sum()
    print(f"Matchup features attached to {n} matches")
    return df


if __name__ == '__main__':
    profiles = build_style_profiles()

    # Show sample profiles
    for team in ['Argentina', 'Brazil', 'France', 'England', 'Spain', 'Germany']:
        if team in profiles:
            p = profiles[team]
            print(f"\n{team}:")
            print(f"  PPDA={p['ppda']:.1f} (lower=more pressing)")
            print(f"  Pass={p['pass_completion']:.1%}")
            print(f"  Direct={p['direct_speed']:.1f}")
            print(f"  DefLine={p['def_line_height']:.1f}")
            print(f"  Pressures={p['pressures']:.0f}")

    # Attach to results
    res = pd.read_csv(DATA_DIR / 'raw' / 'results.csv')
    res['date'] = pd.to_datetime(res['date'])
    merged = attach_matchup_features(res, profiles)

    out_path = DATA_DIR / 'processed' / 'results_with_matchup_v2.csv'
    merged.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")
