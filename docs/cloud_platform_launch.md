# Railway, Render and Zeabur deployment

The recorded deployment uses Railway with the repository Dockerfile and railway.json. Zeabur offers a Chinese-language admin console. Lower-cost Render instances may cold-start. Use a fixed public login URL when delivering accounts to customers.

## Railway setup

Push the project to GitHub, select Dockerfile deployment, and mount the persistent volume at /app/data. Configure the following variables using values from the appropriate providers.

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

Generate a random secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open /health/live and /h5#/login on the Railway service domain. Once both work, add app.<your-domain> as a custom domain on the same service. The documented setup uses Cloudflare Registrar and Cloudflare DNS. Configure only the app subdomain. In Cloudflare, create the CNAME named app pointing to the Railway-supplied target and the TXT record using Railway's verification name and value. Set the CNAME to DNS only during verification.

After Railway verifies the domain, update the public URLs and redeploy:

```text
APP_BASE_URL=https://app.<your-domain>
PAYMENT_RETURN_URL=https://app.<your-domain>/h5#/account
```

## Issue codes and verify the service

Generate codes in the hosted environment so they are saved to /app/data/auth/card_codes.json. Use the following commands to generate and export batches.

```bash
python src/auth/card_cli.py generate --tier basic --days 7 --count 20 --batch taobao-basic-week-001 --platform taobao --output /app/data/basic_week.csv
python src/auth/card_cli.py generate --tier vip --days 30 --count 20 --batch xianyu-vip-month-001 --platform xianyu --output /app/data/vip_month.csv
```

```bash
python src/auth/card_cli.py issue-batch --tier basic --days 7 --count 20 --username-prefix wc --start-index 1 --batch taobao-basic-001 --platform taobao --output /app/data/exports/wc_basic.csv
python src/auth/card_cli.py issue-batch --tier vip --days 30 --count 20 --username-prefix vip --start-index 1 --batch xianyu-vip-001 --platform xianyu --output /app/data/exports/wc_vip.csv
```

Run the smoke test for the production domain:

```bash
python src/smoke_test_launch.py --base-url https://app.<your-domain>
```

Confirm that /health/live returns HTTP 200 and JSON, the H5 login page loads, the basic tier can read forecasts and VIP can access extended data and AI analysis. The historical launch checklist included testing on mainland Chinese mobile and residential networks.

## Customer delivery template

```text
Login URL: https://app.<your-domain>/h5#/login
Username: {buyer_username}
Access code: {card_code}
Tier: {basic/vip}
This service provides match data analysis.
```

## Cloudflare Pages + Pages Functions

Use Cloudflare Pages only for the static H5 layer. Keep the Flask API on Railway, Zeabur, Tencent Cloud, or another Python host, and use Pages Functions as a same-domain `/api/*` proxy.

Pages settings:

```text
Framework preset: None
Root directory: soccer_prediction module
Build command: npm run build
Build output directory: dist
```

Pages environment:

```text
API_ORIGIN=https://your-api-domain.example
```

Proxy behavior:

- `/` redirects to `/h5#/login`
- `dist/` publishes the static H5 bundle
- `functions/api/[[path]].js` forwards `/api/*` requests to `API_ORIGIN`
- The frontend keeps using relative `/api/...` calls, so the browser sees a single domain
