# EPL model card

The published output covers regulation-time home win, draw and away win, in that order. A historical replay reconstructs a prediction from the CC0 archive. A prospective prediction gets its own receipt, cutoff, fixture revision, model digest and public witness.

## Independent model A

* Each season has a separate portable compressed JSON artifact. It fits before July 1 in the starting year, or before the first fixture's T−24h cutoff if earlier. Training labels must already be available at that boundary. The manifest records the last eligible result timestamp, training digest, sample count, model version and scikit-learn version. We do not load arbitrary pickle files.
* The score model is a regularized Dixon–Coles Poisson likelihood with club attack and defence, home advantage and a dependence correction for 0–0, 0–1, 1–0 and 1–1. Training examples in the last five years have a 365-day half-life. New and newly promoted sides start from the league prior. We sum a 0–20 by 0–20 score grid and normalize the probabilities.
* A scikit-learn multiclass gradient boosting classifier has 40 shallow trees per class, learning rate 0.045 and seed 373. It fits at most seven seasons of history. Features are club Elo difference, recent scoring/conceding and points for each side (last eight games, shrunk with four prior games), rest days, club sample sizes and season progress. Elo uses K=20 and a 60-point home advantage. Elo with a draw adjustment also supplies a separate baseline.
* For each new season the preceding season's available outcomes and its already frozen base forecasts select the tree/score mixing weight and temperature by log loss. The score probabilities within each home/draw/away bucket are then rescaled to sum to the final calibrated result probability. Expected goals and the three highest-probability scorelines are calculated from this same distribution.
* Results with a date only are first usable at the next midnight in London. Known kickoff delays this to at least three hours after kickoff. An actual later observation or correction delays use until its receipt. The live pipeline can request a particular frozen model version and rejects timestamps after T−24h. Retrospective bundles cannot prove exactly what an upstream site displayed at any historical time. Their forecasts are labelled as reconstructions.

## Historical evaluation

The first two seasons warm up the model. Subsequent forecasts use an expanding calendar with annual parameter refits and club state updates from eligible results before each forecast. Reproductions read the same input commit and model dependencies. Native numerical libraries on different platforms can introduce small floating-point differences. The packaged model contains the frozen values used by the demo.

| Metric on 9,170 rolling historical EPL predictions | Value |
|---|---:|
| A mean log loss | 0.9837 |
| Elo mean log loss | 0.9964 |
| A multiclass Brier (sum across three outcomes) | 0.5856 |
| A highest-probability outcome correct | 53.29% |

The sample includes 24 full seasons following warmup and 50 resolved games from the pinned 2026/27 file. The data release contains 9,880 results over the 26 preceding complete seasons. See [data provenance](DATA.md). Calibration bins pool all three one-vs-rest probabilities; the report also includes coverage by season and the hardest individual forecasts. Historical scores measure model A and Elo. A comparison to market prices requires the separate paired ledger.

## Market model B and challenger

B0 combines 80% equal-book de-vigged market probabilities and 20% independent probabilities. A missing or unverifiable market observation leaves the B output empty. Scorelines displayed alongside B belong to A.

The optional challenger fits a regularized multinomial residual on the first 380 unique, resolved, witnessed training fixtures. For each outcome it adds a linear combination of log(A / market) to log(market), with a quadratic penalty of five times the sum of squared coefficients. The fit stores its development fixture IDs and version, and the local candidate registry freezes the artifact and training time. Repeated training reads the existing candidate. New candidate predictions require different fixtures, a compatible base registration, valid contemporaneous market receipts and a cutoff later than candidate training. Candidate comparisons are research reconstructions from witnessed base inputs. A claim about a new challenger requires a separately preregistered and publicly witnessed evaluation.

## 复现与阅读结果

在新环境安装仓库后执行下列命令。训练写入本机工作目录，默认的演示权重随安装包提供。重新计算会给出逐赛季结果、校准分桶和误差最大的场次。

```console
epl-agent train
epl-agent backtest --recompute
epl-agent backtest --formal
```

历史胜平负命中率为 53.29%。这组回放验证的是独立模型在历史比分上的滚动表现。市场成绩页单独列出具备同一时点、同一比赛和公开时间证据的样本。真实阵容、实时伤停和天气都不在模型 A 的输入中。
