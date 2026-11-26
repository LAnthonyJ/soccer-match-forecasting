# 英超观察台 · EPL Prediction Agent

[English](README.en.md) · [数据来源](docs/DATA.md) · [模型卡](docs/MODEL.md) · [市场验证协议](docs/EVALUATION.md)

**1.0 发布候选版** · [在线比赛看板](https://lanthonyj.github.io/worldcup_prediction/) · [赛前见证账本](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) · [试运行与正式成绩](https://lanthonyj.github.io/worldcup_prediction/#/report)

首页按日期列出本轮对阵。选择未开赛的比赛，可查看三项概率、双方差距和赛前看点。上一轮的确认比分直接显示在赛程中。需要查阅评分协议、历史模型对照或采集状态时，可进入页脚的“数据来源与方法”。

发布包内含 **26 个完整赛季、9,880 场真实比分**和已构建的界面，首次体验无需 Node、账户或密钥。主胜、平局、客胜针对常规时间含补时。赛前预览会随资料变化，正式冻结时点为开球前 24 小时。

![英超赛程与赛前分析](docs/screenshots/matchdesk-desktop.png)

## 本地运行

打开一场未来比赛后，点“展开分析”阅读依据，或用“问这场”提出自己的问题。免密钥模式提供基础数据解读。已通过事实核对的 DeepSeek 报告注明模型和生成时间。打开分析依据可以核对对应比赛的赛前事实。[AI 分析与部署](docs/ANALYSIS.md)

安装 Python 3.11–3.13，在下载或克隆的仓库根目录运行。推荐使用独立虚拟环境。

```sh
git clone -b epl-v1 --single-branch https://github.com/LAnthonyJ/worldcup_prediction.git epl-prediction-agent
cd epl-prediction-agent
python -m pip install -c requirements.lock .
python -m epl_agent demo
```

打开[本地比赛看板](http://127.0.0.1:5051)。按比赛周浏览，或输入球队中英文名。只有观察到合格赔率的比赛会出现市场对照。待定的开球时间及尚未确认的比分会单独标注。

新安装可以离线浏览打包的比分。要看最新赛程，联网运行 `python -m epl_agent sync`，再刷新看板。同步使用两份免费赛程，时间核实后才展示对应的赛前预览。

[手机赛程](docs/screenshots/matchdesk-mobile.png) · [赛前详情](docs/screenshots/match-preview-mobile.png) · [完场比分](docs/screenshots/match-result-mobile.png)

## 核对一场比赛的全流程

研究成绩页和下列命令可核对赛程版本、实际冻结、公开见证和赛后结算。模型 A 的前瞻成绩包括按时见证并通过审核的独立预测；同期报价足够的比赛还进入市场与模型 B 的配对成绩。真实比赛链接以成绩页的已核验记录为准。

```sh
python -m epl_agent status
python -m epl_agent match trace 2026-27_fulham_manchester-united
```

Windows 维护者可用任务计划程序在开机后自动运行采集。部署入口、备份及回滚步骤见[采集运维与两步验收](docs/EVALUATION.md#采集运维与两步验收)。

## 用同一份数据复现

```sh
python -m epl_agent predict 2026-27_fulham_manchester-united
python -m epl_agent ask "这场为什么更看好客队" --fixture 2026-27_fulham_manchester-united
python -m epl_agent backtest --recompute --output scores.json
python -m epl_agent backtest --formal
```

历史评估采用逐赛季向前训练。最初两个赛季用于积累历史，打包快照另含 2026/27 赛季已记录的 50 场结果，总计 **9,170 场历史重建**。历史统计与前瞻市场对照在成绩页中分别展示。数据版本固定，当前赛程的新鲜度取决于上游更新。

| 命令 | 用途 |
|---|---|
| `python -m epl_agent sync` | 免费同步当季开放赛程，保留观察时间与改期版本 |
| `python -m epl_agent train` | 重新训练独立模型，生成回放和评分，将完整版本设为本地活动模型 |
| `python -m epl_agent register` | 在本机冻结正式评测协议和模型版本 |
| `python -m epl_agent worker` | 前台运行采集服务，每小时同步赛程，每分钟检查 T−24h 窗口 |
| `python -m epl_agent ledger` | 输出可公开的预测、时间证据和缺项原因 |
| `python -m epl_agent snapshot export public-export` | 输出带校验值、赛程分母和修订的公开数据包 |
| `python -m epl_agent snapshot verify public-export` | 按同一报告时点复算，核对独立指定的见证 issue |
| `python -m epl_agent export site` | 导出可在静态服务器运行的交互历史版 |

本地设置、模型和原始盘口默认写入用户主目录的 `.epl-prediction-agent`，可通过 `EPL_AGENT_HOME` 切换。采集赔率需要将个人免费密钥设为 `ODDS_API_KEY`。历史训练和回放全程可离线。官方比较的公开时间见证需由仓库维护者单独开启，详见[操作与评测说明](docs/EVALUATION.md)。

## GitHub Pages

公开站从 `epl-v1` 构建，采集数据发布到 `public-data`。站点提供分季回放、中文问答和可下载的校准与逐场成绩。维护者在采集机运行 `python -m epl_agent worker --publish --publish-snapshot`。原始书商报价保存在本地。独立部署、试运行验证和需设置的环境变量见[维护步骤](docs/EVALUATION.md)。

历史比分来自 openfootball（CC0）。当季合并数据库注明 OpenLigaDB 并按 ODbL 1.0 提供，可在已核验数据包部署的站点下载[当季合并数据库](https://lanthonyj.github.io/worldcup_prediction/merged-seasons.json)。[OpenLigaDB 许可](https://www.openligadb.de/en/license)
