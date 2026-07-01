# Prediction API Contract

Stable field specification for Basic and VIP prediction responses.
Version: 2.0 | Model: dynamic-dc-v1

All responses use the standard envelope:
```json
{
  "ok": true,
  "data": { ... },
  "meta": { "request_id": "req_...", "source": "cache", "tier": "vip" }
}
```

---

## Basic Tier

Always safe for public display.

```json
{
  "ok": true,
  "data": {
    "match_id": "sportmonks-19606960",
    "home_team": "South Africa",
    "away_team": "Canada",
    "model_version": "dynamic-dc-v1",
    "probabilities": {"home": 0.24, "draw": 0.31, "away": 0.45},
    "top_scorelines": [[1,1,0.13], [0,1,0.12], [0,0,0.11]],
    "confidence": 0.57,
    "lineup_data_status": "none",
    "data_updated_at": "2026-06-28T12:00:00+00:00"
  },
  "meta": {
    "request_id": "req_abc",
    "source": "cache",
    "tier": "free",
    "generated_at": "2026-06-28T12:00:00+00:00"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| match_id | string | Canonical match identifier |
| home_team | string | Home team name |
| away_team | string | Away team name |
| model_version | string | Model version that produced this prediction |
| data_version | string | Data source version identifier |
| generated_at | ISO 8601 | Prediction generation timestamp |
| confidence | float 0-1 | Model confidence level |
| probabilities | object | {home, draw, away} win probabilities, sum = 1.0 |
| top_scorelines | array | Top 3 scorelines as [[home, away, prob], ...] |
| expected_goals | object | {home, away} expected goals |
| lineup_data_status | string | none \| partial \| expected \| confirmed |
| data_updated_at | ISO 8601 \| null | Last data refresh timestamp |

`top_scorelines` is truncated to 3 entries in Basic.

---

## VIP Tier

Includes all Basic fields plus premium analysis fields.

```json
{
  "... all Basic fields ...": "...",
  "top_scorelines": [[1,1,0.13], ..., [3,2,0.01]],
  "over25": 0.53,
  "btts": 0.49,
  "score_heatmap": [[0.05, 0.04, ...], ...],
  "market_probs": {"home": 0.20, "draw": 0.28, "away": 0.52},
  "market_edge": {"home": 0.04, "draw": 0.03, "away": -0.07},
  "lineup_adjustment": {"home_lineup_delta": -0.03, "away_lineup_delta": 0.01},
  "missing_key_players": ["home missing: Player A"],
  "prematch_snapshot_id": "sportmonks-19606960_20260628T120000",
  "model_pipeline": {
    "base_model": "dynamic-dc-v1",
    "market_fusion": true,
    "goal_calibration": true,
    "goal_multiplier": 1.10,
    "lineup_adjustment": false,
    "model_version": "dynamic-dc-v1"
  }
}
```

| Additional VIP field | Type | Description |
|---------------------|------|-------------|
| top_scorelines | array | Full 15 scorelines |
| over25 | float 0-1 | Probability over 2.5 goals |
| btts | float 0-1 | Probability both teams score |
| score_heatmap | 10x10 array | Full score probability matrix |
| market_probs | object \| null | De-vigged market implied probabilities |
| market_edge | object \| null | Model - market probability difference (analysis only) |
| lineup_adjustment | object \| null | Lambda deltas from lineup/injury signals |
| missing_key_players | array \| null | Explanation list of missing/doubtful players |
| prematch_snapshot_id | string \| null | Links to the pre-match data used |
| model_pipeline | object \| null | Full pipeline composition for audit |

---

## Fallback behavior

| Failure | Effect |
|---------|--------|
| Odds unavailable | market_probs=null, market_edge=null, model-only prediction |
| Lineup unavailable | lineup_data_status="none", lineup_adjustment=null |
| Injury data missing | has_injuries=False, no adjustment |
| Strength data missing | Default player importance scores used |
| Goal calibration fails | multiplier=1.0 |
| DC model load fails | Prediction unavailable (fatal) |

---

## Data quality levels

| lineup_data_status | Meaning |
|-------------------|---------|
| none | No lineup or injury data available |
| partial | Only injury/suspension data, no starting XI |
| expected | Expected starting XI from pre-match reports |
| confirmed | Official confirmed starting XI |

---

## Model version format

Current: `dynamic-dc-v1`
Pipeline: DC base + market fusion + goal calibration + optional lineup adjustment

---

## Caching

See `src/services/prediction_service.py` for cache helpers:
- `save_prediction_snapshot(snapshot, tier)` — persist to JSON
- `load_latest_prediction(match_id)` — load newest cached prediction
- `load_prediction_by_snapshot_id(snapshot_id)` — load specific snapshot
