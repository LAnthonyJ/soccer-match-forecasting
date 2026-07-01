"""
Historical backtest CLI 鈥?Sportmonks fixtures 鈫?model prediction 鈫?evaluation.

Supports:
  --model dc          Static Dixon-Coles (baseline)
  --model dynamic-dc  Walk-forward dynamic DC with time-weighted online updates

Usage:
    python src/backtest_cli.py --from 2026-06-01 --to 2026-06-28
    python src/backtest_cli.py --from 2026-06-01 --to 2026-06-28 --model dynamic-dc
    python src/backtest_cli.py --from 2026-06-01 --to 2026-06-28 --compare
    python src/backtest_cli.py --from 2026-06-01 --to 2026-06-28 --league 732 --output wc.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.provider_schema import MatchRecord
from src.data.sportmonks_adapter import SportmonksAdapter
from src.data.sportmonks_client import get_fixtures_between
from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES
from src.models.backtest_runner import (
    HistoricalBacktestFixture,
    HistoricalBacktestMatchResult,
    HistoricalBacktestRun,
    HistoricalBacktestRunner,
    _build_report,
)
from src.models.dynamic_dc import DynamicDixonColesConfig, DynamicDixonColesService
from src.models.market_fusion import build_market_baseline_snapshot, normalize_three_way_odds
from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality
from src.models.score_models import ScoreMatrix, load_score_models
from src.data.prematch_snapshot import (
    PrematchDataSnapshot,
    build_prematch_snapshot,
    build_sportmonks_prematch_snapshot,
)
from src.models.lineup_adjustment import LineupAdjustmentConfig, build_lineup_lambda_adjustment
from src.models.total_goals_calibration import (
    LearnedTotalGoalsCalibrator,
    TotalGoalsCalibrationConfig,
    learn_global_multiplier,
)

BACKTEST_DIR = Path(__file__).parent.parent / "data" / "backtests"
BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

# Warmup window: fetch this many days of history before the backtest start
# so the dynamic DC has past results to learn from.
WARMUP_DAYS = 180


# -- Engine builders ------------------------------------------------------------

def _base_dc_model():
    score_models = load_score_models()
    dc = score_models.get("dixon_coles")
    if dc is None:
        raise RuntimeError("score_models.pkl missing dixon_coles model")
    return dc


def _goal_multiplier(override: float | None = None) -> float:
    if override is not None:
        return override
    return float(os.environ.get("TOTAL_GOALS_MULTIPLIER", "1.0"))


def build_engine_static(goal_multiplier: float | None = None) -> ProductionPredictionEngine:
    return ProductionPredictionEngine(
        base_dc_model=_base_dc_model(),
        model_version="sportmonks-dc-v1",
        data_version=f"sportmonks-backtest-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        goal_multiplier=_goal_multiplier(goal_multiplier),
    )


def build_engine_dynamic(
    service: DynamicDixonColesService,
    goal_multiplier: float | None = None,
) -> ProductionPredictionEngine:
    return ProductionPredictionEngine(
        base_dc_model=_base_dc_model(),
        model_version="dynamic-dc-v1",
        data_version=f"sportmonks-backtest-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        goal_multiplier=_goal_multiplier(goal_multiplier),
        dynamic_service=service,
    )


# -- Fixture builder ------------------------------------------------------------

def _raw_to_match(raw: dict) -> MatchRecord | None:
    """Parse one raw fixture dict into a MatchRecord, or None if unusable."""
    adapter = SportmonksAdapter()
    match = adapter.parse_match(raw)
    if match.status not in ("finished", "ft", "completed"):
        return None
    if match.home_score is None or match.away_score is None:
        return None
    return match


def fetch_matches(
    from_date: str,
    to_date: str,
    league_ids: list[int] | None = None,
) -> list[MatchRecord]:
    """Fetch finished matches with scores from Sportmonks."""
    if league_ids is None:
        league_ids = list(INTERNATIONAL_LEAGUES.keys())

    fd = datetime.fromisoformat(from_date)
    td = datetime.fromisoformat(to_date)

    try:
        raw_fixtures = get_fixtures_between(
            fd, td,
            league_ids=league_ids,
            includes="participants;scores;league",
        )
    except Exception:
        return []

    matches = []
    for raw in raw_fixtures:
        m = _raw_to_match(raw)
        if m is not None:
            matches.append(m)
    return matches


def _to_backtest_fixture(match: MatchRecord, raw: dict, fetch_odds: bool = True) -> HistoricalBacktestFixture:
    odds = None
    if fetch_odds:
        try:
            adapter = SportmonksAdapter()
            odds = adapter.average_odds(raw.get("id", 0))
        except Exception:
            odds = None
    return HistoricalBacktestFixture(
        match=match,
        odds=odds,
        quality=PredictionInputQuality(
            team_sample_home=30,
            team_sample_away=30,
            has_lineups=False,
            has_injuries=False,
            odds_snapshots=1 if odds else 0,
            odds_volatility=0.25,
        ),
        context=None,
        raw_payload=raw,
    )


# -- Static DC backtest ---------------------------------------------------------

def run_backtest_static(
    fixtures: list[HistoricalBacktestFixture],
    goal_multiplier: float | None = None,
) -> HistoricalBacktestRun:
    engine = build_engine_static(goal_multiplier=goal_multiplier)
    runner = HistoricalBacktestRunner(engine)
    return runner.run(fixtures)


# -- Dynamic DC walk-forward backtest -------------------------------------------

def run_backtest_dynamic(
    fixtures: list[HistoricalBacktestFixture],
    warmup_matches: list[MatchRecord],
    from_date_str: str,
    goal_multiplier: float | None = None,
    learned_calibrator=None,  # LearnedTotalGoalsCalibrator | None
    use_lineup_signals: bool = False,
    manual_lineups: str | None = None,
    manual_injuries: str | None = None,
    manual_strengths: str | None = None,
    fetch_prematch_api: bool = False,
) -> HistoricalBacktestRun:
    """
    Walk-forward backtest for dynamic DC.

    1. Warm up the dynamic DC on all matches before the backtest window.
    2. Walk through backtest fixtures chronologically:
       a. Predict with current state (per-match calibration if calibrator given)
       b. Then update from this match's result (no leakage)

    If learned_calibrator is provided, goal_multiplier is ignored and per-match
    grouped calibration is used instead.
    """
    from_date = datetime.fromisoformat(from_date_str)
    if from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=timezone.utc)

    config = DynamicDixonColesConfig()
    service = DynamicDixonColesService(_base_dc_model(), config)

    # Warmup: apply all pre-window matches chronologically
    warmup_sorted = sorted(warmup_matches, key=lambda m: m.kickoff_utc)
    warmup_applied = 0
    for m in warmup_sorted:
        if m.kickoff_utc < from_date:
            if service.update_from_match(m, as_of_date=from_date):
                warmup_applied += 1

    # If using learned per-match calibrator, disable fixed multiplier
    engine_gm = goal_multiplier if learned_calibrator is None else 1.0
    engine = build_engine_dynamic(service, goal_multiplier=engine_gm)
    generated_at = datetime.now(timezone.utc)

    # Walk forward through test fixtures chronologically
    sorted_fixtures = sorted(fixtures, key=lambda f: f.match.kickoff_utc)
    results: list[HistoricalBacktestMatchResult] = []

    for fixture in sorted_fixtures:
        match = fixture.match
        kickoff = match.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        # Predict BEFORE seeing this match's result
        prediction_time = kickoff - timedelta(hours=1)

        # Build lineup adjustment if enabled
        lineup_adj = None
        prematch_snap = None
        quality = fixture.quality or PredictionInputQuality()
        if use_lineup_signals:
            from src.data.provider_schema import MatchLineupSignal
            from src.features.lineup_signals import build_lineup_signals
            from src.models.lineup_adjustment import build_lineup_lambda_adjustment, LineupAdjustmentConfig

            # Build prematch snapshot — API or manual or empty
            prematch_snap = _build_prematch_for_match(
                match, fetch_prematch_api=fetch_prematch_api,
                raw_fixture=fixture.raw_payload,
                manual_lineups=manual_lineups,
                manual_injuries=manual_injuries,
                manual_strengths=manual_strengths,
            )

            # Build signals from the snapshot
            lineups_by_team: dict[str, list] = {}
            for p in prematch_snap.lineups:
                lineups_by_team.setdefault(p.team, []).append(p)

            signals = build_lineup_signals(
                match,
                lineups=lineups_by_team or None,
                injuries=prematch_snap.injuries or None,
                player_strengths=prematch_snap.player_strengths or None,
                lineup_type=prematch_snap.lineup_type,
            )
            lineup_adj = build_lineup_lambda_adjustment(
                signals["home"], signals["away"],
                config=LineupAdjustmentConfig(),
            )

            quality = PredictionInputQuality(
                team_sample_home=quality.team_sample_home,
                team_sample_away=quality.team_sample_away,
                has_lineups=prematch_snap.has_lineups(),
                has_injuries=prematch_snap.has_injuries(),
                odds_snapshots=quality.odds_snapshots,
                odds_volatility=quality.odds_volatility,
            )

        snapshot = engine.predict(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            odds=fixture.odds,
            quality=quality,
            neutral=match.neutral,
            context=fixture.context,
            generated_at=prediction_time,
            match_record=match,
            learned_calibrator=learned_calibrator,
            lineup_adjustment=lineup_adj,
            prematch_snapshot=prematch_snap,
        )

        results.append(
            HistoricalBacktestMatchResult(
                snapshot=snapshot,
                actual_home_goals=int(match.home_score),
                actual_away_goals=int(match.away_score),
                actual_outcome=_actual_outcome(int(match.home_score), int(match.away_score)),
                kickoff_utc=kickoff,
                prediction_time=prediction_time,
            )
        )

        # Now update from this match's result for future predictions
        # Use kickoff + 1 day as as_of_date so this match is eligible
        service.update_from_match(match, as_of_date=kickoff + timedelta(days=1))

    report = _build_report(results)
    return HistoricalBacktestRun(
        model_version="dynamic-dc-v1",
        data_version=f"sportmonks-backtest-{generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        generated_at=generated_at,
        results=results,
        report=report,
    )


def run_backtest_market(fixtures: list[HistoricalBacktestFixture]) -> HistoricalBacktestRun:
    """
    Market baseline: HDA probs come directly from de-vigged market odds.

    The score matrix is built from the base DC model for xG/O/U/BTTS display,
    but the HDA decision is purely market-driven.
    """
    dc_model = _base_dc_model()
    generated_at = datetime.now(timezone.utc)
    results: list[HistoricalBacktestMatchResult] = []

    for fixture in fixtures:
        match = fixture.match
        kickoff = match.kickoff_utc
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        prediction_time = kickoff - timedelta(hours=1)

        sm = dc_model.predict_score_matrix(match.home_team, match.away_team, neutral=match.neutral)

        if fixture.odds:
            market_probs = normalize_three_way_odds(*fixture.odds)
        else:
            market_probs = sm.match_outcome_probs()

        snapshot = build_market_baseline_snapshot(
            match_id=match.match_id,
            home_team=match.home_team,
            away_team=match.away_team,
            market_probs=market_probs,
            score_matrix=sm,
            generated_at=prediction_time,
        )

        results.append(
            HistoricalBacktestMatchResult(
                snapshot=snapshot,
                actual_home_goals=int(match.home_score),
                actual_away_goals=int(match.away_score),
                actual_outcome=_actual_outcome(int(match.home_score), int(match.away_score)),
                kickoff_utc=kickoff,
                prediction_time=prediction_time,
            )
        )

    report = _build_report(results)
    return HistoricalBacktestRun(
        model_version="market-baseline",
        data_version=f"sportmonks-odds-{generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        generated_at=generated_at,
        results=results,
        report=report,
    )


def _strip_odds(fixtures: list[HistoricalBacktestFixture]) -> list[HistoricalBacktestFixture]:
    """Return copies with odds=None to force pure-model predictions (no fusion)."""
    return [
        HistoricalBacktestFixture(
            match=f.match,
            odds=None,
            quality=f.quality,
            context=f.context,
            prediction_time=f.prediction_time,
        )
        for f in fixtures
    ]


def _actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


# -- Persistence ----------------------------------------------------------------

def save_backtest_run(
    run: HistoricalBacktestRun,
    output_path: str | Path | None = None,
    tier: str = "vip",
) -> Path:
    if output_path:
        path = Path(output_path)
    else:
        ts = run.generated_at.strftime("%Y%m%dT%H%M%S")
        safe_version = run.model_version.replace("/", "-").replace(" ", "_")
        path = BACKTEST_DIR / safe_version / f"{ts}.json"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run.to_dict(tier=tier), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_backtest_runs(model_version: str | None = None) -> list[dict]:
    runs = []
    search_dir = BACKTEST_DIR / model_version if model_version else BACKTEST_DIR

    for path in sorted(search_dir.glob("**/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if model_version and data.get("model_version") != model_version:
            continue
        runs.append(data)

    return sorted(runs, key=lambda r: r.get("generated_at", ""), reverse=True)


# -- Report formatter -----------------------------------------------------------

def _print_report(run: HistoricalBacktestRun) -> None:
    r = run.report
    print(f"\n{'='*60}")
    print(f"  BACKTEST REPORT 鈥?{run.model_version}")
    print(f"  Data: {run.data_version}")
    print(f"  Generated: {run.generated_at.isoformat()}")
    print(f"{'='*60}")
    print(f"  Matches:           {r.n_matches:>6}")
    print(f"  {'鈹€'*40}")
    print(f"  HDA LogLoss:       {r.hda_logloss:>8.4f}")
    print(f"  HDA Brier:         {r.hda_brier:>8.4f}")
    print(f"  ECE:               {r.ece:>8.4f}")
    if r.market_logloss is not None:
        diff = r.hda_logloss - r.market_logloss
        print(f"  Market LogLoss:    {r.market_logloss:>8.4f}  (螖={diff:+.4f})")
    if r.clv_mean is not None:
        print(f"  CLV Mean:          {r.clv_mean:>8.4f}")
    print(f"  {'鈹€'*40}")
    print(f"  RPS (total goals): {r.rps_total_goals:>8.4f}")
    print(f"  Score LogLoss:     {r.exact_score_logloss:>8.4f}")
    print(f"  Top-3 Score Hit:   {r.top3_score_hit_rate:>7.1%}")
    print(f"  Top-5 Score Hit:   {r.top5_score_hit_rate:>7.1%}")
    if r.mean_predicted_goals is not None and r.mean_actual_goals is not None:
        print(f"  {'鈹€'*40}")
        print(f"  Mean Pred Goals:   {r.mean_predicted_goals:>8.2f}")
        print(f"  Mean Actual Goals: {r.mean_actual_goals:>8.2f}")
        if r.over25_ece is not None:
            print(f"  Over 2.5 ECE:      {r.over25_ece:>8.4f}")
        if r.btts_ece is not None:
            print(f"  BTTS ECE:          {r.btts_ece:>8.4f}")
    print(f"{'='*60}\n")


def _print_comparison(runs: list[HistoricalBacktestRun]) -> None:
    if len(runs) < 2:
        return

    labels = [r.model_version for r in runs]
    col_w = max(len(l) for l in labels) + 2
    col_w = max(col_w, 14)

    header = f"  {'Metric':<25}" + "".join(f"{l:>{col_w}}" for l in labels)
    sep = f"  {'鈹€'*25}" + f"  {'鈹€'*(col_w-2)}" * len(labels)

    print(f"\n{'='*(25 + col_w*len(labels) + 4)}")
    print(f"  MODEL COMPARISON")
    print(f"{'='*(25 + col_w*len(labels) + 4)}")
    print(header)
    print(sep)

    def _fmt(v: float | None, spec: str = ".4f") -> str:
        if v is None:
            return f"{'-':>{col_w}}"
        return f"{v:>{col_w}{spec}}"

    # Always-available metrics
    for name, attr in [("HDA LogLoss", "hda_logloss"), ("HDA Brier", "hda_brier"), ("ECE", "ece")]:
        vals = [getattr(r.report, attr) for r in runs]
        row = f"  {name:<25}" + "".join(_fmt(v) for v in vals)
        print(row)

    # Market LogLoss 鈥?per-run, may be None for pure-model columns
    market_vals = [r.report.market_logloss for r in runs]
    if any(v is not None for v in market_vals):
        row = f"  {'Market LogLoss':<25}" + "".join(_fmt(v) for v in market_vals)
        print(row)

    # Score metrics
    for name, attr in [("RPS (total goals)", "rps_total_goals"), ("Score LogLoss", "exact_score_logloss")]:
        vals = [getattr(r.report, attr) for r in runs]
        row = f"  {name:<25}" + "".join(_fmt(v) for v in vals)
        print(row)

    # Percentage metrics
    for name, attr in [("Top-3 Hit Rate", "top3_score_hit_rate"), ("Top-5 Hit Rate", "top5_score_hit_rate")]:
        vals = [getattr(r.report, attr) for r in runs]
        row = f"  {name:<25}" + "".join(_fmt(v, ".1%") for v in vals)
        print(row)

    # Goal calibration metrics
    goal_metrics = [
        ("Mean Pred Goals", "mean_predicted_goals", ".2f"),
        ("Mean Actual Goals", "mean_actual_goals", ".2f"),
        ("Over 2.5 ECE", "over25_ece", ".4f"),
        ("BTTS ECE", "btts_ece", ".4f"),
    ]
    for name, attr, spec in goal_metrics:
        vals = [getattr(r.report, attr) for r in runs]
        if any(v is not None for v in vals):
            row = f"  {name:<25}" + "".join(_fmt(v, spec) for v in vals)
            print(row)

    print(f"{'='*(25 + col_w*len(labels) + 4)}\n")


# -- CLI ------------------------------------------------------------------------

def _learn_multiplier_from_warmup(
    from_date_str: str,
    league_ids: list[int] | None,
) -> float | None:
    """Learn a global goal multiplier from warmup matches."""
    from_date = datetime.fromisoformat(from_date_str)
    warmup_start = from_date - timedelta(days=WARMUP_DAYS)

    warmup_matches = fetch_matches(
        warmup_start.strftime("%Y-%m-%d"),
        from_date_str,
        league_ids=league_ids,
    )
    if len(warmup_matches) < 10:
        return None

    dc_model = _base_dc_model()

    def predict_fn(m):
        return dc_model.predict_score_matrix(m.home_team, m.away_team, neutral=m.neutral)

    config = TotalGoalsCalibrationConfig()
    mlt = learn_global_multiplier(warmup_matches, predict_fn, config)
    return mlt if mlt != config.default_multiplier else None


def _build_prematch_for_match(
    match: MatchRecord,
    fetch_prematch_api: bool = False,
    raw_fixture: dict | None = None,
    manual_lineups: str | None = None,
    manual_injuries: str | None = None,
    manual_strengths: str | None = None,
) -> PrematchDataSnapshot:
    """Build prematch snapshot with Sportmonks API as primary source."""
    if fetch_prematch_api:
        try:
            fid = int(match.provider_match_id) if match.provider_match_id.isdigit() else None
        except (ValueError, AttributeError):
            fid = None
        # Extract team IDs from raw fixture payload
        hid = None
        aid = None
        if raw_fixture:
            for p in raw_fixture.get("participants", []):
                meta = p.get("meta", {})
                if isinstance(meta, dict):
                    if meta.get("location") == "home":
                        hid = p.get("id")
                    elif meta.get("location") == "away":
                        aid = p.get("id")
        return build_sportmonks_prematch_snapshot(
            match, fixture_id=fid,
            home_team_id=hid, away_team_id=aid,
            manual_lineups_path=manual_lineups,
            manual_injuries_path=manual_injuries,
            manual_strengths_path=manual_strengths,
        )
    return build_prematch_snapshot(
        match,
        manual_lineups_path=manual_lineups,
        manual_injuries_path=manual_injuries,
        manual_strengths_path=manual_strengths,
    )


def _resolve_calibration(
    args, league_ids,
) -> tuple[float | None, "LearnedTotalGoalsCalibrator | None"]:
    """Resolve goal calibration: (global_multiplier, grouped_calibrator).

    Priority: explicit --goal-multiplier > learned-grouped > learned-global > none.
    """
    if args.goal_multiplier is not None:
        return args.goal_multiplier, None

    if args.learn_goal_calibration:
        grouped = _learn_grouped_calibrator(args.from_date, league_ids)
        if grouped is not None:
            print(f"Learned grouped calibration ({len(grouped.multiplier_map())} groups)")
            return None, grouped

        mlt = _learn_multiplier_from_warmup(args.from_date, league_ids)
        if mlt is not None:
            print(f"Learned global multiplier: {mlt:.3f}")
            return mlt, None

    return None, None


def _learn_grouped_calibrator(
    from_date_str: str,
    league_ids: list[int] | None,
) -> "LearnedTotalGoalsCalibrator | None":
    """Fit a LearnedTotalGoalsCalibrator on warmup matches."""
    from_date = datetime.fromisoformat(from_date_str)
    warmup_start = from_date - timedelta(days=WARMUP_DAYS)

    warmup_matches = fetch_matches(
        warmup_start.strftime("%Y-%m-%d"),
        from_date_str,
        league_ids=league_ids,
    )
    if len(warmup_matches) < 30:
        return None

    dc_model = _base_dc_model()

    def predict_fn(m):
        return dc_model.predict_score_matrix(m.home_team, m.away_team, neutral=m.neutral)

    cal = LearnedTotalGoalsCalibrator(TotalGoalsCalibrationConfig())
    cal.fit(warmup_matches, predict_fn)
    if len(cal.multiplier_map()) == 0:
        return None
    return cal


def main():
    parser = argparse.ArgumentParser(
        description="Historical backtest: Sportmonks fixtures 鈫?model 鈫?evaluation"
    )
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument("--league", type=int, nargs="*")
    parser.add_argument("--model", choices=["dc", "dynamic-dc", "market"], default="dc",
                        help="Model: dc | dynamic-dc | market (baseline)")
    parser.add_argument("--compare", action="store_true",
                        help="Run all models and show comparison table")
    parser.add_argument("--goal-multiplier", type=float, default=None,
                        help="Override lambda_multiplier for total-goals calibration (e.g. 1.15)")
    parser.add_argument("--learn-goal-calibration", action="store_true",
                        help="Learn goal multiplier from warmup data (overridden by --goal-multiplier)")
    parser.add_argument("--use-lineup-signals", action="store_true",
                        help="Apply lineup/injury lambda adjustments (uses empty signals if no data)")
    parser.add_argument("--fetch-prematch-api", action="store_true",
                        help="Fetch lineup/strength data from Sportmonks API (falls back to manual JSON)")
    parser.add_argument("--manual-lineups", help="Path to manual lineups JSON")
    parser.add_argument("--manual-injuries", help="Path to manual injuries JSON")
    parser.add_argument("--manual-strengths", help="Path to manual player strengths JSON")
    parser.add_argument("--tier", choices=["basic", "vip"], default="vip")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--print-matches", action="store_true")
    args = parser.parse_args()

    league_ids = args.league if args.league else None

    # -- Compare mode: run both and show side-by-side ---------------------------
    if args.compare:
        _run_compare(args, league_ids)
        return

    # -- Single model mode ------------------------------------------------------
    gm, grouped_cal = _resolve_calibration(args, league_ids)
    if args.model == "dynamic-dc":
        _run_dynamic_backtest(args, league_ids, goal_multiplier=gm,
                              learned_calibrator=grouped_cal,
                              use_lineup_signals=args.use_lineup_signals,
                              manual_lineups=args.manual_lineups,
                              manual_injuries=args.manual_injuries,
                              manual_strengths=args.manual_strengths,
                              fetch_prematch_api=args.fetch_prematch_api)
    elif args.model == "market":
        _run_market_backtest(args, league_ids)
    else:
        _run_static_backtest(args, league_ids, goal_multiplier=gm)


def _run_market_backtest(args, league_ids):
    print(f"Model: MARKET baseline (de-vigged odds)")
    print(f"Fetching fixtures {args.from_date} 鈫?{args.to_date} ...")
    matches = fetch_matches(args.from_date, args.to_date, league_ids=league_ids)

    if not matches:
        print("No finished fixtures found.")
        return

    raw_fixtures = get_fixtures_between(
        datetime.fromisoformat(args.from_date),
        datetime.fromisoformat(args.to_date),
        league_ids=league_ids,
        includes="participants;scores;league",
    )
    raw_by_id = {r["id"]: r for r in raw_fixtures}

    fixtures = []
    for m in matches:
        raw = raw_by_id.get(int(m.provider_match_id), {})
        fixtures.append(_to_backtest_fixture(m, raw))

    print(f"  {len(fixtures)} fixtures loaded\n")
    print("Running market baseline...")
    run = run_backtest_market(fixtures)
    _print_report(run)

    if args.print_matches:
        for result in run.results:
            p = result.snapshot.final_probs
            print(
                f"  {result.snapshot.match_id:<40} "
                f"H={p['home']:.2f} D={p['draw']:.2f} A={p['away']:.2f} "
                f"鈫?actual={result.actual_outcome} "
                f"({result.actual_home_goals}-{result.actual_away_goals})"
            )

    path = save_backtest_run(run, args.output, tier=args.tier)
    print(f"Saved backtest to {path}")


def _run_static_backtest(args, league_ids, goal_multiplier: float | None = None):
    gm_label = f" (goal_mlt={goal_multiplier})" if goal_multiplier else ""
    print(f"Model: STATIC Dixon-Coles{gm_label}")
    print(f"Fetching fixtures {args.from_date} 鈫?{args.to_date} ...")
    matches = fetch_matches(args.from_date, args.to_date, league_ids=league_ids)

    if not matches:
        print("No finished fixtures found.")
        return

    raw_fixtures = get_fixtures_between(
        datetime.fromisoformat(args.from_date),
        datetime.fromisoformat(args.to_date),
        league_ids=league_ids,
        includes="participants;scores;league",
    )
    raw_by_id = {r["id"]: r for r in raw_fixtures}

    fixtures = []
    for m in matches:
        raw = raw_by_id.get(int(m.provider_match_id), {})
        fixtures.append(_to_backtest_fixture(m, raw))

    print(f"  {len(fixtures)} fixtures loaded\n")
    print("Running backtest...")
    run = run_backtest_static(fixtures, goal_multiplier=goal_multiplier)
    _print_report(run)

    if args.print_matches:
        for result in run.results:
            p = result.snapshot.final_probs
            print(
                f"  {result.snapshot.match_id:<40} "
                f"H={p['home']:.2f} D={p['draw']:.2f} A={p['away']:.2f} "
                f"鈫?actual={result.actual_outcome} "
                f"({result.actual_home_goals}-{result.actual_away_goals})"
            )

    path = save_backtest_run(run, args.output, tier=args.tier)
    print(f"Saved backtest to {path}")


def _run_dynamic_backtest(
    args, league_ids,
    goal_multiplier: float | None = None,
    learned_calibrator=None,
    use_lineup_signals: bool = False,
    manual_lineups: str | None = None,
    manual_injuries: str | None = None,
    manual_strengths: str | None = None,
    fetch_prematch_api: bool = False,
):
    if learned_calibrator is not None:
        gm_label = " (grouped calibration)"
    elif goal_multiplier:
        gm_label = f" (goal_mlt={goal_multiplier})"
    else:
        gm_label = ""
    if use_lineup_signals:
        gm_label += " + lineup"
    print(f"Model: DYNAMIC Dixon-Coles (walk-forward){gm_label}")
    from_date = datetime.fromisoformat(args.from_date)
    to_date = datetime.fromisoformat(args.to_date)
    warmup_start = from_date - timedelta(days=WARMUP_DAYS)

    # Fetch warmup matches
    print(f"Fetching warmup fixtures {warmup_start.strftime('%Y-%m-%d')} 鈫?{args.from_date} ...")
    warmup_matches = fetch_matches(
        warmup_start.strftime("%Y-%m-%d"),
        args.from_date,
        league_ids=league_ids,
    )
    print(f"  {len(warmup_matches)} warmup matches")

    # Fetch test window matches
    print(f"Fetching test fixtures {args.from_date} 鈫?{args.to_date} ...")
    test_matches = fetch_matches(args.from_date, args.to_date, league_ids=league_ids)

    if not test_matches:
        print("No finished test fixtures found.")
        return

    raw_fixtures = get_fixtures_between(
        from_date, to_date,
        league_ids=league_ids,
        includes="participants;scores;league",
    )
    raw_by_id = {r["id"]: r for r in raw_fixtures}

    fixtures = []
    for m in test_matches:
        raw = raw_by_id.get(int(m.provider_match_id), {})
        fixtures.append(_to_backtest_fixture(m, raw))

    print(f"  {len(fixtures)} test fixtures loaded\n")
    print("Running walk-forward backtest...")
    run = run_backtest_dynamic(
        fixtures, warmup_matches, args.from_date,
        goal_multiplier=goal_multiplier,
        learned_calibrator=learned_calibrator,
        use_lineup_signals=use_lineup_signals,
        manual_lineups=manual_lineups,
        manual_injuries=manual_injuries,
        manual_strengths=manual_strengths,
        fetch_prematch_api=fetch_prematch_api,
    )
    _print_report(run)

    if args.print_matches:
        for result in run.results:
            p = result.snapshot.final_probs
            print(
                f"  {result.snapshot.match_id:<40} "
                f"H={p['home']:.2f} D={p['draw']:.2f} A={p['away']:.2f} "
                f"鈫?actual={result.actual_outcome} "
                f"({result.actual_home_goals}-{result.actual_away_goals})"
            )

    path = save_backtest_run(run, args.output, tier=args.tier)
    print(f"Saved backtest to {path}")


def _run_compare(args, league_ids):
    gm, grouped_cal = _resolve_calibration(args, league_ids)
    has_cal = (gm is not None and gm != 1.0) or grouped_cal is not None
    cal_label = f" + calibration" if has_cal else ""
    print(f"Compare mode: dc | dyn-pure | market | dyn+market{cal_label}\n")

    from_date = datetime.fromisoformat(args.from_date)
    to_date = datetime.fromisoformat(args.to_date)

    # Shared test fixtures
    test_matches = fetch_matches(args.from_date, args.to_date, league_ids=league_ids)
    if not test_matches:
        print("No finished test fixtures found.")
        return

    raw_fixtures = get_fixtures_between(
        from_date, to_date,
        league_ids=league_ids,
        includes="participants;scores;league",
    )
    raw_by_id = {r["id"]: r for r in raw_fixtures}

    fixtures = []
    for m in test_matches:
        raw = raw_by_id.get(int(m.provider_match_id), {})
        fixtures.append(_to_backtest_fixture(m, raw))

    runs: list[HistoricalBacktestRun] = []

    # 1. Static DC (no fusion 鈥?pure model, rename for display)
    print("Running static DC (pure model)...")
    run_dc = run_backtest_static(_strip_odds(fixtures))
    run_dc = HistoricalBacktestRun(
        model_version="dc-pure",
        data_version=run_dc.data_version,
        generated_at=run_dc.generated_at,
        results=run_dc.results,
        report=run_dc.report,
    )
    runs.append(run_dc)

    # 2. Dynamic DC (no fusion 鈥?pure model)
    warmup_start = from_date - timedelta(days=WARMUP_DAYS)
    print(f"Fetching warmup matches for dynamic DC...")
    warmup_matches = fetch_matches(
        warmup_start.strftime("%Y-%m-%d"),
        args.from_date,
        league_ids=league_ids,
    )
    print(f"  {len(warmup_matches)} warmup matches")
    print("Running dynamic DC (pure model)...")
    run_dyn = run_backtest_dynamic(_strip_odds(fixtures), warmup_matches, args.from_date,
                                    manual_lineups=args.manual_lineups,
                                    manual_injuries=args.manual_injuries,
                                    manual_strengths=args.manual_strengths,
                                    fetch_prematch_api=args.fetch_prematch_api)
    run_dyn = HistoricalBacktestRun(
        model_version="dyn-pure",
        data_version=run_dyn.data_version,
        generated_at=run_dyn.generated_at,
        results=run_dyn.results,
        report=run_dyn.report,
    )
    runs.append(run_dyn)

    # 3. Market baseline
    print("Running market baseline...")
    run_mkt = run_backtest_market(fixtures)
    runs.append(run_mkt)

    # 4. Dynamic DC + market fusion
    print("Running dynamic DC + market fusion...")
    run_dyn_mkt = run_backtest_dynamic(fixtures, warmup_matches, args.from_date,
                                        manual_lineups=args.manual_lineups,
                                        manual_injuries=args.manual_injuries,
                                        manual_strengths=args.manual_strengths,
                                        fetch_prematch_api=args.fetch_prematch_api)
    runs.append(run_dyn_mkt)

    # 5. Dynamic DC + market + calibration (grouped or global)
    if has_cal:
        if grouped_cal is not None:
            label = "dyn+mkt+grouped-cal"
        else:
            label = f"dyn+mkt+cal-{gm:.3f}"
        print(f"Running {label} ...")
        run_dyn_mkt_cal = run_backtest_dynamic(
            fixtures, warmup_matches, args.from_date,
            goal_multiplier=gm,
            learned_calibrator=grouped_cal,
            manual_lineups=args.manual_lineups,
            manual_injuries=args.manual_injuries,
            manual_strengths=args.manual_strengths,
            fetch_prematch_api=args.fetch_prematch_api,
        )
        run_dyn_mkt_cal = HistoricalBacktestRun(
            model_version=label,
            data_version=run_dyn_mkt_cal.data_version,
            generated_at=run_dyn_mkt_cal.generated_at,
            results=run_dyn_mkt_cal.results,
            report=run_dyn_mkt_cal.report,
        )
        runs.append(run_dyn_mkt_cal)

    # 6. Dynamic DC + market + calibration + lineup (if --use-lineup-signals)
    if args.use_lineup_signals:
        label = "dyn+mkt+cal+lineup"
        print(f"Running {label} ...")
        run_dyn_mkt_cal_lu = run_backtest_dynamic(
            fixtures, warmup_matches, args.from_date,
            goal_multiplier=gm,
            learned_calibrator=grouped_cal,
            use_lineup_signals=True,
            manual_lineups=args.manual_lineups,
            manual_injuries=args.manual_injuries,
            manual_strengths=args.manual_strengths,
            fetch_prematch_api=args.fetch_prematch_api,
        )
        run_dyn_mkt_cal_lu = HistoricalBacktestRun(
            model_version=label,
            data_version=run_dyn_mkt_cal_lu.data_version,
            generated_at=run_dyn_mkt_cal_lu.generated_at,
            results=run_dyn_mkt_cal_lu.results,
            report=run_dyn_mkt_cal_lu.report,
        )
        runs.append(run_dyn_mkt_cal_lu)

    # Print comparison
    _print_comparison(runs)

    # Save all
    for r in runs:
        p = save_backtest_run(r, tier=args.tier)
        print(f"Saved [{r.model_version}]: {p}")


if __name__ == "__main__":
    main()

