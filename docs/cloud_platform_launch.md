# Railway / Render / Zeabur 固定链接上线执行单

目标：用托管平台拿到固定公网链接，例如 `https://app.your-domain.com/h5#/login`，买家付款后你只需要发送登录地址、用户名和卡密。

## 1. 平台选择

优先顺序：

1. Railway：当前项目已经有 `Dockerfile` 和 `railway.json`，部署路径最短。
2. Zeabur：中文后台，上手轻，但你当前已经在 Railway 上跑通了。
3. Render：稳定，但低价层可能有冷启动。

默认继续使用 Railway，不做平台切换。

## 2. 部署前确认

- 代码已经推到 GitHub 仓库。
- Railway 服务使用 Dockerfile 部署。
- 持久化 Volume 已挂载到 `/app/data`。
- 正式售卖不能依赖 Railway 默认域名，后续用自定义域名对外发货。

## 3. Railway 环境变量

Railway 中至少需要这些变量：

```text
APP_ENV=prod
APP_BASE_URL=https://app.your-domain.com
DATA_DIR=/app/data
ENABLE_H5=true
FLASK_DEBUG=0
SPORTMONKS_API_KEY=<provider-token>
BETA_AUTH_SECRET=<32+ char random string>
BETA_SESSION_TTL_HOURS=720
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=<32+ char random string>
PAYMENT_RETURN_URL=https://app.your-domain.com/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
ENABLE_AI_ANALYSIS=true
DEEPSEEK_API_KEY=<deepseek-token>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

生成随机 secret：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. Railway 基本验收

部署成功后先检查：

- `https://<railway-default-domain>/health/live`
- `https://<railway-default-domain>/h5#/login`

如果这两条都能打开，再进入自定义域名阶段。

## 5. 自定义域名

推荐方案：

- 域名注册：Cloudflare Registrar
- DNS：Cloudflare DNS Free
- 对外链接：`https://app.<你的域名>/h5#/login`

只绑定一个子域名 `app`，不处理裸域名，减少配置和排障成本。

## 6. Railway 绑定自定义域名

在 Railway 当前服务中添加：

```text
app.<你的域名>
```

Railway 会给你两条记录：

- 一条 `CNAME`
- 一条 `TXT` 验证记录

在 Cloudflare DNS 中按 Railway 提示添加：

- `Type: CNAME`
  `Name: app`
  `Target: <Railway 提供的 xxx.up.railway.app>`
  `Proxy status: DNS only`
- `Type: TXT`
  `Name: <Railway 提供的验证名>`
  `Value: <Railway 提供的验证值>`

默认先用 `DNS only`，不要开启 Cloudflare 代理。

## 7. 域名切换后的变量

Railway 自定义域名验证成功后，把这两个变量改成正式域名：

```text
APP_BASE_URL=https://app.<你的域名>
PAYMENT_RETURN_URL=https://app.<你的域名>/h5#/account
```

然后手动重新部署一次。

## 8. 云端发卡

正式卡密必须在云端环境生成，写入线上 `/app/data/auth/card_codes.json`。

普通生成：

```bash
python src/auth/card_cli.py generate --tier basic --days 7 --count 20 --batch taobao-basic-week-001 --platform taobao --output /app/data/basic_week.csv
python src/auth/card_cli.py generate --tier vip --days 30 --count 20 --batch xianyu-vip-month-001 --platform xianyu --output /app/data/vip_month.csv
```

发货导出：

```bash
python src/auth/card_cli.py issue-batch --tier basic --days 7 --count 20 --username-prefix wc --start-index 1 --batch taobao-basic-001 --platform taobao --output /app/data/exports/wc_basic.csv
python src/auth/card_cli.py issue-batch --tier vip --days 30 --count 20 --username-prefix vip --start-index 1 --batch xianyu-vip-001 --platform xianyu --output /app/data/exports/wc_vip.csv
```

## 9. 最终验收

替换成你的正式域名后执行：

```bash
python src/smoke_test_launch.py --base-url https://app.<你的域名>
```

浏览器检查：

- `https://app.<你的域名>/health/live` 返回 200 JSON
- `https://app.<你的域名>/h5#/login` 能打开登录页
- basic 卡密登录后可查基础预测
- vip 卡密登录后可看 VIP 数据和 AI 分析
- 用中国大陆手机流量和家宽各测试一次

## 10. 发货模板

```text
登录地址：https://app.<你的域名>/h5#/login
用户名：{buyer_username}
卡密：{card_code}
等级：{basic/vip}
说明：本服务仅提供赛事数据分析参考。
```
