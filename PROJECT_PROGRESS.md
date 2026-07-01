# Football AI Predictor — 项目进度文档

> 最后更新: 2026-06-28  
> 工作目录: `soccer_prediction module/`

---

## 项目概述

构建一个国家队足球比赛预测系统，目标是**击败博彩市场（Pinnacle）**的胜平负赔率。

核心技术栈：
- **ELO评分系统** — 基于FIFA认可的World Football ELO，按洲际设定初始值
- **XGBoost + LightGBM 集成模型** — 76维特征预测胜平负
- **Dixon-Coles 比分预测模型** — 经典足球比分泊松模型，MLE拟合
- **ML-Poisson 比分模型** — XGBoost回归预测预期进球
- **DeepSeek LLM** — 中文分析报告生成
- **Streamlit Web UI** — 交互式预测界面

数据来源（全部免费）：
- `results.csv` — 国际比赛历史（1872-2026，~50K场）
- FIFA Rankings CSV (Dato-Futbol)
- StatsBomb Open Data (事件数据/xG)
- OddsPortal (Pinnacle赔率，仅少量)
- transfermarkt (阵容/球员数据)
- 直播吧 (赛前新闻)

---

## 项目结构

```
soccer_prediction module/
├── data/
│   ├── raw/
│   │   ├── results.csv              # 49,477场国际比赛
│   │   ├── wc2026_results.csv       # WC 2026 72场小组赛完整结果
│   │   └── oddsportal/
│   │       └── matches.csv          # 377K场赔率数据
│   ├── processed/
│   │   ├── results_with_event_features.csv  # 合并了StatsBomb事件特征
│   │   ├── event_features.csv              # StatsBomb每场事件
│   │   ├── results_with_signals_master.csv # 赛前信号
│   │   └── results_with_matchup.csv        # 战术对阵
│   └── predictions/
│       └── wc2026_predictions.csv   # WC 2026预测追踪（18行：12已完成+6待定）
├── outputs/
│   └── models/
│       ├── full_model.pkl           # H/D/A集成模型 (XGB+LGB+ELO)
│       └── score_models.pkl         # 比分预测模型 (DixonColes+MLPoisson+Ensemble)
├── src/
│   ├── data/
│   │   ├── cleaner.py               # 数据清洗 (MIN_YEAR=2000, 队名规范化)
│   │   ├── align_transfermarkt.py   # transfermarkt数据对齐
│   │   └── build_kaggle_signals.py  # 射手数据信号提取
│   ├── features/
│   │   ├── elo.py                   # ELO评分系统 (洲际初始值, 统一泊松公式)
│   │   ├── build_features.py        # 特征工程编排器 (76维特征)
│   │   ├── extract_event_features.py # StatsBomb事件特征 (xG, PPDA等)
│   │   ├── style_matchup.py         # 连续风格对阵特征
│   │   ├── tactical_matchup.py      # 风格-vs-风格胜率
│   │   └── extract_signals.py       # 赛前信号提取
│   ├── models/
│   │   ├── train.py                 # H/D/A模型训练 (XGB+LGB+Ensemble)
│   │   ├── score_models.py          # 比分预测模型 (DixonColes, MLPoisson, Ensemble)
│   │   ├── score_evaluation.py      # 比分评估 (RPS, LogLoss, 校准)
│   │   ├── pre_match_adjuster.py    # 赛前信号校准框架
│   │   └── odds_model.py            # 赔率模型
│   ├── llm/
│   │   ├── explainer.py             # DeepSeek LLM解释器
│   │   ├── match_info_extractor.py  # 赛前信息提取 + 信号计算
│   │   └── pre_match_pipeline.py    # 赛前信息管道
│   ├── pipeline.py                  # 主训练管道
│   ├── pipeline_score.py            # 比分模型训练管道
│   ├── predict_today.py             # 每日预测脚本
│   ├── backtest_12_odds.py          # 12场真实赔率回测
│   ├── betting_backtest_dc.py       # Dixon-Coles投注策略回测 (70场)
│   ├── evaluate_wc2026.py           # WC 2026完整评估
│   ├── evaluate_full_wc2026.py      # 70场比分模型评估
│   └── evaluate_score_models.py     # 比分模型对比评估
├── web/
│   ├── app.py                       # Streamlit主应用 (4个Tab)
│   └── score_components.py          # 比分可视化组件 (Plotly)
├── venv/                            # Python 3.13虚拟环境
└── PROJECT_PROGRESS.md              # 本文档
```

---

## Phase 1-6: 已完成工作

### Phase 1: 数据源
- ✅ 国际比赛历史数据 (`results.csv`, 1872-2026)
- ✅ FIFA排名 (Dato-Futbol, 23,692/25,414场匹配)
- ✅ StatsBomb事件数据 (257场, xG/PPDA/传球/压迫等)
- ✅ transfermarkt阵容数据 (486场对齐, 信号提取)
- ✅ OddsPortal赔率数据 (199场有Pinnacle收盘赔率)
- ✅ 直播吧赛前新闻抓取管道

### Phase 2: 特征工程 (76维特征)
8大类特征：
1. **球队实力** (5): elo_home/away/diff, fifa_points_home/away
2. **近期状态** (12): form_gf/ga/gd/win_rate/points_per_game, opp_elo_avg, weighted_form, tournament_form
3. **交锋历史** (10): h2h_matches, win_pct, recent, competitive, goal_diff, same_venue
4. **赛事背景** (11): is_friendly/wc/wc_qualifier/continental/knockout, rest_days, match_seq, neutral_venue
5. **加权状态** (6): wgf/wga/wgd (指数衰减, 半衰12场)
6. **教练** (2): coach_win_rate (3年滚动胜率)
7. **StatsBomb事件** (24): xG, xG/shot, pass%, PPDA, direct_speed, def_line, pressures, prog_passes, 4风格分数
8. **战术对阵** (8): tactical_matchup_adv, matchup_*_diff (6个连续差异特征)

**时间衰减权重**: 半衰期3年, 下限10%  
**赛事重要性权重**: WC=5x, 洲际=3.5x, WCQ=2.5x

### Phase 3: 赛前信号框架
- ✅ `PreMatchAdjuster` — 13个信号校准
- ✅ 基于ELO基线的贝叶斯调整
- ✅ t检验显著性 (p<0.10, n≥20)
- ⚠️ 信号数据稀疏 (仅39-128场比赛), 实际调整效果有限
- ✅ Phase 3b: LLM赛前信息提取管道 (直播吧+DeepSeek)

### Phase 4: 模型训练 (XGBoost + LightGBM)
- ✅ 随机85/15训练/验证分割 (时间衰减+赛事权重)
- ✅ WC 2022作为独立测试集
- ✅ 时间衰减样本权重
- ✅ Isotonic概率校准
- ✅ Ensemble权重优化 (SLSQP, 按胜平负独立优化)
- ⚠️ 在WC 2026实战中表现不佳: LogLoss 0.829 vs 市场 0.716

### Phase 5: LLM解释 + Web UI
- ✅ DeepSeek API集成 (中文分析报告生成)
- ✅ 直播吧新闻抓取 + LLM结构化提取
- ✅ Streamlit Web UI (4 Tab: 预测/比分/面板/历史)
- ✅ 中文队名翻译 (ZH_TO_EN, ~80队)
- ✅ 投注建议 (Kelly准则, Edge≥10%阈值)

### Phase 6: 投注策略
- ✅ Edge ≥ 10% 阈值 (12场WC 2026验证: ROI +22% @ ≥10%)
- ✅ 1/4 Kelly准则
- ✅ 单注5%本金上限
- ⚠️ 策略风险: DC模型倾向于在冷门方向产生Edge信号

---

## Phase 7: 比分预测系统 (核心最新进展)

### 模型架构
三模型集成 → 完整比分分布:
```
Dixon-Coles (MLE, ~200参数) ─┐
ML-Poisson (XGBoost → λ)    ─┼→ ScoreEnsemble → P(比分=i,j)
xG-Poisson (StatsBomb校准)   ─┘                  ├→ 精确比分Top-15
                                                  ├→ Over/Under 2.5
                                                  └→ BTTS (双双进球)
```

### Dixon-Coles 模型详情
- **参数**: α_i (进攻), β_i (防守) per team, γ (主场优势), ρ (低比分相关)
- **公式**: λ_home = exp(γ + α_home + β_away), λ_away = exp(α_away + β_home)
- **ρ修正**: τ(0,0), τ(1,0), τ(0,1), τ(1,1) — 修正低比分依赖
- **训练**: 两阶段 — IRLS泊松GLM热身 + L-BFGS-B精调(γ, ρ)
- **数据**: 11,611场 (前100队), 时间权重半衰5年
- **学习参数**: γ=0.267, ρ=-0.0906
- **最强攻击**: Spain 0.854, Brazil 0.832, Germany 0.796
- **最强防守**: Argentina -0.822, Brazil -0.718, Spain -0.687

### WC 2026 70场回测结果

| 指标 | Dixon-Coles | ML-Poisson | ELO基线 | Ensemble |
|------|-----------|-----------|---------|----------|
| **RPS ↓** | **0.0565** | 0.0582 | 0.0599 | 0.0571 |
| **比分LogLoss ↓** | **2.859** | 3.209 | 3.216 | 3.010 |
| **H/D/A准确率** | **42/70 (60%)** | 33/70 (47%) | 35/70 (50%) | 36/70 (51%) |
| **O/U准确率** | **39/70 (56%)** | 34/70 (49%) | 28/70 (40%) | 38/70 (54%) |
| **BTTS准确率** | 40/70 (57%) | 37/70 (53%) | 33/70 (47%) | **42/70 (60%)** |

### 12场真实赔率回测

| 指标 | Dixon-Coles | Pinnacle市场 | 旧HDA Ensemble |
|------|-----------|-------------|---------------|
| **H/D/A LogLoss** | 0.767 | **0.716** | 0.829 |
| **H/D/A 准确率** | **9/12 (75%)** | 8/12 (67%) | 8/12 (67%) |
| **投注P&L** | **Y-62 (-36%)** | — | Y-179 (-100%) |

### 核心发现
1. **Dixon-Coles 全面碾压 ML-Poisson** — 简单参数模型泛化更好
2. **DC比分模型推导的H/D/A (60%准确率) > 专用HDA集成 (51%)** — 比分信号比胜平负标签更丰富
3. **DC与市场H/D/A LogLoss差距仅0.05** — 接近可投注水平
4. **ML-Poisson过拟合确认** — WC 2022验证集最优, WC 2026实战第三
5. **DC系统性低估总进球** — 预测均值42%, 实际59% Over

---

## 关键Bug修复 (最新)

### 1. 泊松公式统一
- **修复前**: train.py用`exp(0.15+0.0011*elo_diff)`, elo.py用`1.30+elo_diff/400*0.5`
- **修复后**: 统一为`exp(0.15 + 0.0011*elo_diff)`, 模块级常量`_ELO_LAMBDA_BASELINE_LOG=0.15`

### 2. 中立场地主客对称性
- **问题**: 世界杯无主客场, 但HDA Ensemble带训练数据中的主场偏差
- **修复**: 
  - 搜索逻辑: 先精确反向搜索 → 再模糊匹配
  - 反向匹配时交换全部76个方向性特征
  - DC模型完美对称 (已验证)
  - HDA Ensemble标注"仅供参考"

### 3. 队名映射
- Cleaner映射: `United States→USA`, `Czech Republic→Czechia`, `IR Iran→Iran`等
- Web ZH_TO_EN: ~80条中文映射 + `巴拿马→Panama`, `民主刚果→DR Congo`

### 4. Fallback预测
- 特征矩阵找不到的比赛 → 直接用Dixon-Coles (仅需队名)

---

## 当前投注策略

### 规则
| 参数 | 值 |
|------|-----|
| 最小Edge | ≥ 10% |
| Kelly比例 | 1/4 |
| 单注上限 | 5% 本金 |
| 本金 | Y1,000 |
| 投注市场 | 仅H/D/A (胜平负) |
| 信号来源 | Dixon-Coles vs Pinnacle |

### WC 2026追踪 (18场比赛)

**已完赛 (12场)**:
- 旧HDA Ensemble: 5注0胜, Y-179
- Dixon-Coles: 5注1胜, Y-62
- DC准确率: 9/12 (75%)

**待完赛 (6场, June 28)**:
- 乌兹别克斯坦胜 @ 5.30 | Edge +13.8% | Y39
- 奥地利胜 @ 3.00 | Edge +10.9% | Y23
- 其余4场 PASS

---

## 已知问题

1. **DC低估进球** — WC 2026是高分杯赛 (59% Over), DC仅预测42%
2. **平局Edge是假信号** — 8/12场最大Edge指向平局, 仅2场正确
3. **冷门偏好** — DC在冷门方向产生更多Edge ≥10%信号
4. **HDA Ensemble不对称** — XGB+LGB含训练数据主场偏差, 不适用中立比赛
5. **rho可能偏强** — -0.09比俱乐部足球预期值强, 可能高估平局概率
6. **小样本** — 12场真实赔率, 统计结论不可靠
7. **WC 2026结果来源不官方** — 从越南体育网站抓取, 可能有误差

---

## 下一步方向

1. **提高总进球预测** — 调高λ基线或使用Negative Binomial (厚尾)
2. **过滤平局Edge** — 只投H或A方向的Edge≥10%
3. **O/U 2.5投注** — DC在此市场有56%准确率, 值得独立开发
4. **重新训练DC** — 用WC 2026结果更新团队参数 (在线学习)
5. **淘汰赛预测** — 72场小组赛数据可重新校准模型
6. **Correlated Poisson** — 用Bivariate Poisson替代独立Poisson

---

## 快速启动

```bash
# 训练比分模型
python src/pipeline_score.py

# 预测今日比赛
python src/predict_today.py

# 回测70场WC 2026
python src/betting_backtest_dc.py

# 12场真实赔率回测
python src/backtest_12_odds.py

# 启动Web UI
streamlit run web/app.py --server.headless true

# 完整评估
python src/evaluate_full_wc2026.py
```

## 关键文件速查

| 想做什么 | 文件 |
|---------|------|
| 看模型训练逻辑 | `src/models/train.py` |
| 看Dixon-Coles实现 | `src/models/score_models.py` |
| 看特征工程 | `src/features/build_features.py` |
| 看Web UI | `web/app.py` |
| 看投注回测 | `src/backtest_12_odds.py` |
| 看WC 2026预测追踪 | `data/predictions/wc2026_predictions.csv` |
| 看ELO实现 | `src/features/elo.py` |
| 看数据清洗规则 | `src/data/cleaner.py` |

---

## Step 10F - Production Launch Readiness (2026-06-29)

Planned scope:

- Runtime environment config (`src/config/runtime.py`, `APP_ENV`, `ENABLE_H5`, etc.).
- Production server entrypoint (`src.server:app`).
- Launch smoke test CLI (`src/smoke_test_launch.py`).
- Runtime and storage health checks (runtime config + data directory writability).
- Deployment guide (`docs/deployment_guide.md`) and launch checklist (`docs/launch_checklist.md`).
- `.env.example`, `requirements.txt`, `.gitignore` updates.

Primary verification:

```powershell
python -m unittest discover -s tests
python src/smoke_test_launch.py
```
