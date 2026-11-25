# 英超赛事分析 · Cloudflare Pages Functions

Python 从已核验数据导出静态页面和逐场事实。Pages Functions 使用同站的 ASSETS 读取这些事实，并以 D1 记录共享报告与实际调用预留。首次运行无需 DeepSeek 密钥即可浏览资料及模板。

## 配置部署

1. 维护者在自己的 Cloudflare 账户创建一个 Pages 项目和 D1 数据库。预览环境使用独立的测试数据库；生产账本可供生产域名及别名共享。
2. 在项目根目录执行已核验的导出流程，然后运行 `python scripts/analysis_release.py cloud <导出目录> --target cloudflare/site`。进入 `cloudflare/` 并运行 `npm ci`。Node.js 24 和 Wrangler 的锁版本用于 CI 与部署。
3. 设置环境变量 `EPL_CF_PAGES_PROJECT`、`CLOUDFLARE_D1_DATABASE_ID` 和 `EPL_PRICING_VALID_UNTIL`（最近核对费率后批准使用至 YYYY-MM-DD，可留空关闭付费推理）。运行 `npm run configure`，它读取已构建的 site，生成本地 wrangler.toml 并安装路由。
4. 在生产 Pages 项目通过 Secrets 配置 `DEEPSEEK_API_KEY` 和至少 24 个随机字符的 `VISITOR_HMAC_SECRET`。Wrangler 部署身份需要目标项目 Pages 和 D1 的相应权限。CI 的 `CLOUDFLARE_API_TOKEN` 和 `CLOUDFLARE_ACCOUNT_ID` 仅用于部署。
5. 从同目录运行 `npx --no-install wrangler d1 migrations apply DB --remote`，然后运行 `npx --no-install wrangler pages deploy site --project-name <项目名>`。本地测试用 `npm test`；可用 `wrangler pages functions build functions` 独立编译。

## 接口与可追溯性

- `GET /api/matches/{id}/analysis` 先读取共享报告。只有 current.json 所列、确实有预测的本轮比赛可以在冷启动时触发推理。历史比赛支持同哈希缓存和基础数据解读。
- `GET /api/matches/{id}/analysis?cached=1` 供 GitHub Pages 镜像获取已通过核验的共享报告。没有缓存返回 404。它不会生成报告。
- `POST /api/analysis/ask` 接收 `fixture_id`、`fact_hash`、1–350 字问题、可选时区和最多三轮历史。当前版本变化时返回 409。返回的 `source` 标识 deepseek 或 template，`as_of` 是数据截止，`generated_at` 是生成时间，`generated_as_of` 保留共享报告最初的数据时点。
- 报告从 `analysis/facts/{id}.json` 或 `analysis/archive/{season}.json` 读取。哈希覆盖 schema、fixture_id、revision、source、facts，按递归排序键、UTF-8 紧凑 JSON 计算 SHA-256。only-HTML 的未知路由回退不视为事实文件。赛季与赛程读取 `analysis/catalog.json`；明确要求赛后复盘时读取独立的 `analysis/reviews/{season}.json`。
- DeepSeek Flash 的非思考模式通过四个只读函数选择并排列事实。服务端核对引用属于所选比赛、赛前或赛后视图和指定工具，再从原事实渲染正文。报告的核心概率、球队近况、预计进球、市场和资料边界都随现有数据呈现。无效的工具名、引用或来源触发模板回退。供应商收不到完整账本、赔率密钥、访问者 IP 或客户端提交的助手回复。

## 预算与隐私

D1 每次付费请求用单条有条件的 INSERT 预留最坏成本，同一事务判断 UTC 自然月的报告 20 元、追问 30 元、合计 50 元上限。使用整数微元。按输入 UTF-8 字节加工具消息余量和 480 个输出 token 预留，按核准的缓存未命中高峰价格结算。当前固定 Flash 计价为输入 2 元/百万 token、输出 8 元/百万 token。若费率、模型或消息封装变化，应先更新边界和测试，再延长资费核准日。余额不足或费率未批准时返回模板。经代理到同一出口的用户共享每日 5 次实时追问，模板和只读查询不扣调用次数。IP 按天 HMAC 化，只记录摘要。未知、过大或缺失的 token 用量保留完整预留。网络失败后不自动重试付费请求。D1 存储请求时间、匿名摘要、预留与结算微元和成功报告，用户问题与完整对话不落库。[DeepSeek 定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) · [DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/) · [D1 API](https://developers.cloudflare.com/d1/worker-api/d1-database/) · [Pages 绑定](https://developers.cloudflare.com/pages/functions/bindings/)
