# 国内三网七天验收

在电信、联通、移动各使用一个实际接入点。首次运行当天可以检查部署时延和故障原因。正式结论需要三个接入点都有连续七个完整 UTC 日的观测记录。目前尚无实网验收结果。

## 部署探针

在每台探针安装本项目的 Python 依赖，并在 `frontend` 目录执行 `npm ci` 和 `npx --no-install playwright install chromium`。给每台探针独立生成至少 32 个随机字符的签名密钥，保存到该探针的私有环境变量。三个变量分别为 `EPL_MONITOR_TELECOM_KEY`、`EPL_MONITOR_UNICOM_KEY`、`EPL_MONITOR_MOBILE_KEY`。汇总机需要对应的三把密钥。

在已部署的 Cloudflare 生产域名运行探针。每台机器使用相同的域名，指定自己的网络和 SQLite 路径。以下是电信探针的单轮命令，`SITE_ORIGIN` 是公开站点的 HTTPS 根地址。

```sh
python scripts/monitor_public.py sample --network telecom --db telecom.sqlite3 --origin "$SITE_ORIGIN" --browser
```

通过探针所在主机的定时任务每 5 分钟启动一次，从 UTC 零点之前持续到第八天零点后 10 分钟。设置稳定的工作目录、时钟同步和直连网络。每个时段只有一次采样，进程中断会留下缺测记录。运营商检查读取同域 `/api/monitor/network` 的边缘国家代码和 ASN，记录经过签名的运营商编号；原始 IP、输入文本和模型回复均不进入观察库。[Cloudflare 请求元数据](https://developers.cloudflare.com/workers/runtime-apis/request/#incomingrequestcfproperties)

每轮验证应用资源、本轮预测的事实哈希、公开批次 SHA 和快照下载。每个接入点每天首个健康时段另外发起一次普通访客追问，计入应用每日限额及月预算。`--browser` 会在浏览器里打开比赛、阅读报告、追问、核对本场证据，再下载一个赛季的真实 CSV。在未启用 DeepSeek 时，模板答复单独计数。

## 汇总报告

从采集机导出本次观察期间的首次上传回执。将三个探针的 SQLite 一致副本复制到汇总机；正在写入时可用 SQLite 的备份接口生成副本。

```sh
python -m epl_agent upload-times > uploads.json
python scripts/monitor_public.py report --db telecom.sqlite3 unicom.sqlite3 mobile.sqlite3 --origin "$SITE_ORIGIN" --end YYYY-MM-DD --uploads uploads.json --require-pass
```

`--end` 是窗口结束日的 UTC 零点，不含该日；Windows PowerShell 5 创建 JSON 文件时可用 `python -m epl_agent upload-times | Out-File -Encoding ascii uploads.json`。在每个网络的 2,016 个时段中，至少 2,006 个需要健康。每个上传 SHA 必须在 600 秒内从每个网络读到。程序另报 AI 请求数、真实 DeepSeek 成功数、回退数、失败数和未获得实时回答的比例。零请求时该比例为空。正式验收检查每个网络七天都有真实 AI 成功，并至少有一次浏览器流程完成。探针退出、身份不符、数据包缺失、上传后未能读到均可在汇总中定位。

归档报告、上传回执和具体观察区间。切换接入点、域名或运营商时保留分组。国内可达性、合格市场配对和累计模型优势是三个独立的验收结果。

## D1 备份和恢复演练

先记录当前公共数据 SHA、代码提交和部署 ID。暂时将 `EPL_PRICING_VALID_UNTIL` 清空并部署到生产域名，确认新付费调用已停、进行中的请求已完成。已有报告及静态数据仍可读取。在私有备份目录保留账本，里面含每日匿名访客标识与预算预留记录。

在 `cloudflare` 目录用已配置的维护身份导出 `DB`，导入单独准备的临时空数据库，再导出恢复副本。下面三个动作分别以生产数据库、演练数据库、演练数据库为目标；运行前核对 Wrangler 显示的数据库名称和 ID。[D1 导入与导出](https://developers.cloudflare.com/d1/best-practices/import-export-data/)

```sh
npx --no-install wrangler d1 export DB --remote --output="$BACKUP_DIR/original.sql"
npx --no-install wrangler d1 execute "$RESTORE_TEST_DB" --remote --file="$BACKUP_DIR/original.sql"
npx --no-install wrangler d1 export "$RESTORE_TEST_DB" --remote --output="$BACKUP_DIR/restored.sql"
python ../scripts/verify_analysis_restore.py "$BACKUP_DIR/original.sql" "$BACKUP_DIR/restored.sql"
```

核验器逐行比对两份导出中的报告、事实哈希、每日访客计数依据、已计费用和未结预留，以摘要输出。上线前记录实际演练输出。在没有服务凭据的开发环境可运行 `python -m unittest discover -s tests -p test_monitor_public.py -v` 演练本地 SQLite 的恢复和错账检测。

回退应用时使用此前核验成功的代码及其静态制品，继续绑定当前计量账本。确认两站的 `public-data-commit.txt` 指向预期数据，抽查本场证据与共享报告，核对已计费用及待结预留后再恢复收费调用。Windows 采集机的快照备份、冻结见证和单实例回滚步骤见[采集运维与两步验收](EVALUATION.md#采集运维与两步验收)。
