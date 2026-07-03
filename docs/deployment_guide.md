# Deployment Guide

## Runtime

The deployable unit is the Flask API plus static H5 frontend.

Production import target:

```text
src.server:app
```

## Local Start

```powershell
python -m pip install -r requirements.txt
python src/api/app.py
```

Open:

```text
http://localhost:5000/h5
```

## Linux Production Start

```bash
python -m pip install -r requirements.txt
APP_ENV=prod FLASK_DEBUG=0 gunicorn "src.server:app" --bind "0.0.0.0:${PORT:-5000}" --workers 2 --timeout 120
```

## Required Environment

```text
APP_ENV=prod
APP_BASE_URL=https://your-domain.example
DATA_DIR=/srv/worldcup-predict/data
ENABLE_H5=true
PORT=5000
FLASK_DEBUG=0
SPORTMONKS_API_KEY=<provider-token>
BETA_AUTH_SECRET=<32+ char random string>
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=<32+ char random string>
PAYMENT_RETURN_URL=https://your-domain.example/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
ENABLE_AI_ANALYSIS=true
DEEPSEEK_API_KEY=<deepseek-token>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

## Docker Start

The repository includes a platform-neutral `Dockerfile` for Railway, Render, and Zeabur.

Build locally:

```bash
docker build -t worldcup-predict .
```

Run locally:

```bash
docker run --rm -p 5000:5000 \
  -e APP_ENV=prod \
  -e APP_BASE_URL=http://localhost:5000 \
  -e DATA_DIR=/app/data \
  -e ENABLE_H5=true \
  -e FLASK_DEBUG=0 \
  -e BETA_AUTH_SECRET=local-beta-secret-local-beta-secret \
  -e PAYMENT_WEBHOOK_SECRET=local-webhook-secret-local-webhook \
  -e ENABLE_DEV_PAYMENT_WEBHOOK=false \
  -e SPORTMONKS_API_KEY=<provider-token> \
  worldcup-predict
```

Open:

```text
http://localhost:5000/h5#/login
```

## Railway / Render / Zeabur

Use Docker deployment for the first public link. All three platforms can run the same image.

Common settings:

- Build type: Dockerfile
- Start command: use the Dockerfile `CMD`
- Public port: platform-provided `$PORT`
- Persistent disk / volume mount: `/app/data`
- Health check path: `/health/live`

Required cloud environment:

```text
APP_ENV=prod
APP_BASE_URL=https://<your-platform-domain>
DATA_DIR=/app/data
ENABLE_H5=true
FLASK_DEBUG=0
SPORTMONKS_API_KEY=<provider-token>
BETA_AUTH_SECRET=<32+ char random string>
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=<32+ char random string>
PAYMENT_RETURN_URL=https://<your-platform-domain>/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
ENABLE_AI_ANALYSIS=true
DEEPSEEK_API_KEY=<deepseek-token>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Platform notes:

- Railway: create a service from GitHub, select Dockerfile deployment, add a Volume mounted at `/app/data`.
- Render: create a Web Service with Docker runtime, add a Persistent Disk mounted at `/app/data`.
- Zeabur: deploy the GitHub repo with Dockerfile, add a Volume mounted at `/app/data`.

Do not sell from a deployment without a persistent disk. Card codes and subscriptions are stored under `DATA_DIR`.

For a step-by-step fixed-link launch checklist, see `docs/cloud_platform_launch.md`.
For the lowest-cost custom domain setup with Railway + Cloudflare, use `app.<your-domain>` and follow `docs/cloud_platform_launch.md`.

## Cloudflare Pages Frontend

Use Cloudflare Pages only for the static H5 frontend. Keep the Flask API on Railway, Zeabur, Tencent Cloud, or another Python host.

Pages build settings:

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

Behavior:

- Static files are published from `dist/`.
- `frontend/index.html` redirects `/` to `/h5#/login`.
- `functions/api/[[path]].js` proxies same-domain `/api/*` requests to `API_ORIGIN`.
- Do not set `API_BASE_URL` when using the Pages proxy path. Leave the frontend on relative `/api/...`.

Validation:

```text
https://your-pages-domain.example/h5#/login
https://your-pages-domain.example/api/health/live
```

## Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.example;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Smoke Test

Local Flask test-client smoke:

```powershell
python src/smoke_test_launch.py
```

Live URL smoke:

```powershell
python src/smoke_test_launch.py --base-url https://your-domain.example
```

Cloud URL smoke:

```powershell
python src/smoke_test_launch.py --base-url https://<your-platform-domain>
```

## Rollback

Rollback means serving the previous app version with the same `DATA_DIR`.

Before rollback:

- Preserve `data/billing`.
- Preserve `data/prediction_snapshots`.
- Preserve `data/jobs`.
- Do not delete paid subscription records.

After rollback:

```powershell
python src/smoke_test_launch.py --base-url https://your-domain.example
```

## Sales Notes

- Sell card codes on third-party platforms and keep the website login-only.
- Do not expose API keys, session tokens, or bulk card-code exports in logs.
- Rotate `SPORTMONKS_API_KEY` before production if it was ever committed, screenshotted, or pasted externally.
