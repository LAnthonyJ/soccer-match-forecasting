# 小范围售卖清单

## 上线前

- 固定链接：确认 `https://app.<你的域名>/h5#/login` 可以正常打开
- 环境变量：确认已配置 `SPORTMONKS_API_KEY`、`BETA_AUTH_SECRET`、`PAYMENT_WEBHOOK_SECRET`、`DEEPSEEK_API_KEY`
- 持久化：确认 Railway Volume 挂载到 `/app/data`
- 冒烟测试：运行 `python src/smoke_test_launch.py --base-url https://app.<你的域名>`
- 自测流程：分别用 `basic` 和 `vip` 卡密完成登录、搜索、预测、AI 分析

## 云端发卡

正式售卖卡密必须在云端环境生成，确保写入线上 `/app/data/auth/card_codes.json`。

生成基础版：

```bash
python src/auth/card_cli.py generate --tier basic --days 7 --count 10 --batch taobao-20260701 --platform taobao --output /app/data/basic_week.csv
```

生成 VIP：

```bash
python src/auth/card_cli.py generate --tier vip --days 30 --count 10 --batch xianyu-20260701 --platform xianyu --output /app/data/vip_month.csv
```

生成发货 CSV：

```bash
python src/auth/card_cli.py issue-batch --tier basic --days 7 --count 20 --username-prefix wc --start-index 1 --batch taobao-basic-001 --platform taobao --output /app/data/exports/wc_basic.csv
python src/auth/card_cli.py issue-batch --tier vip --days 30 --count 20 --username-prefix vip --start-index 1 --batch xianyu-vip-001 --platform xianyu --output /app/data/exports/wc_vip.csv
```

查询单张卡密：

```bash
python src/auth/card_cli.py show --code <code>
```

禁用异常卡密：

```bash
python src/auth/card_cli.py disable --code <code>
```

## 商品描述建议

- 标题避免使用“稳赚”“博彩”“彩票”“下注建议”等词
- 推荐标题：`世界杯赛事数据分析会员卡密`
- 推荐标题：`世界杯比分概率分析系统体验卡`
- 推荐标题：`世界杯赛前数据预测工具会员`

## 发货模板

```text
登录地址：https://app.<你的域名>/h5#/login
用户名：{buyer_username}
卡密：{card_code}
等级：{basic/vip}
有效期至：{expires_at}
说明：本服务仅提供赛事数据分析参考，不构成任何结果承诺。
```

## 日常巡检

- 查看 `GET /health/live`
- 抽查一场世界杯比赛的搜索和预测
- 检查 `/app/data/ai_analysis` 是否有最新 VIP 分析缓存
- 检查卡密是否存在异常重复使用
