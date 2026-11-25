# English Premier League data

The bundled dataset contains **9,880 completed Premier League fixtures** from 2000/01–2025/26. Every one of the 26 seasons contains 20 clubs, 380 distinct ordered home/away pairs, 380 results, zero duplicate fixtures and zero missing pairs. An additional 380 fixtures for 2026/27 include 50 completed results at the pinned revision. These are real historical records. The bundled file is a dated snapshot, so a future scheduled time or a missing result may have changed since it was produced.

The source is [openfootball/england at commit 9ee7a0c](https://github.com/openfootball/england/tree/9ee7a0c351805c99cc334478a9bf04b7d0432fec), under [CC0 1.0](../epl_agent/assets/data/CC0-LICENSE.txt). The [per-season manifest](../epl_agent/assets/data/coverage.json) lists paths, expected and imported counts, missing and duplicated fixture IDs, normalized-source SHA-256 hashes and the canonical CSV hash. Newline normalization to LF makes the check independent of Git's Windows line-ending settings. Historical revisions before this first pinned dataset were not collected.

## Reproduce the dataset

In a shell at the project root, install this project and run

```sh
git clone https://github.com/openfootball/england.git ../openfootball-england
git -C ../openfootball-england checkout --detach 9ee7a0c351805c99cc334478a9bf04b7d0432fec
python -m epl_agent.data ../openfootball-england --output rebuilt-data
```

Compare the regenerated `coverage.json` and `matches.csv` with the copies under `epl_agent/assets/data/`. Without an internet connection, training and replay read the bundled data directly.

## Match identity and time

Only the English top division's regular-season results are imported. IDs combine season, canonical home club and canonical away club. Chinese display names, common English abbreviations and historical suffixes resolve to canonical names. IDs survive a change in kickoff. The upstream matchday number determines the displayed round, even for fixtures played outside their original week.

Date headings use the stated weekday to resolve the year on either side of the season boundary. A group of matches under the same date and clock inherits that kickoff. A new date clears the prior clock; absent times remain unknown. London civil times are converted to UTC with daylight-saving rules. In 2025/26 the compact source mixes local and UTC clocks. For that season the importer joins all 380 games to [the companion file with per-game UTC offsets](https://github.com/openfootball/england/blob/9ee7a0c351805c99cc334478a9bf04b7d0432fec/2025-26/1-premierleague-full.txt). Dates, pair identity and coverage must agree. For example, Liverpool–Bournemouth on 15 August 2025 was listed at 19:00 UTC+0, while Aston Villa–Newcastle the next day was listed at 12:30 UTC+1.

## Live observations and corrections

`python -m epl_agent sync --season 2026-27` resolves the upstream commit and fetches the complete season at that immutable revision. The first successful import records an observation time on this machine. Each later change appends a version to a private SQLite database. Transactions preserve the original versions on failure. A changed date, kickoff or round increments the schedule revision; a corrected score records a new result observation time. Withdrawal and reinstatement keep their history. Incomplete season files, duplicate fixtures, conflicting simultaneous imports and older out-of-order imports leave the previous committed view available.

Local observation fields distinguish clocks used for different questions:

| Field | Meaning |
|---|---|
| `date`, `kickoff_utc` | Source's scheduled match day and kickoff, when known |
| `schedule_observed_at` | First local observation of this schedule revision |
| `result_observed_at` | First local observation of this score or score correction |
| `observed_at`, `source_commit`, `revised_fields` | Observation, source identity and changes from the preceding local row |

`load_matches(path, as_of=cutoff)` overlays the last locally observed version at or before a UTC cutoff onto the pinned reconstruction. Bundled historical rows do not claim a verified observation time. Retroactively downloading a file cannot establish what the market or a predictor knew before a match. The code and scorecard use separate reconstructed and prospective cohorts.

## Current-season result supplement

The worker fetches the full English top division from the two public providers once per scheduled sync. Openfootball supplies canonical clubs, rounds and kickoff times. OpenLigaDB supplies supplementary final scores. Its season shortcut is `pl` and the season identifier is the starting year. Only an exact season, home club and away club match can supply a missing result. A result needs the provider's finished flag and one valid `After90Minutes` record. Halftime scores, incomplete results and previously unseen pairings do not settle fixtures. A partial secondary response leaves the existing fixture list intact. [OpenLigaDB API examples](https://github.com/OpenLigaDB/OpenLigaDB-Samples) · [API terms of use](https://www.openligadb.de/en/terms-of-use)

Each normalized source observation includes a fixture ID, revision and local receipt time. The SQLite cache records changes transactionally. When a provider is offline its previous observations remain available and the sync reports the failed attempt. The board counts a fixture as overdue when its verified kickoff is more than 24 hours in the past and a settled score is missing. Recent successful sync time and latest scored match date measure different things. The latter is the date on the canonical schedule.

When upgrading an existing installation, restart the worker with the installed v1 package. The first v1 sync adds a SQLite trigger to protect merged fixtures from being overwritten by an older worker. A rejected legacy write leaves the latest committed results available to readers.

If both providers report different final scores, the canonical score is left pending. Both reports and earlier settled versions remain inspectable. Agreement in a later observation resolves the conflict from that observation onward. A maintainer can also select a reported result using a public source and a written reason. From the repository or installed environment, run the following command with the actual ID and public evidence for the disputed match.

```console
python -m epl_agent.source_sync resolve SEASON FIXTURE_ID --prefer openfootball --evidence HTTPS_EVIDENCE_URL --note "Reason for the correction"
```

`--prefer` accepts `openfootball` or `openligadb`. The decision is bound to the two disputed scores. If either changes, the decision is re-evaluated and a new disagreement requires new evidence. The resolution time is recorded locally. Previously frozen predictions retain their original inputs.

The historical archive remains available under CC0. Current-season data incorporating OpenLigaDB is credited to OpenLigaDB and the combined downloadable database is distributed separately under ODbL 1.0. Its normalized records contain the match and source fields needed to reproduce the merge. Club images, provider credentials and bookmaker prices are outside that export. OpenLigaDB is community-maintained and offers no completeness or freshness guarantee. [OpenLigaDB data license](https://www.openligadb.de/en/license) · [ODbL 1.0 text](https://opendatacommons.org/licenses/odbl/1-0/)

## Market data

Users can capture new EPL quotes using their own free key. The operator retains raw quotes locally. Only derived predictions, summaries and model evaluation material are eligible for public export under [The Odds API terms](https://the-odds-api.com/terms-and-conditions.html). The package contains no bookmaker feed, commercial warehouse, scraped Football-Data CSV or credentials.

## 中文摘要

仓库附带 26 个完整英超赛季共 9,880 场真实比分，另附 2026/27 赛季的固定版本赛程。每季都核对了球队数、380 组主客对阵、比分、重复及缺失。开放比分采用 CC0；代码采用项目的 MIT 许可。无需密钥即可离线使用。上面的来源清单记录了原始文件版本及校验值。

同步命令记录本机首次看到赛程及其修订的 UTC 时间。历史包用于重建赛前特征，正式市场比较只使用带有实际赛前观测证据的前瞻记录。

当季同步会使用 OpenLigaDB 的常规时间终场比分补缺。主客队和赛季须精确对应。两源比分冲突时暂停结算。看板分别显示比分覆盖日期、同步成功时间和开球超过 24 小时仍待补的场数。合并数据库按 ODbL 1.0 署名分发，原始 CC0 历史比分可以独立使用。
