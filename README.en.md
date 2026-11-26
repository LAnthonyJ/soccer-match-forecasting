# EPL Matchdesk

[简体中文](README.md) · [Data provenance](docs/DATA.md) · [Model card](docs/MODEL.md) · [Evaluation protocol](docs/EVALUATION.md)

**1.0 release candidate** · [Matchdesk](https://lanthonyj.github.io/worldcup_prediction/) · [Pregame witness ledger](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) · [Pilot and formal scorecards](https://lanthonyj.github.io/worldcup_prediction/#/report)

The home screen lists the current matchweek by date in your time zone. Open an upcoming fixture for probabilities, recent form and match-specific questions. Confirmed results appear directly in the schedule. The footer links to the data sources, research scorecard and collection status.

Explore English Premier League match predictions one day before kickoff. The Chinese-language dashboard compares an independent football model, a de-vigged market consensus and an 80/20 market/football blend. The local assistant answers questions using the selected match and the same probability snapshot.

![Premier League match dashboard](docs/screenshots/matchdesk-desktop.png)

## Run locally

Open a fixture and select the analysis panel to review the match outlook, recent form, market comparison and source facts. The offline demo labels program-generated summaries. Verified DeepSeek reports display the provider and generation time. GitHub Pages can mirror public reports; interactive follow-ups use the same-origin gateway. [Analysis and deployment](docs/ANALYSIS.md)

From a clone or source archive, using Python 3.11–3.13:

```sh
git clone -b epl-v1 --single-branch https://github.com/LAnthonyJ/worldcup_prediction.git epl-prediction-agent
cd epl-prediction-agent
python -m pip install -c requirements.lock .
python -m epl_agent demo
```

Open [Matchdesk](http://127.0.0.1:5051). Pick a season and matchweek, search a club and select a fixture. Predictions and results are displayed separately. The static frontend, CC0 data and fitted models ship with the package; no provider account or Node installation is required.

Run `python -m epl_agent sync` with an internet connection to refresh the current season, then reload the board. A fresh offline installation shows the packaged results and marks unconfirmed kickoffs as pending.

## Trace a match

The fixture timeline records schedule revisions, the T−24h cutoff, freeze, public witness, result observation and confirmed Pages publication. The independent scorecard includes eligible A forecasts even when the collector has no odds key. The market scorecard uses the paired subset. The release candidate remains available while real fixtures mature.

```sh
python -m epl_agent status
python -m epl_agent match trace 2026-27_fulham_manchester-united
```

Use `scripts/windows-collector.ps1` for the Windows startup task. Find credentials, backup, rollout and verification steps in the [operator runbook](docs/EVALUATION.md#windows-operator-quick-start).

## Reproduce the research

```sh
python -m epl_agent predict 2026-27_fulham_manchester-united
python -m epl_agent backtest --recompute --output scores.json
python -m epl_agent backtest --formal
python -m epl_agent export site
```

The release contains 9,880 results across 26 complete seasons plus a pinned partial 2026/27 season. Two warm-up seasons leave 9,170 historical reconstructions. The formal leaderboard counts only prospectively paired, witnessed forecasts. Market superiority is an empirical target.

Use `sync` to observe schedules and results from openfootball and OpenLigaDB, `train` to activate a local build, and `worker` for ongoing capture. Trained models and historical data ship with the package. The historical database is CC0. The downloadable merged current-season database is attributed to OpenLigaDB and distributed under ODbL 1.0. [OpenLigaDB terms](https://www.openligadb.de/en/license)

The operator configures `ODDS_API_KEY` for UK 1X2 odds and runs `worker --publish --publish-snapshot`. The data branch is `public-data`. Use `epl-agent snapshot export public-export` to create the public file and `epl-agent snapshot verify public-export` to check a batch against the independently configured issue and recompute its scores. The deployed dashboard exposes the release-validation fixture after a real paired result is available. The deployment recipe and required environment variables are in the [maintainer guide](docs/EVALUATION.md).
