# 市场对照协议 v1

预测对象为英超常规时间（含补时）主胜、平局、客胜。主挑战模型 B0 固定为同场去水市场概率的 80% 加独立模型 A 的 20%。A 与市场另列成绩。协议和书商固定清单位于 [market.py](../epl_agent/market.py)。本地注册记录将该清单、算法参数、模型版本和正式评测赛季一起计算摘要。

## 采集与公开见证

1. 对将要运行的公开代码版本执行 `python -m epl_agent register`。正式期为注册之后开始的连续两个完整赛季（采用 7 月 1 日赛季边界）。先在 GitHub 发布协议，并在自己的公开仓库建立专用 issue。
2. 市场采集启用时配置 `ODDS_API_KEY`；独立 A 可直接运行。公开见证在采集机上设置 `EPL_EVIDENCE_REPO=owner/repository`、`EPL_EVIDENCE_ISSUE`（数字）以及 `EPL_GITHUB_TOKEN`（仅目标仓库的 Issues 写权限）。不要将密钥放入仓库、网页或公开工作流参数。先执行 `python -m epl_agent witness` 公开注册，再启动 `python -m epl_agent worker --publish`。发布选项会向指定公开 issue 发送协议或新预测的摘要和派生概率。
3. 每小时同步赛程，每分钟检查目标窗口，即开球前 24 小时截止的前 15 分钟。同窗比赛共享一份全英超、英国地区、十进制 h2h 响应，常态每 3 分钟最多请求一次，最后 2 分钟可以每分钟尝试。进入最后 2 分钟时用已经收到的最新合格报价生成预测，为公开见证留出时间。每家书商三项须齐全、有限且大于 1，报价更新时间至接收时间不超过 24 小时。按各家倒数和归一化后等权平均，至少三家。观察、生成以及外部见证都须不晚于截止时刻。月度 500 免费 credits 保留 50；错过窗口、数据缺失与改期计入覆盖报告。
4. 本地 SQLite 保存原始报价，公开 issue 保存时间见证。每次预测含赛程版本、模型、观察时间与判定协议的哈希。用 `python -m epl_agent ledger` 导出派生记录，再用 `python -m epl_agent audit` 从 GitHub 重新读取公开见证，核对原始时间和摘要。成绩页使用最近一次审计结果。服务器离线期间错过的比赛保留为缺项。数据修订可改变标签，旧版本保存在本机赛程观察库中。

## 评分

在完全相同的合格比赛上，计算市场与 B 的平均自然对数损失差（市场减 B），同时报告 Brier、命中率与校准。每场等权。在 ISO 比赛周内保留全部比赛，按周有放回抽样 10,000 次，种子为 20261125，取差值的 2.5% 和 97.5% 分位数。两季各有 380 场完赛、每季有效配对覆盖率至少 90%、改善至少 0.005 且区间下界大于 0，才允许声明在对应赛季、时点和书商集合上领先。覆盖分母包含缺少行情、缺少公开见证和模型缺报的比赛。

使用 `python -m epl_agent backtest --formal` 保存每月进度和赛季总结。新版本应使用独立的注册记录和后续评测期。研究候选模型可用 `python -m epl_agent candidate train` 在首个 380 场经见证的配对上拟合并冻结，再用 `candidate score` 检查其训练后的新比赛。候选回放结果单独列示。

实时接口免费档没有历史额度。监管数据源条款和免费档变动时，先更新协议再启动下一批评测。[免费档与限制](https://the-odds-api.com/) · [数据使用条款](https://the-odds-api.com/terms-and-conditions.html) · [概率评分依据](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)

## v1.0 公开快照与 Pages

采集机独占原始报价。预注册及赛前预测的时间见证保存在专用公开 issue 中。`public-data` 分支保存版本化的公开输入，包含完整赛季赛程、比分修订及接收时间、市场共识、A/B 派生概率、缺项、来源健康状态和批次截止时间。OpenLigaDB 与 openfootball 合并的当季数据库按 ODbL 1.0 单独署名分发。安装包的原始历史比分继续使用 CC0。Pages 可下载 `snapshot.json`、`merged-seasons.json`、`openligadb.json` 和 `public-data-commit.txt`（已发布的批次）。

```sh
python -m epl_agent snapshot export ./public-input
python -m epl_agent snapshot verify ./public-input
python -m epl_agent export ./site --snapshot ./public-input
```

无密钥、空账本可执行上述离线演示。核验已见证的数据时，在独立的受信任配置中设置 `EPL_EVIDENCE_REPO` 和 `EPL_EVIDENCE_ISSUE`。采集机或构建机可用 `EPL_AUDIT_GITHUB_TOKEN` 提高只读 GitHub API 限额。验证器从该 issue 分页读取截至批次时点的全部评论，复核未编辑的原始摘要、接收时刻及发布清单，按同一批赛程重算样本、分数和区间。已公布却缺少对应记录、文件校验不符、审计服务故障均会导致失败。已经冻结的 v0.1 预测仍沿用原摘要。

正式成绩仅使用独立审计通过的配对。注册时正在进行的赛季单列为试运行。例如 2026 年 11 月注册，2026/27 是试运行，2027/28 和 2028/29 是正式期。缺少 API 密钥或市场报价的用户可以继续读取模型 A。未完成审计的本地配对列在 `verification.pending_pairs`，计分以 `n_pairs` 为准。

### 采集机和发布机

1. 在采集机分别设置仅有目标仓库 Issues 写权限的 `EPL_GITHUB_TOKEN`、仅有该仓库 Contents 写权限的 `EPL_SNAPSHOT_GITHUB_TOKEN`，以及个人 `ODDS_API_KEY`。将前述见证仓库和 issue 绑定到可信配置。先运行 `register`、`witness` 发布协议，然后运行 `python -m epl_agent worker --publish --publish-snapshot`。采集进程独立调度来源同步、冻结、见证、上传和线上提交回读。需要立即重试时用 `python -m epl_agent snapshot publish ./public-input`。
2. 发布只向专用数据分支追加提交。`public-data` 归项目发布工具管理。创建新分支或追加非强推更新失败时保留旧分支。独立仓库的默认分支 workflow 支持每三小时与手动构建。部署前在 Settings → Pages 选择 GitHub Actions，并允许相应代码分支部署到 `github-pages` 环境。通过仓库变量 `EPL_EVIDENCE_ISSUE` 固定预期 issue。
3. 在 `LAnthonyJ/worldcup_prediction` 中使用 [英超见证 issue #1](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) 与 `epl-v1` 代码分支。在该采集机设置 `EPL_PAGES_CODE_BRANCH=epl-v1`。发布者在 `public-data` 更新后，将其确切 SHA 写入 `epl-v1/.github/epl-public-data-sha`，触发该分支的 Pages 工作流。构建读取固定 SHA 上的数据，代码来自受信任的英超分支。构建失败保留前一次成功部署。`master` 用于原世界杯项目。

### 从干净环境部署静态分支

若维护者选用 Pages 的 “Deploy from a branch”，可使用以下独立路径。在新的工作目录中，以 Git Bash 执行。先从 GitHub 取英超代码和已发布的输入；安装依赖后，在空白 `EPL_AGENT_HOME` 下重新审计。对该仓库设置 `EPL_EVIDENCE_REPO=LAnthonyJ/worldcup_prediction`、`EPL_EVIDENCE_ISSUE=1`。脚本保留 gh-pages 上的其他站点路径。

```sh
set -euo pipefail
repo=https://github.com/LAnthonyJ/worldcup_prediction.git
git clone --single-branch --branch epl-v1 "$repo" epl-code
git clone --single-branch --branch public-data "$repo" epl-input
if [[ -f epl-code/.github/epl-public-data-sha ]]; then
  git -C epl-input checkout --detach "$(cat epl-code/.github/epl-public-data-sha)"
fi
export EPL_EVIDENCE_REPO=LAnthonyJ/worldcup_prediction EPL_EVIDENCE_ISSUE=1
export EPL_AGENT_HOME="$PWD/clean-epl-home"
python -m pip install -c epl-code/requirements.lock ./epl-code
python -m epl_agent snapshot verify epl-input
python -m epl_agent export built-epl --snapshot epl-input
git -C epl-input rev-parse HEAD > built-epl/public-data-commit.txt
if [[ -n "$(git ls-remote --heads "$repo" refs/heads/gh-pages)" ]]; then
  git clone --single-branch --branch gh-pages "$repo" epl-site
else
  git init -b gh-pages epl-site
  git -C epl-site remote add origin "$repo"
fi
mkdir -p epl-site/epl
cp -R built-epl/. epl-site/epl/
touch epl-site/.nojekyll
git -C epl-site add -- .nojekyll epl
git -C epl-site commit -m "Publish audited EPL dashboard"
git -C epl-site push origin HEAD:gh-pages
```

选择 `gh-pages` 分支根目录后，英超部署路径为 `/worldcup_prediction/epl/`。同一仓库每次采用一种 Pages 发布方式。

### Verification in English

Export the collector inputs with `epl-agent snapshot export DIR`. On an independent host, configure the expected public witness repository and issue, run `epl-agent snapshot verify DIR`, then `epl-agent export site --snapshot DIR`. The verifier checks the pinned snapshot, all three-way fixture probabilities and witness inventory, then recomputes paired metrics at the collector's cutoff. Pilot and registered full seasons have separate scorecards. The published data branch contains derived probabilities and the separately attributed ODbL fixture data. A failed Pages audit stops the deployment before the previous site is replaced.


## 采集运维与两步验收

**运行账户和升级顺序。** 首先把支持 `public-snapshot/v1` 和 `public-snapshot/v2` 的读端部署到 `epl-v1`，使用同一个固定 v1 数据提交完成验证。切换采集端前，记录已部署提交、采集目录和旧解释器路径。在冻结窗口以外停止旧 worker，运行 `Backup`，核对输出的备份位置和校验清单。任务使用持久安装目录及原有数据库。Windows 账户需有任务注册权限；以该账户执行下列 PowerShell 步骤，用 `Probe` 确认未登录时也能读取数据库并访问 GitHub。

```powershell
$collector = '.\scripts\windows-collector.ps1'
$settings = @{Python=(Get-Command python.exe).Source; Repository='LAnthonyJ/worldcup_prediction'; Issue=1}
& $collector Backup
& $collector Configure -SecretName EPL_GITHUB_TOKEN
& $collector Configure -SecretName EPL_SNAPSHOT_GITHUB_TOKEN
& $collector Check @settings
& $collector Probe @settings
& $collector Install @settings
& $collector Start
& $collector Status
```

后续升级时执行 `& $collector Stop`。这会向持有指定数据目录锁的实例发出停机请求，等待当前工作完成，并实际重新取得锁验证停机。任务外壳提前退出时，仍可通过数据目录联系子进程。看到 `lock_released: true` 后，依次备份、更新程序并启动。老版本若报告不支持停机协议，请维护者核对原任务账户、解释器、项目运行脚本、数据目录及父子进程后停止该实例，再重新核实锁已释放。

如果 Windows 权限阻止创建开机任务，可显式运行 `& $collector Install @settings -AtLogon` 创建当前账户的登录启动项。该模式从用户登录时开始采集，`Status` 中的 `logon_fallback` 会显示启用状态。登录前错过的窗口由 worker 记录。

`Configure` 在本机隐藏输入并绑定当前 Windows 身份保存凭据。见证凭据需目标仓库 Issues 写权限；数据发布凭据需目标仓库 Contents 写权限。已有可用于该仓库的 Git Credential Manager 登录时可选用 `-UseGitCredential`。启用市场时再配置 `ODDS_API_KEY`。若免密码的计划任务身份无法访问凭据，可在本机以 `-Credential (Get-Credential)` 对同一账户依次执行 `Probe`、`Install`。`Install` 只注册任务，`Start` 在接管时启动，`Uninstall` 移除本项目任务。更新与备份操作使用相同的 `-DataRoot`；默认位置为用户目录下的 `.epl-prediction-agent`。

**运行诊断。** `epl-agent status` 和 `/api/status` 展示最近心跳、下一场冻结窗口、逾期比分、待见证数、本地上传与已回读的线上提交。`worker --once` 会执行一次真实采集和来源同步，离线演练请使用测试套件的临时数据库和模拟传输。在 `match trace <比赛ID>` 中核对赛程修订、实际时间和冻结哈希。来源联通时间与最新比分日期分别展示。逾期比赛随来源恢复继续补结；若两源仍有争议，维护者核对公开依据后可执行 `match resolve <比赛ID> --prefer openligadb --evidence <公开HTTPS链接> --note "<裁决理由>"`，也可选 `openfootball`。修订参与最新批次评分。

**上线验收。** 第一步以真实比赛作为样本，记录比赛详情 URL、截止前的 issue 评论 URL 和固定数据提交。新环境独立执行 `snapshot verify` 时应得到一致的 A 样本量和评分，缺少市场报价的该场对市场样本量贡献为零。第二步用真实的三家及以上合格同期报价复算 A、市场和 B，三条链接与市场配对样本均能核验后可发布正式标签。模拟时钟演练单列为软件测试。

**回滚。** 部署或核验失败时继续提供上一份审计成功的站点。先停止升级后的 worker，备份故障现场、完整冻结数据库、待见证和已有回执。若新版已产生新见证，保留最新数据和能够读 v2 的版本，修复后从同一目录恢复；覆盖为旧备份会丢失已公告哈希。仅在确认没有升级后的新增观察及见证时，才在独立目录恢复升级前备份、重装已知可用的软件并重新核验公开账本。对齐线上 SHA、任务账户和本地样本后再启动唯一的采集任务。

## Windows operator quick start

For upgrades, use `& $collector Stop`. The request identifies the running instance by its data directory and waits for active work and release of its exclusive lock. This also reaches a collector whose scheduler launcher has already exited. After `lock_released: true`, back up the databases, update the package, and start the task. For a legacy collector that reports an unsupported stop protocol, identify its account, interpreter, project script, data directory and child processes before stopping the exact instance and verifying the lock again.

If Windows denies boot-task registration, `& $collector Install @settings -AtLogon` explicitly installs a current-user sign-in shortcut. Collection starts after sign-in. Check the `logon_fallback` field in `Status` to identify this mode.

Use a permanent Python installation and the Windows account that owns the existing collector data. Deploy a reader that understands both snapshot versions before switching writers. Stop the old worker outside a freeze window and take a verified SQLite backup with the PowerShell commands above. `Configure` stores each token under the current user's Windows protection. The issue writer needs Issues write and the snapshot publisher needs Contents write on the target repository. The odds key is optional. Run `Check`, then the scheduled identity `Probe`, then `Install` and `Start`. For a different collector directory pass the same `-DataRoot` to backup, checks and installation. Use `Status` to inspect the task and `Uninstall` to remove it.

Inspect `epl-agent status`, `/api/status`, and `epl-agent match trace FIXTURE_ID` for the next cutoff, outstanding receipts, result freshness, and confirmed Pages SHA. A correction changes subsequent scores; old public input commits remain reproducible. Gate A requires a real timely public A receipt, a confirmed result and independently reproduced score. Gate B requires the additional eligible real quotes and verified market pair. Keep the fixture, witness and fixed public input URLs for each gate.

If publication fails, keep serving the previous verified deployment. Preserve the latest databases and receipts before rollback. Restore a pre-upgrade backup into a separate directory only after checking that it would discard no new observations or witnesses. For a collector that has already emitted v2 evidence, retain the compatible reader and recover forward using the original ledger.
