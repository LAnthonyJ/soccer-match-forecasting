<!-- encoding: UTF-8; Windows PowerShell: use `Get-Content -Encoding UTF8` or `rg` to read -->
# Step 1: Sportmonks 付费数据源接入

> 审核文档 — 提交给 reviewer agent 的完整交付说明

---

## 1. 目标

将 `src/data/provider_schema.py` 中的 `ProviderAdapter` 抽象接口变成真正的 Sportmonks API 适配器，打通 **赛程、赔率、首发、伤停、球员数据** 五大数据维度，使模型能消费真实付费数据源，替代此前仅依赖本地 CSV 的状态。

---

## 2. 新增文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/data/sportmonks_client.py` | ~270 | API v3 底层客户端：认证、缓存、限流、分页 |
| `src/data/sportmonks_adapter.py` | ~270 | `ProviderAdapter` 实现：Sportmonks JSON → 规范 Record |
| `src/data/sportmonks_ingestion.py` | ~360 | 数据摄入管道：编排拉取逻辑 + 构建模型上下文 |
| `src/predict_production_live.py` | ~200 | 生产预测 CLI：Sportmonks → 模型 → 快照 JSON |
| `tests/test_sportmonks_adapter.py` | ~180 | 12 个单元测试（纯逻辑，不调真实 API） |
| `.gitignore` | 12 | 排除 .env、缓存、pkl 模型文件 |

修改 1 个已有文件：

| 文件 | 修改内容 |
|------|---------|
| `src/data/__init__.py` | 添加模块 docstring（原文件为空） |

---

## 3. 架构设计

```
                        ┌──────────────────────┐
                        │  predict_production   │
                        │  _live.py (CLI)        │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │  SportmonksIngestion  │  ← 编排层
                        │  (sportmonks_         │
                        │   ingestion.py)        │
                        └──────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼──────┐  ┌─────────▼──────┐  ┌─────────▼──────┐
    │ Sportmonks     │  │ Sportmonks     │  │ MatchRecord     │
    │ Adapter        │  │ Client         │  │ OddsSnapshot    │
    │ (JSON→Record)  │  │ (HTTP+cache)   │  │ LineupPlayer..  │
    └────────────────┘  └────────┬───────┘  │ InjuryRecord    │
                                 │          └─────────────────┘
                                 │
                      ┌──────────▼───────────┐
                      │  Sportmonks API v3    │
                      │  api.sportmonks.com   │
                      │  (Base: Pro + Int'l   │
                      │   Tournaments plan)   │
                      └──────────────────────┘
```

三层分离：
- **Client** 只负责 HTTP 通信、JSON 缓存、限流计数
- **Adapter** 只负责数据转换，不调网络
- **Ingestion** 编排调用顺序，产出 `IngestionResult` 或 prediction-ready dict

---

## 4. 各文件详细说明

### 4.1 `sportmonks_client.py` — API 客户端

**核心函数：**

```python
_get(endpoint, params, cache_ttl_min, timeout) → dict
```
- 认证：HTTP Header `Authorization: <SPORTMONKS_API_KEY>`
- 缓存：MD5(endpoint+params) → `data/processed/sportmonks_cache/<hash>.json`
- 限流：全局 `_request_count` 计数器，接近 2000/天 时抛异常
- 429 处理：sleep 65s 后重试
- 代理绕过：`session.trust_env = False`（解决企业代理导致的 SSL 超时）

**对外暴露的函数：**

| 函数 | 端点 | 缓存 TTL | 说明 |
|------|------|----------|------|
| `get_fixture(id, includes)` | `/fixtures/{id}` | 10min | 单场详情 |
| `get_fixtures_between(from, to, ...)` | `/fixtures/between/{}/{}/` | 10min | 日期范围，自动分页 |
| `get_fixture_odds_1x2(id)` | `/fixtures/{id}?include=odds` | 10min | 仅提取 market_id=1，按 bookmaker 分组 |
| `get_squad(team_id)` | `/squads/teams/{id}` | 6h | 当前球队阵容 |
| `get_player(player_id)` | `/players/{id}` | 24h | 球员详情（含 DOB） |
| `get_team(team_id)` | `/teams/{id}` | 24h | 球队元信息 |
| `get_national_teams()` | `/teams` | — | 过滤 type=national |
| `get_expected_lineups(fixture_id)` | `/expected-lineups/fixtures/{id}` | 30min | 预计首发 |
| `get_bookmaker_name(id)` | `/bookmakers/{id}` | 7d | 博彩公司名称 |
| `paginate(endpoint, params)` | 通用 | — | 自动翻页收集所有结果 |
| `batch_get_players(ids)` | 批量 `/players/{id}` | — | 批量查球员 |

**位置映射：**
```python
POSITION_MAP = {24: "GK", 25: "DF", 26: "MF", 27: "MF", 28: "FW", 29: "FW", 30: "DF", 31: "DF", 32: "MF"}
```

### 4.2 `sportmonks_adapter.py` — 数据适配器

继承 `ProviderAdapter`（`provider_schema.py`），实现四个解析方法：

**`parse_match(payload) → MatchRecord`**
- 从 `participants` 数组提取主客队（按 `meta.location` 匹配）
- 从 `scores` 提取 `CURRENT` (type_id=1525) 作为终场比分
- `state_id` → status 映射：1/2=scheduled, 3/4=live, 5=finished, 7/8/9=取消/推迟
- 队名规范化：`Congo DR→DR Congo`, `Türkiye→Turkey` 等，对齐 `results.csv` 的训练数据命名

**`parse_odds_snapshot(payload) → OddsSnapshot`**

单条 1X2 赔率转换，含 bookmaker 标识和时间戳。

**`fetch_odds_snapshots(fixture_id) → list[OddsSnapshot]`**

实际调用 client 获取所有 bookmaker 的 1X2 赔率，每人一条 snapshot。

**`average_odds(fixture_id) → tuple[float,float,float] | None`**

21 家 bookmaker 的三项赔率简单平均，供市场先验融合使用。

**`parse_lineup_player(payload) → LineupPlayerRecord`**

Squad 条目 + 球员详情 → 规范记录，含 `position_code()` 位置映射。

**`fetch_squad_players(team_id, team_name) → list[LineupPlayerRecord]`**

拉取整队 squad 并注入球员姓名。

**`fetch_expected_lineup_players(fixture_id, ...) → list[LineupPlayerRecord]`**

拉取赛前预计首发（依赖 Expected Lineups add-on）。

**`parse_injury(payload) → InjuryRecord`**

Sportmonks 无专门伤停端点，通过 squad `end` 日期（球员离队时间）作为不可用代理。

### 4.3 `sportmonks_ingestion.py` — 摄入管道

**`SportmonksIngestion` 类：**

| 方法 | 功能 |
|------|------|
| `ingest_tournament(league_id, ...)` | 拉单个赛事全部 fixture + odds |
| `ingest_upcoming(days_ahead)` | 未来 N 天国际比赛 |
| `ingest_recent_results(days_back)` | 近期已完赛比赛 |
| `ingest_prediction_ready(fixture_id)` | **核心方法**：拉一场比赛的全部预测所需数据 |
| `ingest_fixture_batch(fixture_ids)` | 批量版本 |
| `export_to_json(result, filename)` | 导出 `IngestionResult` 为 JSON |

**`ingest_prediction_ready()` 返回的 dict 结构：**
```python
{
    "match": MatchRecord(...),
    "odds": (home_avg, draw_avg, away_avg),  # 或 None
    "context": {  # 喂给 DynamicDixonColesService
        "home_attack_delta": 0.05,   # squad 深度优势
        "away_lineup_delta": 0.015,  # 经验差异
    },
    "quality": {  # 直接传给 PredictionInputQuality
        "team_sample_home": 26,
        "team_sample_away": 26,
        "has_lineups": True,
        "has_injuries": False,
        "odds_snapshots": 21,
        "odds_volatility": 0.185,
    },
    "home_squad_size": 26,
    "away_squad_size": 26,
}
```

**Context 特征构建逻辑 (`_build_match_context`)：**

1. **Squad 深度差异**：如果主队 squad 比客队大 15%+，给 `home_attack_delta` (max 0.10)；反过来给 `away_attack_delta`
2. **经验差异**：取每队前 5 名球员的年龄均值，差距 >1.5 岁时给 `home_lineup_delta` 或 `away_lineup_delta` (max 0.08)。球员数据走缓存，不重复调 API

**赔率波动估计 (`_estimate_odds_volatility`)：**

计算跨 bookmaker 的 home odds 变异系数 (CV)，scale 后 clip 到 [0,1]，作为输入质量因子。

**已注册的国际赛事 league IDs：**
```python
INTERNATIONAL_LEAGUES = {
    732: "World Cup",          714: "WC Qualification Asia",
    717: "WC Qualification Concacaf",  720: "WC Qualification Europe",
    723: "WC Qualification Oceania",   726: "WC Qualification South America",
    711: "CAF World Cup Qualifiers",   729: "WC Qualification Intercontinental Playoffs",
    1105: "AFC Asian Cup",     1114: "Copa America",
    1117: "Africa Cup of Nations",     1118: "AFCON Qualifications",
    1325: "Euro Qualification",        1326: "European Championship",
    1538: "UEFA Nations League",       1082: "Friendly International",
    1103: "African Nations Championship", 1112: "CONCACAF Gold Cup",
    1124: "OFC Nations Cup",   1125: "Olympic Games",
}
```

### 4.4 `predict_production_live.py` — 生产预测 CLI

**CLI 参数：**

```bash
# 单场预测
python src/predict_production_live.py --fixture-id 19609197 [--tier vip] [--output path.json]

# 未来 N 天所有国际比赛
python src/predict_production_live.py --upcoming --days 7

# 指定日期范围（用于回测验证）
python src/predict_production_live.py --range --from 2026-06-01 --to 2026-06-30
```

**数据流：**
```
SportmonksIngestion.ingest_prediction_ready(fixture_id)
    → MatchRecord, odds tuple, context dict, quality dict
        → ProductionPredictionEngine.predict(...)
            → DynamicDixonColesService.predict_score_matrix(...)
                → base DC model + context deltas
            → TotalGoalsCalibrator.apply(score_matrix)
            → normalize_three_way_odds(odds) → market_probs
            → confidence_from_inputs(quality) → confidence_weight
            → blend_with_market(model_probs, market_probs, weight)
            → build_prediction_snapshot(...)
                → PredictionSnapshot.to_public_dict(tier)
```

输出 JSON 包含 `_meta` 扩展字段（tournament、kickoff_utc、status、实际比分），便于后续回测对比。

### 4.5 `tests/test_sportmonks_adapter.py` — 单元测试

12 个测试，全部通过（纯逻辑，mock 数据，不调真实 API）：

| 测试类 | 测试 | 验证内容 |
|--------|------|---------|
| `SportmonksAdapterTests` | `test_parse_match_extracts_fields_correctly` | fixture → MatchRecord 全字段 |
| | `test_parse_match_scheduled_status` | state_id=1 → "scheduled" |
| | `test_parse_odds_snapshot_converts_properly` | 1X2 odds → OddsSnapshot |
| | `test_parse_lineup_player_maps_position` | position_id=27 → "MF" |
| | `test_parse_injury_basic` | 伤停条目 → InjuryRecord |
| | `test_normalize_team_name` | Congo DR→DR Congo, Türkiye→Turkey |
| | `test_extract_final_score` | CURRENT type_id=1525 → (3, 3) |
| | `test_extract_final_score_empty` | 空 scores → (None, None) |
| | `test_find_participant` | meta.location → home/away 对应 |
| `IngestionPipelineTests` | `test_build_match_context_empty_squads` | 空 squad → {} |
| | `test_build_match_context_squad_imbalance` | 20 vs 10 → home_attack_delta > 0 |
| | `test_build_match_context_equal_squads` | 等量 → 无 delta |

---

## 5. 已验证的端到端流程

真实 API 调用测试 (fixture 19609197, Algeria vs Austria, WC 2026)：

```
Sportmonks API → 21家 bookmaker 1X2 平均赔率 H=3.41 D=2.16 A=3.40
    → DC 比分矩阵 → 进球校准 (×1.10)
    → 模型概率 H=30.0% D=33.5% A=36.5%
    → 去水市场概率 H=27.9% D=44.1% A=28.0%
    → 置信度 64% (基于 squad 深度 + odds 深度 + 低波动)
    → 融合概率 (64% 模型 + 36% 市场)
    → VIP 快照 JSON (含 xG, Over 2.5, BTTS, Top-15 比分, 热度图, market_edge)
```

实际比分 3-3，模型预测 Over 2.5=51.0% 命中，但 3-3 不在 Top-15 比分中（罕见高分平局）。

---

## 6. 环境依赖

- `SPORTMONKS_API_KEY` 环境变量（已配置）
- Python 依赖：`requests`（已有）
- 无新增 pip 依赖

缓存目录：`data/processed/sportmonks_cache/`（已在 .gitignore 中排除）

---

## 7. 已知限制

1. **伤停数据**：Sportmonks 无专门 injury endpoint，当前通过 squad end-date 做不可用代理，精度有限。建议后续对接专门的 injury API 或 news scraping
2. **Expected Lineups**：仅在赛前 1-2 小时可用，且依赖 add-on 订阅
3. **世界杯权限**：当前 "International Tournaments" 计划可读取 WC 2026 的 fixture/odds/squad，但无法确认是否所有 WC 数据都在订阅范围内
4. **球员年龄查询**：`_average_squad_age()` 仅查前 5 名球员以控制 API 调用量，做粗略的经验代理。如需精确的 squad 估值，应通过 transfermarkt 或专门的数据源补充
5. **队名对齐**：`TEAM_NAME_NORMALIZE` 目前手动维护 ~6 组映射，后续如需支持全部 200+ 国家队可能需要更系统的 fuzzy matching

---

## 8. 测试结果

```
Ran 20 tests in 0.004s — OK

包含：
  - 8 个原有 Phase 7 测试（market_fusion, dynamic_dc, total_goals, prediction_snapshot, production_prediction）
  - 12 个新增 adapter / ingestion 测试
```

---

## 9. 下一步

按原计划优先级：

1. ✅ **接真实付费数据源** — 本文档交付
2. **统一回测 runner** — 按时间切分回放历史预测，验证新模型 vs 旧 DC
3. **Web 接入** — 把 Streamlit 切到 `ProductionPredictionEngine`，直接消费快照 JSON
4. **数据库层** — SQLite/Postgres 表持久化 prediction_snapshots, odds_snapshots, model_runs
5. **阵容/伤停特征** — 丰富 `_build_match_context` 的 context delta 特征
