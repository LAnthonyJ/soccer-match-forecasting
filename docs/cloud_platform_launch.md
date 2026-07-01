# Railway / Render / Zeabur 固定链接上线执行单

目标：用托管平台直接拿到公网链接，例如 `https://xxx.up.railway.app/h5#/login`。买家付款后，只需要发送登录地址、用户名和卡密。

## 1. 选择平台

优先顺序：

1. Zeabur：中文界面，上手最轻。
2. Railway：Docker 部署体验好，调试方便。
3. Render：稳定，但低价层可能有冷启动。

首版只部署一个平台，避免多个线上数据目录不一致。

## 2. 部署前必须确认

- 代码已经推到 GitHub 仓库。
- 平台服务使用 Dockerfile 部署。
- 持久化磁盘/Volume 挂载到 `/app/data`。
- 不能在没有持久化磁盘的免费环境正式售卖，否则卡密和订阅会在重启后丢失。

## 3. 平台环境变量

在平台后台配置以下变量：

```text
APP_ENV=prod
APP_BASE_URL=https://你的平台域名
DATA_DIR=/app/data
ENABLE_H5=true
FLASK_DEBUG=0
SPORTMONKS_API_KEY=你的Sportmonks新key
BETA_AUTH_SECRET=至少32位随机字符串
BETA_SESSION_TTL_HOURS=720
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=至少32位随机字符串
PAYMENT_RETURN_URL=https://你的平台域名/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
ENABLE_AI_ANALYSIS=true
DEEPSEEK_API_KEY=你的DeepSeek key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

生成随机 secret：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`BETA_AUTH_SECRET` 和 `PAYMENT_WEBHOOK_SECRET` 必须不同。

## 4. Railway

1. New Project -> Deploy from GitHub repo。
2. Railway 自动识别 `railway.json` 和 `Dockerfile`。
3. Variables 填入第 3 节环境变量。
4. 添加 Volume，Mount Path 设置为 `/app/data`。
5. 部署成功后打开 `https://你的railway域名/h5#/login`。

## 5. Render

1. New -> Web Service -> 连接 GitHub 仓库。
2. Runtime 选择 Docker，或使用仓库里的 `render.yaml` Blueprint。
3. 添加 Persistent Disk，Mount Path 设置为 `/app/data`。
4. Environment 填入第 3 节环境变量。
5. 部署成功后打开 `https://你的render域名/h5#/login`。

## 6. Zeabur

1. New Project -> Deploy from GitHub。
2. 选择 Dockerfile 部署。
3. Variables 填入第 3 节环境变量。
4. 添加 Volume，Mount Path 设置为 `/app/data`。
5. 部署成功后打开 `https://你的zeabur域名/h5#/login`。

## 7. 线上验收

替换成你的公网域名后执行：

```bash
python src/smoke_test_launch.py --base-url https://你的平台域名
```

浏览器检查：

- `https://你的平台域名/health/live` 返回健康。
- `https://你的平台域名/h5#/login` 能打开登录页。
- 未登录打开 `https://你的平台域名/h5#/search` 会回到登录页。
- 未登录请求 `/api/search/fixtures` 返回 401。
- basic 卡密登录后能查询基础预测。
- vip 卡密登录后能查看 VIP 预测和 AI 分析。
- 比赛时间显示北京时间。
- Sportmonks 查询失败时页面给出明确错误，不会卡死。

## 8. 云端发卡

正式卡密必须在云端服务环境里生成，写入线上 `/app/data/auth/card_codes.json`。

基础版 7 天：

```bash
python src/auth/card_cli.py generate --tier basic --days 7 --count 20 --batch taobao-basic-week-001 --platform taobao --output /app/data/basic_week.csv
```

VIP 30 天：

```bash
python src/auth/card_cli.py generate --tier vip --days 30 --count 20 --batch xianyu-vip-month-001 --platform xianyu --output /app/data/vip_month.csv
```

查询批次：

```bash
python src/auth/card_cli.py list --batch taobao-basic-week-001
```

禁用卡密：

```bash
python src/auth/card_cli.py disable --code <code>
```

## 9. 发货模板

```text
登录地址：https://你的平台域名/h5#/login
用户名：{buyer_username}
卡密：{card_code}
等级：{basic/vip}
有效期：{duration}
说明：本服务仅提供赛事数据分析，不构成投注建议。
```

## 10. 上线后每日检查

- 打开 `/health/live`。
- 抽查 1 场世界杯比赛预测。
- 检查 `/app/data/auth/card_codes.json` 是否存在。
- 检查 `/app/data/ai_analysis` 是否有 VIP 分析缓存。
- 检查平台日志没有输出 API key、卡密批量列表、session token。
