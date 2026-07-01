"""
Feature engineering pipeline: recent form, head-to-head, tournament context.
"""
import pandas as pd
import numpy as np


def compute_recent_form(df: pd.DataFrame, n_matches: int = 10) -> pd.DataFrame:
    """
    For each match, compute the recent form of both teams based on their
    previous n matches. Form includes: goals for, goals against, results.

    Must be called on chronologically sorted data.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Accumulate team match history
    team_history = {}

    form_records = []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_score = row['home_score']
        away_score = row['away_score']

        # Get current histories
        h_hist = team_history.get(home, [])
        a_hist = team_history.get(away, [])

        # Recent n matches for each team
        h_recent = h_hist[-n_matches:] if len(h_hist) >= n_matches else h_hist
        a_recent = a_hist[-n_matches:] if len(a_hist) >= n_matches else a_hist

        # Compute form metrics
        home_form = _compute_form_metrics(h_recent)
        away_form = _compute_form_metrics(a_recent)

        form_records.append({
            **{f'home_{k}': v for k, v in home_form.items()},
            **{f'away_{k}': v for k, v in away_form.items()},
        })

        # Update history after the match (only if played)
        if pd.notna(home_score) and pd.notna(away_score):
            if home not in team_history:
                team_history[home] = []
            if away not in team_history:
                team_history[away] = []
            team_history[home].append({
                'gf': home_score, 'ga': away_score,
                'opponent': away, 'date': row['date']
            })
            team_history[away].append({
                'gf': away_score, 'ga': home_score,
                'opponent': home, 'date': row['date']
            })

    form_df = pd.DataFrame(form_records)
    return pd.concat([df.reset_index(drop=True), form_df.reset_index(drop=True)], axis=1)


def _compute_form_metrics(history: list) -> dict:
    """Compute form metrics from a team's match history."""
    n = len(history)
    if n == 0:
        return {
            'form_gf_avg': np.nan, 'form_ga_avg': np.nan,
            'form_gd_avg': np.nan, 'form_win_rate': np.nan,
            'form_points_per_game': np.nan, 'form_n_matches': 0,
        }

    gfs = [m['gf'] for m in history]
    gas = [m['ga'] for m in history]

    wins = sum(1 for gf, ga in zip(gfs, gas) if gf > ga)
    draws = sum(1 for gf, ga in zip(gfs, gas) if gf == ga)

    return {
        'form_gf_avg': np.mean(gfs),
        'form_ga_avg': np.mean(gas),
        'form_gd_avg': np.mean(gfs) - np.mean(gas),
        'form_win_rate': wins / n,
        'form_points_per_game': (wins * 3 + draws) / n,
        'form_n_matches': n,
    }


def compute_weighted_form(df: pd.DataFrame) -> pd.DataFrame:
    """
    Time-weighted form: recent matches weighted more heavily.
    Exponential decay with half-life of ~12 matches.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    team_history = {}
    records = []
    HALF_LIFE = 12

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_score = row['home_score']
        away_score = row['away_score']

        h_hist = team_history.get(home, [])
        a_hist = team_history.get(away, [])

        # Weighted form
        h_weighted = _weighted_form(h_hist, HALF_LIFE)
        a_weighted = _weighted_form(a_hist, HALF_LIFE)

        records.append({
            'home_wgf': h_weighted.get('wgf', np.nan),
            'home_wga': h_weighted.get('wga', np.nan),
            'home_wgd': h_weighted.get('wgd', np.nan),
            'away_wgf': a_weighted.get('wgf', np.nan),
            'away_wga': a_weighted.get('wga', np.nan),
            'away_wgd': a_weighted.get('wgd', np.nan),
        })

        # Update history
        if pd.notna(home_score) and pd.notna(away_score):
            if home not in team_history:
                team_history[home] = []
            if away not in team_history:
                team_history[away] = []
            team_history[home].append({'gf': home_score, 'ga': away_score})
            team_history[away].append({'gf': away_score, 'ga': home_score})

    form_df = pd.DataFrame(records)
    return pd.concat([df.reset_index(drop=True), form_df.reset_index(drop=True)], axis=1)


def _weighted_form(history: list, half_life: int) -> dict:
    """Compute exponentially weighted form metrics."""
    n = len(history)
    if n == 0:
        return {}

    decay = 0.5 ** (1.0 / half_life)
    weights = [decay ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)

    wgf = sum(m['gf'] * w for m, w in zip(history, weights)) / w_sum
    wga = sum(m['ga'] * w for m, w in zip(history, weights)) / w_sum

    return {'wgf': wgf, 'wga': wga, 'wgd': wgf - wga}


def compute_head_to_head(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each match, compute head-to-head record between the two teams
    from their previous encounters.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    h2h = {}
    records = []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_score = row['home_score']
        away_score = row['away_score']

        key = tuple(sorted([home, away]))
        h2h_record = h2h.get(key, {'home_wins': 0, 'draws': 0, 'away_wins': 0, 'n': 0})

        records.append({
            'h2h_matches': h2h_record['n'],
            'h2h_home_win_pct': h2h_record['home_wins'] / max(1, h2h_record['n']),
            'h2h_draw_pct': h2h_record['draws'] / max(1, h2h_record['n']),
            'h2h_away_win_pct': h2h_record['away_wins'] / max(1, h2h_record['n']),
        })

        # Update H2H after match
        if pd.notna(home_score) and pd.notna(away_score):
            if key not in h2h:
                h2h[key] = {'home_wins': 0, 'draws': 0, 'away_wins': 0, 'n': 0}
            if home_score > away_score:
                h2h[key]['home_wins'] += 1
            elif home_score == away_score:
                h2h[key]['draws'] += 1
            else:
                h2h[key]['away_wins'] += 1
            h2h[key]['n'] += 1

    h2h_df = pd.DataFrame(records)
    return pd.concat([df.reset_index(drop=True), h2h_df.reset_index(drop=True)], axis=1)


def compute_tournament_context(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enhanced tournament context (#32-#39).

    Adds: tournament type, knockout, rest days, match sequence, motivation flags.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # #32: Tournament type encoding (keep existing)
    df['is_friendly'] = (df['tournament'] == 'Friendly').astype(int)
    df['is_world_cup'] = (df['tournament'] == 'FIFA World Cup').astype(int)
    df['is_wc_qualifier'] = df['tournament'].str.contains('qualification', na=False).astype(int)
    df['is_continental'] = df['tournament'].apply(
        lambda x: any(t in str(x) for t in ['UEFA', 'Copa', 'AFC', 'CONCACAF', 'African', 'Nations'])
    ).astype(int)

    # #33: is_knockout — detect from tournament + round keywords
    knockout_keywords = ['final', 'semi', 'quarter', 'round of 16', 'round of 32',
                          'third place', '3rd place']
    df['is_knockout'] = df['tournament'].astype(str).str.lower().apply(
        lambda x: int(any(kw in x for kw in knockout_keywords))
    )

    # #37: days since last match for each team
    team_last_date = {}
    home_rest, away_rest = [], []
    for _, row in df.iterrows():
        d = row['date']
        home_rest.append((d - team_last_date.get(row['home_team'], d)).days)
        away_rest.append((d - team_last_date.get(row['away_team'], d)).days)
        team_last_date[row['home_team']] = d
        team_last_date[row['away_team']] = d
    df['days_since_last_home'] = home_rest
    df['days_since_last_away'] = away_rest
    df['rest_advantage'] = df['days_since_last_home'] - df['days_since_last_away']

    # #38: match sequence in tournament (approximate by counting team's matches that year)
    df['year'] = df['date'].dt.year
    team_year_count = {}
    home_seq, away_seq = [], []
    for _, row in df.iterrows():
        hk = (row['home_team'], row['year'])
        ak = (row['away_team'], row['year'])
        home_seq.append(team_year_count.get(hk, 0) + 1)
        away_seq.append(team_year_count.get(ak, 0) + 1)
        team_year_count[hk] = team_year_count.get(hk, 0) + 1
        team_year_count[ak] = team_year_count.get(ak, 0) + 1
    df['match_seq_home'] = home_seq
    df['match_seq_away'] = away_seq

    # #40: neutral venue (already in data)
    df['neutral_venue'] = df['neutral'].astype(int) if 'neutral' in df.columns else 0

    return df


def compute_enhanced_form(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enhanced form features (#21, #24, #26).

    #21: average ELO of recent opponents (quality-adjusted form)
    #24: tournament-importance-weighted recent results
    #26: performance in same tournament type historically
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # Tournament K multipliers for #24
    TOURNAMENT_WEIGHTS = {
        'FIFA World Cup': 3.0,
        'FIFA World Cup qualification': 2.5,
        'UEFA Euro': 2.5,
        'Copa América': 2.5,
        'African Cup of Nations': 2.5,
        'AFC Asian Cup': 2.5,
        'UEFA Nations League': 1.5,
        'Friendly': 1.0,
    }

    team_history = {}  # team → [{gf, ga, opponent, elo, tournament, date}]

    opp_elo_home, opp_elo_away = [], []
    weighted_form_h, weighted_form_a = [], []
    tourney_form_h, tourney_form_a = [], []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_score = row['home_score']
        away_score = row['away_score']
        tournament = row['tournament']
        date = row['date']

        h_hist = team_history.get(home, [])
        a_hist = team_history.get(away, [])

        # #21: Average ELO of recent 5 opponents
        elo_col = 'elo_away' if 'elo_away' in df.columns else 'elo_home'
        h_opp_elos = [m.get('opp_elo', np.nan) for m in h_hist[-5:]]
        a_opp_elos = [m.get('opp_elo', np.nan) for m in a_hist[-5:]]
        opp_elo_home.append(np.nanmean(h_opp_elos) if h_opp_elos else np.nan)
        opp_elo_away.append(np.nanmean(a_opp_elos) if a_opp_elos else np.nan)

        # #24: Tournament-weighted recent form (last 10)
        def weighted_result(hist):
            if not hist:
                return np.nan
            total_w, total = 0, 0
            for m in hist[-10:]:
                w = TOURNAMENT_WEIGHTS.get(m.get('tournament', 'Friendly'), 1.0)
                total_w += w
                total += w * (1 if m['gf'] > m['ga'] else 0.5 if m['gf'] == m['ga'] else 0)
            return total / total_w if total_w > 0 else np.nan

        weighted_form_h.append(weighted_result(h_hist))
        weighted_form_a.append(weighted_result(a_hist))

        # #26: Same tournament type historical form
        def tourney_form(hist, t):
            same = [m for m in hist if m.get('tournament') == t]
            if not same:
                return np.nan
            wins = sum(1 for m in same if m['gf'] > m['ga'])
            return wins / len(same)

        tourney_form_h.append(tourney_form(h_hist, tournament))
        tourney_form_a.append(tourney_form(a_hist, tournament))

        # Update history
        if pd.notna(home_score) and pd.notna(away_score):
            for team, gf, ga, opp, elo_key in [
                (home, home_score, away_score, away, 'elo_away'),
                (away, away_score, home_score, home, 'elo_home'),
            ]:
                if team not in team_history:
                    team_history[team] = []
                opp_elo_val = row.get(elo_key, np.nan)
                team_history[team].append({
                    'gf': gf, 'ga': ga, 'opponent': opp,
                    'opp_elo': opp_elo_val, 'tournament': tournament, 'date': date,
                })

    result = pd.DataFrame({
        'home_opp_elo_avg_5': opp_elo_home,
        'away_opp_elo_avg_5': opp_elo_away,
        'home_weighted_form': weighted_form_h,
        'away_weighted_form': weighted_form_a,
        'home_tournament_form': tourney_form_h,
        'away_tournament_form': tourney_form_a,
    })
    return pd.concat([df.reset_index(drop=True), result.reset_index(drop=True)], axis=1)


def compute_enhanced_h2h(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enhanced H2H features (#28-#31).

    #28: H2H last 10 years only
    #29: H2H competitive matches only (exclude friendlies)
    #30: H2H average goal difference
    #31: H2H at same venue type (neutral/non-neutral)
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    h2h = {}  # (team1, team2) → [matches]

    results = {
        'h2h_recent_win_home': [], 'h2h_recent_win_away': [],
        'h2h_comp_win_home': [], 'h2h_comp_win_away': [],
        'h2h_goal_diff': [], 'h2h_same_venue_pct': [],
    }

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        key = tuple(sorted([home, away]))
        matches = h2h.get(key, [])

        # #28: Recent H2H (last 10 years)
        recent = [m for m in matches
                  if (row['date'] - m['date']).days <= 3652]
        r_n = len(recent)
        r_home_wins = sum(1 for m in recent if (m['home'] == home and m['hg'] > m['ag']) or
                          (m['home'] == away and m['ag'] > m['hg']))
        r_away_wins = sum(1 for m in recent if (m['home'] == home and m['hg'] < m['ag']) or
                          (m['home'] == away and m['ag'] < m['hg']))
        results['h2h_recent_win_home'].append(r_home_wins / max(1, r_n))
        results['h2h_recent_win_away'].append(r_away_wins / max(1, r_n))

        # #29: Competitive-only H2H
        comp = [m for m in matches if m.get('competitive', True)]
        c_n = len(comp)
        c_home_wins = sum(1 for m in comp if (m['home'] == home and m['hg'] > m['ag']) or
                          (m['home'] == away and m['ag'] > m['hg']))
        c_away_wins = sum(1 for m in comp if (m['home'] == home and m['hg'] < m['ag']) or
                          (m['home'] == away and m['ag'] < m['hg']))
        results['h2h_comp_win_home'].append(c_home_wins / max(1, c_n))
        results['h2h_comp_win_away'].append(c_away_wins / max(1, c_n))

        # #30: H2H goal difference
        if matches:
            gds = []
            for m in matches:
                if m['home'] == home:
                    gds.append(m['hg'] - m['ag'])
                else:
                    gds.append(m['ag'] - m['hg'])
            results['h2h_goal_diff'].append(np.mean(gds))
        else:
            results['h2h_goal_diff'].append(np.nan)

        # #31: H2H at same venue type
        current_neutral = row.get('neutral', False)
        same_venue_matches = [m for m in matches
                               if m.get('neutral', False) == current_neutral]
        sv_n = len(same_venue_matches)
        results['h2h_same_venue_pct'].append(sv_n / max(1, len(matches)) if matches else np.nan)

        # Update H2H history
        if pd.notna(row['home_score']) and pd.notna(row['away_score']):
            if key not in h2h:
                h2h[key] = []
            h2h[key].append({
                'home': home, 'away': away,
                'hg': row['home_score'], 'ag': row['away_score'],
                'date': row['date'],
                'competitive': row.get('is_friendly', 0) == 0,
                'neutral': row.get('neutral', False),
            })

    result_df = pd.DataFrame(results)
    return pd.concat([df.reset_index(drop=True), result_df.reset_index(drop=True)], axis=1)


def compute_fifa_points(df: pd.DataFrame, fifa_csv_path: str = None) -> pd.DataFrame:
    """
    #3: Attach FIFA ranking points to each match.

    Uses the pre-downloaded FIFA rankings CSV.
    Points are the most recent ranking before the match date.
    """
    if fifa_csv_path is None:
        from pathlib import Path
        fifa_csv_path = Path(__file__).parent.parent.parent / 'data' / 'raw' / 'fifa_ranking_historical.csv'

    try:
        fifa = pd.read_csv(fifa_csv_path)
        fifa['date'] = pd.to_datetime(fifa['date'])
        fifa = fifa.sort_values('date')
    except Exception:
        print("  FIFA rankings CSV not found, skipping #3")
        df['fifa_points_home'] = np.nan
        df['fifa_points_away'] = np.nan
        return df

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # Build lookup by team_short code — need mapping from our team names to FIFA codes
    # FIFA CSV uses 3-letter codes (ARG, FRA, ENG...)
    # We'll build a crude mapping by matching team names
    fifa_team_names = fifa['team'].unique()
    fifa_team_codes = dict(zip(fifa['team'], fifa['team_short']))

    # For each match, find the most recent FIFA points for each team
    home_points, away_points = [], []
    for _, row in df.iterrows():
        match_date = row['date']
        ht = row['home_team']
        at = row['away_team']

        hp = _get_latest_fifa_points(fifa, ht, match_date)
        ap = _get_latest_fifa_points(fifa, at, match_date)
        home_points.append(hp)
        away_points.append(ap)

    df['fifa_points_home'] = home_points
    df['fifa_points_away'] = away_points
    df['fifa_points_diff'] = df['fifa_points_home'] - df['fifa_points_away']

    matched = sum(1 for hp in home_points if pd.notna(hp))
    print(f"  FIFA points matched for {matched}/{len(df)} matches")
    return df


def _get_latest_fifa_points(fifa_df: pd.DataFrame, team_name: str,
                              match_date: pd.Timestamp) -> float:
    """Get the most recent FIFA points for a team before match_date."""
    # Try exact name match first
    team_rows = fifa_df[
        (fifa_df['team'] == team_name) &
        (fifa_df['date'] <= match_date)
    ]
    if not team_rows.empty:
        return team_rows.sort_values('date').iloc[-1]['total_points']

    # Try partial match
    team_rows = fifa_df[
        (fifa_df['team'].str.contains(team_name[:5], case=False, na=False)) &
        (fifa_df['date'] <= match_date)
    ]
    if not team_rows.empty:
        return team_rows.sort_values('date').iloc[-1]['total_points']

    return np.nan


def compute_coach_win_rate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute manager win rate from historical results.

    Tracks each team's historical win rate as a proxy for manager quality.
    Uses a 3-year rolling window (approximate tenure of NT managers).
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    team_history = {}
    home_wr, away_wr = [], []

    for _, row in df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_score = row['home_score']
        away_score = row['away_score']
        date = row['date']

        h_hist = team_history.get(home, [])
        a_hist = team_history.get(away, [])

        # Last 3 years of matches (~30-40 matches for a NT)
        h_recent = [m for m in h_hist if (date - m['date']).days <= 1095]
        a_recent = [m for m in a_hist if (date - m['date']).days <= 1095]

        h_w = sum(1 for m in h_recent if m['result'] == 'W') / max(1, len(h_recent))
        a_w = sum(1 for m in a_recent if m['result'] == 'W') / max(1, len(a_recent))
        home_wr.append(h_w)
        away_wr.append(a_w)

        if pd.notna(home_score) and pd.notna(away_score):
            for team, result in [(home, 'W' if home_score > away_score else 'L' if home_score < away_score else 'D'),
                                  (away, 'W' if away_score > home_score else 'L' if away_score < home_score else 'D')]:
                if team not in team_history:
                    team_history[team] = []
                team_history[team].append({'result': result, 'date': date})

    result = pd.DataFrame({
        'coach_win_rate_home': home_wr,
        'coach_win_rate_away': away_wr,
    })
    return pd.concat([df.reset_index(drop=True), result.reset_index(drop=True)], axis=1)


def build_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Orchestrator: build all features for modeling (Phase 4 enhanced).

    Returns DataFrame with all features and target variables.
    """
    print("Computing ELO ratings...")
    from src.features.elo import compute_elo_history, get_pre_match_elo
    elo_history = compute_elo_history(df)
    df = get_pre_match_elo(elo_history, df)

    print("Computing recent form...")
    df = compute_recent_form(df, n_matches=10)

    print("Computing weighted form...")
    df = compute_weighted_form(df)

    print("Computing head-to-head...")
    df = compute_head_to_head(df)

    print("Computing enhanced H2H...")
    df = compute_enhanced_h2h(df)

    print("Adding tournament context...")
    df = compute_tournament_context(df)

    print("Computing enhanced form (#21, #24, #26)...")
    df = compute_enhanced_form(df)

    print("Attaching FIFA ranking points (#3)...")
    df = compute_fifa_points(df)

    print("Computing coach win rate...")
    df = compute_coach_win_rate(df)

    # Target variables
    df['result'] = df.apply(
        lambda r: 'H' if r['home_score'] > r['away_score']
        else ('D' if r['home_score'] == r['away_score'] else 'A'),
        axis=1
    )
    df['target_home_win'] = (df['result'] == 'H').astype(int)
    df['target_draw'] = (df['result'] == 'D').astype(int)
    df['target_away_win'] = (df['result'] == 'A').astype(int)
    df['total_goals'] = df['home_score'] + df['away_score']

    print(f"Feature matrix shape: {df.shape}")
    return df
