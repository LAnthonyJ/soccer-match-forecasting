# EPL Matchdesk

[简体中文](README.md) · [Data provenance](docs/DATA.md) · [Model card](docs/MODEL.md) · [Evaluation protocol](docs/EVALUATION.md)

**Browse this week's Premier League fixtures, open a forecast and check the final score.** The Chinese-language dashboard works without an account. [Open Matchdesk](https://lanthonyj.github.io/worldcup_prediction/)

| Try it | What to look for |
|---|---|
| Read a forecast | The 26 November 2026 preview for Forest vs Chelsea gave Forest 37.7%, a draw 27.3% and Chelsea 35.0%. The most likely single scoreline was 1–1 at about 13.0%. [Open the fixture](https://lanthonyj.github.io/worldcup_prediction/#/match/2026-27_nottingham-forest_chelsea) · [Pinned example](docs/FAN-RELEASE-2026-11-26.md) |
| Ask about another team | In the match panel **问这场**, enter “阿森纳下一场踢谁” (Who do Arsenal play next?), then “那场几点开球” (When does that match kick off?). The reply identifies the fixture, data cutoff and evidence. “本页这场” returns the conversation to the match on screen. You can also ask for the last five league results, the last meeting or standings calculated from confirmed scores. |
| Check a result | The matchweek and the original share link show the confirmed score. The fixture's data-and-methods link opens its record. [Brighton 2–0 Brentford](https://lanthonyj.github.io/worldcup_prediction/#/match/2026-27_brighton-hove-albion_brentford) |

Kickoff times use your device's time zone. Outcome probabilities cover regulation time including stoppage time. Previews can change until the T−24h freeze. When at least three eligible bookmakers supply complete quotes in time, the match can also show the market consensus and the 80% market / 20% independent-model blend. Answers identify missing evidence for injuries, starting lineups and individual goalscorers.

![Premier League match dashboard](docs/screenshots/matchdesk-desktop.png)

## Run locally

Install Git and Python 3.11–3.13. The package includes the built frontend, fitted models and 9,880 results from 26 complete seasons. After installing the dependencies, you can run the demo offline without Node or an API key. The EPL application lives on the repository's `epl-v1` branch.

```sh
git clone -b epl-v1 --single-branch https://github.com/LAnthonyJ/worldcup_prediction.git epl-prediction-agent
cd epl-prediction-agent
```

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock .
.\.venv\Scripts\python.exe -m epl_agent demo
```

**macOS or Linux**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements.lock .
.venv/bin/python -m epl_agent demo
```

Open [your local dashboard](http://127.0.0.1:5051). A fresh offline install includes historical scores. In the bundled 2026/27 matchweek 12, all ten kickoff times are awaiting verification. For an immediate pre-match example, use the public Forest vs Chelsea link above.

To sync fixtures and results, open a second terminal in the project directory. Run `.\.venv\Scripts\python.exe -m epl_agent sync` on Windows or `.venv/bin/python -m epl_agent sync` on macOS or Linux, then reload the board. In the commands below, `python` refers to that same interpreter.

## Trace a match

The software is at **1.0.0rc1**. In the 26 November 2026 release checks, 48 questions produced 144 correct answers across Flask, the static site and a simulated production gateway. An anonymous replay also verified all ten fixtures' kickoff times, forecast states and displayed probabilities. The public demo answers from published match facts. Live DeepSeek responses and prospective market pairs have separate acceptance checks. [Release record](docs/FAN-RELEASE-2026-11-26.md) · [CI run](https://github.com/LAnthonyJ/worldcup_prediction/actions/runs/45788121445) · [Public scorecard](https://lanthonyj.github.io/worldcup_prediction/#/report) · [AI service setup](docs/ANALYSIS.md)

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

The collector checks the capture window roughly every 20 seconds and syncs each fixture source roughly hourly. The operator configures `ODDS_API_KEY` for UK 1X2 odds and runs `python -m epl_agent worker --publish --publish-snapshot` to publish. Each Pages build pins a commit on `public-data`. The deployed marker gives its full SHA so you can retrieve that version of `snapshot.json`, check the witness issue and recompute the scores. [Deployed data commit](https://lanthonyj.github.io/worldcup_prediction/public-data-commit.txt) · [Public data branch](https://github.com/LAnthonyJ/worldcup_prediction/tree/public-data) · [Witness ledger](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) · [Maintainer guide](docs/EVALUATION.md)
