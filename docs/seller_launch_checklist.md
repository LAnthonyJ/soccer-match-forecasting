# Limited-release seller checklist

## Before launch

Open https://app.<your-domain>/h5#/login and verify the Railway volume at /app/data. Configure SPORTMONKS_API_KEY, BETA_AUTH_SECRET, PAYMENT_WEBHOOK_SECRET and DEEPSEEK_API_KEY. Run the launch smoke test against the custom domain. Check login, search, forecasts and the analysis available to basic and VIP accounts.

## Issue access codes

Generate codes in the cloud environment so they are recorded in /app/data/auth/card_codes.json.

### Basic codes

```bash
python src/auth/card_cli.py generate --tier basic --days 7 --count 10 --batch taobao-20260701 --platform taobao --output /app/data/basic_week.csv
```

### VIP codes

```bash
python src/auth/card_cli.py generate --tier vip --days 30 --count 10 --batch xianyu-20260701 --platform xianyu --output /app/data/vip_month.csv
```

### Export delivery CSV files

```bash
python src/auth/card_cli.py issue-batch --tier basic --days 7 --count 20 --username-prefix wc --start-index 1 --batch taobao-basic-001 --platform taobao --output /app/data/exports/wc_basic.csv
python src/auth/card_cli.py issue-batch --tier vip --days 30 --count 20 --username-prefix vip --start-index 1 --batch xianyu-vip-001 --platform xianyu --output /app/data/exports/wc_vip.csv
```

### Inspect or disable a code

```bash
python src/auth/card_cli.py show --code <code>
```

```bash
python src/auth/card_cli.py disable --code <code>
```

## Product listing

Describe the product as a World Cup match analytics membership, score probability trial, or pre-match forecast subscription. Avoid promises of guaranteed returns or language suggesting a lottery or betting advice service.

## Delivery template

```text
Login URL: https://app.<your-domain>/h5#/login
Username: {buyer_username}
Access code: {card_code}
Tier: {basic/vip}
Expires: {expires_at}
This service provides match analysis. Results are uncertain.
```

## Daily checks

Check GET /health/live, search for a World Cup fixture and open its forecast. Verify that /app/data/ai_analysis contains recent VIP analysis and review access codes for unexpected reuse.
