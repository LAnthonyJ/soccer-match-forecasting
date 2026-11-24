# Football AI Predictor project progress

The June 28, 2026 project snapshot compares national-team match predictions with Pinnacle's home/draw/away prices. The later production-readiness update follows the modeling results.

## Data and model structure

The historical dataset contains about 50,000 international matches from 1872–2026. FIFA rankings from Dato-Futbol matched 23,692 of 25,414 matches. Other inputs included 257 StatsBomb event records at match level, 486 aligned Transfermarkt matches, 199 Pinnacle closing prices from OddsPortal, and pre-match reporting from Zhibo8. The cleaner uses results from 2000 onward for the modeling pipeline.

```
soccer_prediction module/
├── data/
│   ├── raw/
│   │   ├── results.csv
│   │   ├── wc2026_results.csv
│   │   └── oddsportal/
│   │       └── matches.csv
│   ├── processed/
│   │   ├── results_with_event_features.csv
│   │   ├── event_features.csv
│   │   ├── results_with_signals_master.csv
│   │   └── results_with_matchup.csv
│   └── predictions/
│       └── wc2026_predictions.csv
├── outputs/
│   └── models/
│       ├── full_model.pkl
│       └── score_models.pkl
├── src/
│   ├── data/
│   │   ├── cleaner.py
│   │   ├── align_transfermarkt.py
│   │   └── build_kaggle_signals.py
│   ├── features/
│   │   ├── elo.py
│   │   ├── build_features.py
│   │   ├── extract_event_features.py
│   │   ├── style_matchup.py
│   │   ├── tactical_matchup.py
│   │   └── extract_signals.py
│   ├── models/
│   │   ├── train.py
│   │   ├── score_models.py
│   │   ├── score_evaluation.py
│   │   ├── pre_match_adjuster.py
│   │   └── odds_model.py
│   ├── llm/
│   │   ├── explainer.py
│   │   ├── match_info_extractor.py
│   │   └── pre_match_pipeline.py
│   ├── pipeline.py
│   ├── pipeline_score.py
│   ├── predict_today.py
│   ├── backtest_12_odds.py
│   ├── betting_backtest_dc.py
│   ├── evaluate_wc2026.py
│   ├── evaluate_full_wc2026.py
│   └── evaluate_score_models.py
├── web/
│   ├── app.py
│   └── score_components.py
├── venv/
└── PROJECT_PROGRESS.md
```

## Phases 1–6

The 76-feature pipeline covers team strength (Elo and FIFA points), recent form, head-to-head results, competition and venue context, weighted scoring form, coaching records, StatsBomb events and tactical matchups. Weighted form uses a 12-match half-life and coaching win rates use a three-year window. Training weights decay with a three-year half-life and a 10% floor. Competition weights are 5× for World Cups, 3.5× for continental tournaments and 2.5× for World Cup qualifiers.

The pre-match adjuster calibrates 13 signals against an Elo baseline using Bayesian adjustments and t-tests at p < 0.10 with at least 20 observations. Only 39–128 matches informed individual signals. A subsequent pipeline extracted structured information from Zhibo8 news with DeepSeek.

XGBoost and LightGBM used a random 85/15 training/validation split, time and tournament weights, a separate 2022 World Cup test set, isotonic calibration and outcome-specific ensemble weights optimized with SLSQP. In the 2026 odds comparison the ensemble had log loss 0.829 against 0.716 for the market.

The Streamlit dashboard covers match forecasts, scorelines, performance and history. It supports about 80 Chinese team aliases. The historical strategy used a 10% minimum edge, quarter-Kelly sizing and a 5% bankroll cap. A threshold analysis on 12 matches reported +22% ROI at that cutoff. The tracked bets below are reported separately.

## Phase 7 score forecasts

The ensemble combines a roughly 200-parameter Dixon–Coles model, XGBoost Poisson goal rates, and an expected-goals Poisson model calibrated from StatsBomb. Its joint score distribution gives the top 15 scores, over/under 2.5 goals and the probability that both teams score.

Dixon–Coles uses team attack and defense parameters, home advantage γ and low-score dependence ρ. Home intensity is exp(γ + home attack + away defense) and away intensity is exp(away attack + home defense). The correction applies to 0–0, 1–0, 0–1 and 1–1. An IRLS Poisson fit initializes L-BFGS-B refinement. Training covered 11,611 matches from the top 100 teams, with a five-year half-life. Fitted γ was 0.267 and ρ was −0.0906. The highest attack parameters were Spain 0.854, Brazil 0.832 and Germany 0.796. The strongest defensive parameters were Argentina −0.822, Brazil −0.718 and Spain −0.687.

### 70-match 2026 World Cup retrospective

| Metric | Dixon-Coles | ML-Poisson | Elo baseline | Ensemble |
|------|-----------|-----------|---------|----------|
| **RPS ↓** | **0.0565** | 0.0582 | 0.0599 | 0.0571 |
| **Scoreline log loss ↓** | **2.859** | 3.209 | 3.216 | 3.010 |
| **H/D/A accuracy** | **42/70 (60%)** | 33/70 (47%) | 35/70 (50%) | 36/70 (51%) |
| **O/U accuracy** | **39/70 (56%)** | 34/70 (49%) | 28/70 (40%) | 38/70 (54%) |
| **BTTS accuracy** | 40/70 (57%) | 37/70 (53%) | 33/70 (47%) | **42/70 (60%)** |

### Twelve matches with recorded odds

| Metric | Dixon-Coles | Pinnacle market | Previous HDA Ensemble |
|------|-----------|-------------|---------------|
| **H/D/A LogLoss** | 0.767 | **0.716** | 0.829 |
| **H/D/A  accuracy** | **9/12 (75%)** | 8/12 (67%) | 8/12 (67%) |
| **Betting P&L** | **Y-62 (-36%)** | — | Y-179 (-100%) |

Dixon–Coles performed best on ranked probability score and scoreline log loss in the 70-match sample. Its outcome accuracy was 60% versus 51% for the ensemble. ML-Poisson had led on the 2022 validation set. Dixon–Coles underestimated high totals: its mean over-2.5 probability was 42% and the observed rate was 59%. In the smaller odds sample, its log loss exceeded Pinnacle's by about 0.05.

## Corrections and limitations

- The Elo Poisson baseline now consistently uses exp(0.15 + 0.0011 × elo_diff). The earlier implementation also used 1.30 + elo_diff / 400 × 0.5.
- For neutral venues, search exact reversed fixtures before fuzzy matching and swap all 76 directional features when reversing a match. The Dixon–Coles model passed the symmetry check. Training home advantage can still affect the H/D/A ensemble.
- Normalize names such as United States → USA, Czech Republic → Czechia and IR Iran → Iran. The lookup also recognizes aliases for Panama and DR Congo.
- When the feature matrix lacks a fixture, fall back to a Dixon–Coles prediction using the team names.

The tracker used a starting bankroll of ¥1,000 and Dixon–Coles versus Pinnacle for home/draw/away outcomes. Of 18 tracked matches, 12 had finished when the report was written. The old ensemble lost all five tracked bets (−¥179), and Dixon–Coles won one of five (−¥62) while predicting nine of 12 outcomes correctly. For six then-pending June 28 matches, the tracker listed Uzbekistan at 5.30 (+13.8% edge, ¥39), Austria at 3.00 (+10.9%, ¥23), and four passes.

Eight of the 12 largest estimated edges were draws, only two of which occurred. The model often flagged underdogs. The fitted low-score correlation may overestimate draw probabilities. Twelve odds observations and unofficial match results from a Vietnamese sports site limit interpretation.

The original next steps were to recalibrate total goals or try a negative binomial distribution, test excluding draw signals, evaluate over/under 2.5 separately (56% accuracy here), update team strengths online, recalibrate for knockout games after 72 group matches, and compare bivariate Poisson with independent Poisson.

## Historical commands

```sh
python src/pipeline_score.py
python src/predict_today.py
python src/betting_backtest_dc.py
python src/backtest_12_odds.py
streamlit run web/app.py --server.headless true
python src/evaluate_full_wc2026.py
```

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
