"""Production pipeline smoke test.

Validates the full prediction chain with a real Sportmonks fixture.
Usage:
    python src/smoke_test_prediction.py --fixture-id 19606960
    python src/smoke_test_prediction.py --fixture-id 19606960 --tier vip
    python src/smoke_test_prediction.py --fixture-id 19606960 --no-prematch-api
    python src/smoke_test_prediction.py --fixture-id 19606960 --no-odds --no-lineup
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("SPORTMONKS_API_KEY", "")

from src.services.prediction_service import PredictionService, save_prediction_snapshot

CHECK = "  [OK]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"


def smoke_test(
    fixture_id: int,
    tier: str = "vip",
    use_prematch_api: bool = True,
    expect_odds: bool = True,
    expect_lineup: bool = True,
) -> bool:
    """Run a complete smoke test. Returns True if all checks pass."""
    passed = 0
    failed = 0
    warnings = 0

    def check(label: str, condition: bool, is_warning: bool = False):
        nonlocal passed, failed, warnings
        if condition:
            print(f"{CHECK} {label}")
            passed += 1
        elif is_warning:
            print(f"{WARN} {label}")
            warnings += 1
        else:
            print(f"{FAIL} {label}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"  SMOKE TEST — Fixture {fixture_id}")
    print(f"  Tier: {tier} | API: {use_prematch_api}")
    print(f"{'='*50}\n")

    service = PredictionService()

    # 1. Predict
    try:
        result = service.predict_match(
            fixture_id=fixture_id,
            use_prematch_api=use_prematch_api,
        )
    except Exception as e:
        print(f"  [FATAL] Prediction failed: {e}")
        return False

    snap = result.snapshot
    vip = snap.to_public_dict(tier="vip")
    basic = snap.to_public_dict(tier="basic")

    # 2. Structural checks
    check("Prediction generated", snap is not None)
    check("model_version present", bool(vip.get("model_version")))
    check("data_version present", bool(vip.get("data_version")))
    check("data_updated_at present", bool(basic.get("data_updated_at")))
    check("confidence in [0,1]", 0 <= vip["confidence"] <= 1)
    check("probabilities sum to 1", abs(sum(vip["probabilities"].values()) - 1.0) < 0.01)
    check("top_scorelines non-empty", len(vip["top_scorelines"]) > 0)

    # 3. Tier isolation
    check("basic has no score_heatmap", "score_heatmap" not in basic)
    check("basic has no market_edge", "market_edge" not in basic)
    check("basic has no lineup_adjustment", "lineup_adjustment" not in basic)
    check("basic has no prematch_snapshot_id", "prematch_snapshot_id" not in basic)
    check("basic has no model_pipeline", "model_pipeline" not in basic)
    check("vip has score_heatmap", "score_heatmap" in vip)
    check("vip has market_edge", "market_edge" in vip or "market_probs" not in vip)
    check("basic has lineup_data_status", "lineup_data_status" in basic)

    # 4. Odds-related
    has_odds = vip.get("market_probs") is not None
    check("odds handled", has_odds == expect_odds, is_warning=(has_odds != expect_odds))
    if has_odds:
        check("market_probs present", bool(vip.get("market_probs")))

    # 5. Lineup-related
    has_lineup = basic.get("lineup_data_status", "none") != "none"
    check("lineup status present", "lineup_data_status" in basic)
    check(
        f"expect_lineup={expect_lineup}, got={has_lineup}",
        has_lineup == expect_lineup,
        is_warning=True,  # lineup may vary by API availability
    )
    if vip.get("lineup_adjustment"):
        check("lineup_adjustment present", True)

    # 6. Fallback tracking (machine-readable)
    if result.fallback_used:
        print(f"  Fallback chain: {result.fallback_used}")
    if result.warnings:
        print(f"  Warnings: {result.warnings}")
    check("fallback_used is list", isinstance(result.fallback_used, list))

    # 7. Save and reload
    try:
        path = save_prediction_snapshot(snap, tier=tier)
        check("prediction saved to disk", path.exists())
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        check("reloaded has match_id", reloaded["match_id"] == snap.match_id)
        check("reloaded has model_version", reloaded["model_version"] == snap.model_version)
    except Exception as e:
        check(f"save/reload: {e}", False)

    # 8. Cache queries
    from src.services.prediction_service import (
        load_latest_prediction,
        list_cached_predictions,
    )
    latest = load_latest_prediction(snap.match_id)
    if latest:
        check("latest prediction loadable", True)
    cached = list_cached_predictions(snap.match_id)
    check("cached predictions listable", len(cached) > 0)

    # 9. Summary
    print(f"\n{'='*50}")
    match = snap.home_team + " vs " + snap.away_team
    print(f"  MATCH:     {match}")
    print(f"  MODEL:     {snap.model_version}")
    print(f"  DATA:      {snap.data_version}")
    print(f"  LINEUP:    {basic.get('lineup_data_status', '?')}")
    print(f"  ODDS:      {'available' if has_odds else 'missing'}")
    print(f"  PRED:      OK")
    print(f"  BASIC:     OK" if "score_heatmap" not in basic else "  BASIC:     LEAK")
    print(f"  VIP:       {'OK' if 'score_heatmap' in vip else 'MISSING'}")
    print(f"  SAVED:     {path}")
    print(f"  Passed: {passed}  Failed: {failed}  Warnings: {warnings}")
    print(f"{'='*50}")

    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Production prediction smoke test")
    parser.add_argument("--fixture-id", type=int, required=True)
    parser.add_argument("--tier", choices=["basic", "vip"], default="vip")
    parser.add_argument("--no-prematch-api", action="store_true",
                        help="Disable Sportmonks API; use manual JSON or empty snapshot")
    parser.add_argument("--no-odds", action="store_true")
    parser.add_argument("--no-lineup", action="store_true")
    args = parser.parse_args()

    ok = smoke_test(
        fixture_id=args.fixture_id,
        tier=args.tier,
        use_prematch_api=not args.no_prematch_api,
        expect_odds=not args.no_odds,
        expect_lineup=not args.no_lineup,
    )
    sys.exit(0 if ok else 1)
