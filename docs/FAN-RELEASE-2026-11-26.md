# 英超观察台球迷功能验收 · 2026 年 11 月 26 日

本次固定数据截至北京时间 11 月 25 日 23:59:09，包含 2026/27 赛季第 12 轮全部十场。以下概率属于赛前预览。各场实际冻结和公开见证以当场账本为准。[固定数据提交](https://github.com/LAnthonyJ/worldcup_prediction/commit/812f04285f30c1a5867a904b8f92288cb70d484b) · [候选版代码](https://github.com/LAnthonyJ/worldcup_prediction/commit/b47f4ca32d5e2f43e24439b2e40cfcee8d80ae19)

## 已通过

| 检查 | 预期 | 实测与证据 |
|---|---|---|
| 问答与推荐 | 八类各六题，三端对应比赛、时点、数值或具体缺项全部正确。整轮伤停推荐为零 | 48 题在 Flask、静态页、模拟生产网关共 144 个答案均通过。10 场各有 3 个推荐。静态和 Flask 各实点 30 个，网关逐个核对 30 个，全部匹配。含候选选择的整套矩阵共 206 项通过。[案例](../tests/browser/fan_cases.mjs) · [三端测试](../tests/browser/fan_matrix.mjs) |
| 跨场指代 | 在森林页留在原位，阿森纳下一场与“那场”均指向曼城；明确说“本页”后回到森林 | 阿森纳对曼城为北京时间 11 月 30 日 00:30，森林对切尔西为 11 月 28 日 04:00。多个候选需选择，选完沿用原提问。[森林比赛页](https://lanthonyj.github.io/worldcup_prediction/#/match/2026-27_nottingham-forest_chelsea) |
| 赛前判断、最近五场、交锋与积分 | 预测、赛后记录与当前战绩可区分，按各自的截止时点复现 | 森林主胜 37.7%、平 27.3%、客胜 35.0%；前三个比分为 1–1（13.0%）、2–1（8.5%）、1–2（8.1%）。手算小赛季覆盖并列、补赛、跨季、冲突及撤销。9 月 10 日仍读到 1–1，9 月 14 日读到已更正的 2–1。[历史事实回归](../tests/test_team_history.py) |
| 赛后更新 | 确认比分进入新版可读数据后，前台 60 秒内显示 | 浏览器模拟来源迟到 48 小时，45 秒检查发现新版；断网后重连、比分冲突、更正均保留原链接和筛选。生命周期测试校验两版下载字节及既有冻结哈希。[浏览器回归](../tests/browser/ui.cjs) · [冻结至结算测试](../tests/test_lifecycle_e2e.py) |
| 干净安装与原有功能 | 两套系统免密钥安装、运行、读取真实比赛，兼容旧入口 | Windows、Ubuntu 各 93 项后端回归通过。Windows 离线 wheel、Ubuntu 干净克隆均能输出真实比赛概率。三引擎检查覆盖 360、768、1440 像素。网关 45 项回归通过。[CI](https://github.com/LAnthonyJ/worldcup_prediction/actions/runs/45785198047) |
| 移动弱网首屏 | 每种入口十次冷启动，赛程和首条判断两项 P95 均 ≤2.5 秒 | 最终构建的实时版两项均 2.307 秒，固定版两项均 2.110 秒。每次独立 Edge 上下文、360 × 800、CPU 四倍减速、RTT 120 毫秒、下载 200 KB/s、上传 90 KB/s。P95 为每组第十项。[测试方法](FAN-ACCEPTANCE.md) |
| 匿名打开公网 | 首页、旧问答地址、赛前与完场分享链接可直接打开 | 已用匿名浏览器访问全部四条真实路由，核对第 12 轮赛程、赛前问答及完场比分。[部署](https://github.com/LAnthonyJ/worldcup_prediction/actions/runs/45785197987) · [旧问答入口](https://lanthonyj.github.io/worldcup_prediction/#/ask) · [完场分享](https://lanthonyj.github.io/worldcup_prediction/#/match/2026-27_brighton-hove-albion_brentford) |

两组冷启动的逐次可见时间如下。每次赛程与首条判断在同一帧出现，单位为毫秒。

| 入口 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 本机实时版 | 2307 | 2055 | 2065 | 2037 | 2015 | 2017 | 2071 | 2028 | 2036 | 2041 |
| 固定静态版 | 1996 | 2035 | 2110 | 2051 | 2051 | 2018 | 2035 | 2079 | 2055 | 2029 |

固定文件的 SHA-256 为 `a971c647e8eacaf590ba59fb2199aa7b3bed88727dcf858f7dea380a2e80fe0a`。[下载核验输入](https://raw.githubusercontent.com/LAnthonyJ/worldcup_prediction/812f04285f30c1a5867a904b8f92288cb70d484b/snapshot.json)

匿名复算覆盖首页摘要、轮次列表、十场详情、快照下载和赛后成绩。三项概率在页面精度内逐项相同。Windows 和 Ubuntu 重算预览的最大原始浮点差为 1.11 × 10⁻¹⁶。核验器对预览同时检查显示值及 10⁻¹⁵ 原始误差上限，对正式冻结的原始概率逐项精确比较。Chromium、WebKit、Edge 各 13 项浏览器回归通过，GitHub Actions 的 Windows 与 Ubuntu 两种 Python 版本共四组后端检查通过。[逐场核验器](../scripts/verify_public_round.py) · [匿名页面检查](../scripts/verify_public_routes.cjs)

采集库经一致性备份后，Windows 计划任务已切换到验收过的离线包。11 月 26 日 16:49:33 的下一批数据由采集机自动上传。16:54:33 匿名浏览器通过四条路径，16:55:07 完成十场逐项复算，正式和试运行市场配对均为 0。新快照 SHA-256 为 `8f761694b2d3fa2a45f66163cd5a13a9cb74f37c1580e4eedf71c713ae3656fb`。相邻两批各有可下载的固定提交。[自动上传的数据](https://github.com/LAnthonyJ/worldcup_prediction/commit/a4f9d4e370b58073e73c92aad41c1727f6b42527) · [自动部署](https://github.com/LAnthonyJ/worldcup_prediction/actions/runs/45786486524)

独立核对当前线上批次时，先下载数据分支固定提交的 `snapshot.json` 到一个目录，在安装了项目的环境运行下列命令。第二条命令使用已安装的前端 Playwright 浏览器。

```sh
python scripts/verify_public_round.py ./downloaded-snapshot
node scripts/verify_public_routes.cjs
```

## 未通过

本次固定批次的自动化功能、性能和公网逐场核验均达到表中门槛。下表跟踪需要设备、人员、生产凭据或真实比赛才能取得的证据。

## 等待外部条件

| 项目 | 当前证据 | 放行条件 |
|---|---|---|
| 实机和首次使用者 | 桌面引擎及手机尺寸模拟已测；真实设备与参与者记录空缺 | iPhone Safari、Android Chrome 跑通完整流程。5 名首次使用者每项至少 4 人在 30 秒内独立完成，跨场辨认 5/5。[记录表](FAN-ACCEPTANCE.md) |
| 生产 DeepSeek、公网三网 | 本批次公开的是基础数据解读。离线网关测试覆盖计费、并发和故障 | 生产域名验证共享报告、追问、模型和用量，每日每人 5 次，月预算 20 + 30 元。三网各连续 7 天 ≥99.5%，上传后 10 分钟内可读且每日有真实 AI 成功记录。[监测标准](MONITORING.md) |
| 实赛前瞻 A | 固定批次内已计分 0 场 | 至少一场完成准时冻结、准时公开见证、确认终场和匿名评分，留下比赛、见证和固定提交三个链接。[公开账本](https://github.com/LAnthonyJ/worldcup_prediction/issues/1) |
| 市场配对与正式版 | 试运行和正式样本中市场配对均为 0 场 | 取得合格的三家赛前报价并完成第三方复算，才发布 v1.0.0。市场领先结论依原注册的两季门槛判定。[评测协议](EVALUATION.md) |
