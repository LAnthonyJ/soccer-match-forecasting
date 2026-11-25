# 英超观察台 · EPL Prediction Agent

[English](README.en.md) · [数据来源](docs/DATA.md) · [模型卡](docs/MODEL.md) · [市场验证协议](docs/EVALUATION.md)

**1.0 发布候选版** · [在线比赛看板](https://lanthonyj.github.io/worldcup_prediction/) · [赛前见证账本](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) · [试运行与正式成绩](https://lanthonyj.github.io/worldcup_prediction/#/report)

点击“看本轮”查看本周赛程及最新可用预测，“体验历史回放”会打开一场资料完整的比赛。首页列出比分资料日期、最后成功同步及超过开球时间 24 小时的待补场次。市场领先的正式结论需要预注册的两个完整赛季；当前赛季用于真实试运行，首场通过见证并完成结算的比赛可从成绩页打开。

选择一场英超，回看开球前一天的主胜、平局、客胜概率，查看最可能的比分，或用中文提问。发布包内含 **26 个完整赛季、9,880 场真实比分**和已构建的界面，首次体验无需 Node、账户或数据密钥。研究版正在积累带时间证据的赛前预测。

![英超比赛看板，展示富勒姆对曼联的真实历史回放](docs/screenshots/matchdesk-desktop.png)

## 本地运行

安装 Python 3.11–3.13，在下载或克隆的仓库根目录运行。推荐使用独立虚拟环境。

```sh
git clone -b epl-v1 --single-branch https://github.com/LAnthonyJ/worldcup_prediction.git epl-prediction-agent
cd epl-prediction-agent
python -m pip install -c requirements.lock .
python -m epl_agent demo
```

打开 [本地比赛看板](http://127.0.0.1:5051)。默认从一场有回放的比赛开始。选择赛季、比赛周或输入球队中英文名；展开“实际赛果”可核对结果。“预测助手”支持问“这场与市场分歧有多大？”以及“本周末阿森纳踢谁？”。只有观察到合格赔率的比赛会出现市场和融合模型数值。

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
