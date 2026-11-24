# Step 10E H5 Paid Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a mobile-first H5 paid prediction frontend that consumes the hardened Step 10C API and turns prediction, VIP gating, checkout, and subscription status into a sellable user experience.

**Architecture:** Create a new static H5 app under `frontend/h5` and serve it through the existing Flask backend for first launch. Keep the frontend framework-free in Step 10E to avoid package/network risk; use modular vanilla JavaScript, CSS, and HTML. The frontend talks only to the Step 10C API envelope and does not call model code directly.

**Tech Stack:** Static HTML/CSS/JavaScript, existing Flask API, unittest for Python route/static serving tests, optional Playwright/browser smoke if available.

---

## Scope

Step 10E builds the first sellable H5 user interface.

In scope:

- Mobile-first H5 app.
- Match list page using `/api/matches/today`.
- Prediction detail page using `/api/predictions/<fixture_id>`.
- VIP detail state using `/api/predictions/<fixture_id>/vip`.
- Subscription page using `/api/me/subscription`.
- Checkout flow using `/api/billing/checkout`.
- Basic/VIP field gating in UI.
- Stable API client for Step 10C envelope.
- Static frontend serving from Flask.
- Frontend smoke tests and route tests.

Out of scope:

- Real WeChat/Alipay payment SDK.
- Mini-program native implementation.
- User password login.
- Complex admin dashboard.
- SSR or React/Vue migration.
- CDN deployment.

## Product Shape

The first screen should be the actual prediction product, not a marketing landing page.

Primary user flow:

1. User opens H5.
2. User id is loaded from local storage or entered through a compact identity panel.
3. App loads today/upcoming matches.
4. User opens a match.
5. Free/basic view shows HDA probabilities, top 3 scores, confidence, model version, data update time.
6. VIP prompt appears for heatmap, top 15 scores, market edge, BTTS, over/under, lineup explanation.
7. User selects a VIP plan and starts checkout.
8. Mock checkout creates pending order.
9. User can simulate payment success in dev mode.
10. Subscription status updates and VIP fields become visible.

## Page Map

Use a single-page H5 app with hash routes:

- `#/matches`: match list and today/upcoming predictions.
- `#/match/<fixture_id>`: prediction detail.
- `#/subscribe`: plan selection and checkout.
- `#/account`: subscription status.
- `#/admin-refresh`: optional admin-only refresh status page, hidden unless tier is admin.

## Component Map

Files under `frontend/h5`:

- `index.html`: shell, root containers, bottom navigation.
- `styles.css`: mobile-first visual system and responsive layout.
- `app.js`: router, bootstrapping, global state.
- `api.js`: Step 10C API envelope client.
- `state.js`: local storage user id, subscription cache, selected match.
- `views/matches.js`: match list page.
- `views/prediction.js`: prediction detail page.
- `views/subscribe.js`: VIP plan and checkout page.
- `views/account.js`: subscription/account page.
- `views/adminRefresh.js`: admin refresh status page.
- `components/layout.js`: app header, bottom nav, loading/error/empty states.
- `components/probability.js`: probability bars, confidence badge.
- `components/scorelines.js`: top scoreline list and VIP lock state.
- `components/vipGate.js`: locked section and upsell card.
- `components/toast.js`: small feedback messages.
- `fixtures/mockApi.js`: optional mocked API responses for static development.

Backend/static serving:

- Modify `src/api/app.py`: serve `/h5` and `/h5/<path>`.
- Create `tests/test_h5_static.py`: static route tests.

Docs:

- Create `docs/h5_frontend_contract.md`: route map, API mapping, state flow, launch checklist.

## API Mapping

All responses use Step 10C envelope:

```json
{
  "ok": true,
  "data": {},
  "meta": {}
}
```

Frontend client rule:

- If `ok === true`, return `data` and expose `meta`.
- If `ok === false`, throw an `ApiClientError` with `code`, `message`, `details`, and `status`.

Endpoints:

| UI action | Method | Endpoint | Header |
|---|---|---|---|
| Load matches | GET | `/api/matches/today?limit=50` | optional `X-User-Id` |
| Load basic prediction | GET | `/api/predictions/{fixture_id}` | optional `X-User-Id` |
| Load VIP prediction | GET | `/api/predictions/{fixture_id}/vip` | required `X-User-Id` with VIP |
| Load subscription | GET | `/api/me/subscription` | required `X-User-Id` |
| Create checkout | POST | `/api/billing/checkout` | required `X-User-Id` |
| Admin latest refresh | GET | `/api/admin/jobs/refresh/latest` | required admin user |

## State Flow

Global state:

```js
{
  userId: string | null,
  subscription: {
    tier: "free" | "basic" | "vip" | "admin",
    status: "none" | "active" | "expired" | "cancelled",
    expires_at: string | null
  } | null,
  currentRoute: string,
  lastError: null | { code: string, message: string },
  selectedFixtureId: string | null
}
```

Local storage keys:

- `wc_predict_user_id`: current user id.
- `wc_predict_last_fixture_id`: last opened match.
- `wc_predict_dev_mode`: `"1"` enables dev-only payment simulation UI.

State transitions:

- `anonymous -> free`: no `X-User-Id`; prediction basic endpoint can still load, subscription endpoint shows login prompt.
- `free -> vip_pending`: checkout order created.
- `vip_pending -> vip`: dev mock webhook or real provider callback later activates subscription.
- `vip -> expired/free`: subscription endpoint returns tier `free`.
- `vip -> admin`: only if backend resolves admin tier.

## Task 1: Static H5 Shell

**Files:**

- Create: `frontend/h5/index.html`
- Create: `frontend/h5/styles.css`
- Create: `frontend/h5/app.js`
- Test: manual browser open or Flask static test in Task 8

- [ ] **Step 1: Create `index.html`**

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>World Cup forecast</title>
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <div class="brand">World Cup forecast</div>
        <div class="subbrand">Probabilities, scorelines, goal totals and pre-match signals</div>
      </div>
      <button id="accountButton" class="icon-button" aria-label="Account">Account</button>
    </header>

    <main id="app" class="main-view">
      <section class="loading-view">Loading...</section>
    </main>

    <nav class="bottom-nav">
      <button data-route="#/matches">Matches</button>
      <button data-route="#/subscribe">VIP</button>
      <button data-route="#/account">Account</button>
    </nav>
  </div>
  <script type="module" src="./app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `styles.css`**

Use a quiet sports-data interface, not a marketing page:

```css
:root {
  --bg: #f4f6f2;
  --panel: #ffffff;
  --ink: #14201b;
  --muted: #617069;
  --line: #dce4dd;
  --accent: #0d7c66;
  --accent-2: #d18b22;
  --danger: #b42318;
  --radius: 8px;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Aptos", "Segoe UI", sans-serif;
  background:
    linear-gradient(135deg, rgba(13,124,102,.08), transparent 35%),
    linear-gradient(315deg, rgba(209,139,34,.10), transparent 30%),
    var(--bg);
  color: var(--ink);
}

button {
  font: inherit;
  border: 0;
  cursor: pointer;
}

.app-shell {
  max-width: 720px;
  min-height: 100vh;
  margin: 0 auto;
  padding-bottom: 72px;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  background: rgba(244,246,242,.92);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line);
}

.brand {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0;
}

.subbrand {
  margin-top: 2px;
  font-size: 12px;
  color: var(--muted);
}

.icon-button,
.primary-button,
.secondary-button {
  border-radius: var(--radius);
  padding: 10px 12px;
  font-weight: 700;
}

.icon-button,
.secondary-button {
  background: #e8eee9;
  color: var(--ink);
}

.primary-button {
  background: var(--accent);
  color: white;
}

.main-view {
  padding: 16px;
}

.section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin: 4px 0 12px;
}

.section-title h1,
.section-title h2 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 0;
}

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  margin-bottom: 12px;
  box-shadow: 0 8px 20px rgba(20,32,27,.05);
}

.match-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 8px;
}

.team {
  font-weight: 800;
  min-width: 0;
  overflow-wrap: anywhere;
}

.away { text-align: right; }

.kickoff {
  font-size: 12px;
  color: var(--muted);
}

.prob-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.prob-box {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 10px;
  background: #fbfcfa;
}

.prob-label {
  color: var(--muted);
  font-size: 12px;
}

.prob-value {
  margin-top: 4px;
  font-size: 20px;
  font-weight: 900;
}

.locked {
  border: 1px dashed #c7a66d;
  background: #fff8e8;
}

.bottom-nav {
  position: fixed;
  left: 50%;
  bottom: 0;
  width: min(720px, 100%);
  transform: translateX(-50%);
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  background: rgba(255,255,255,.96);
  border-top: 1px solid var(--line);
}

.bottom-nav button {
  padding: 14px 8px;
  background: transparent;
  color: var(--muted);
  font-weight: 800;
}

.empty,
.error,
.loading-view {
  padding: 28px 16px;
  text-align: center;
  color: var(--muted);
}

@media (min-width: 760px) {
  .main-view { padding: 24px; }
}
```

- [ ] **Step 3: Create bootstrap `app.js`**

```js
import { renderMatches } from "./views/matches.js";
import { renderPrediction } from "./views/prediction.js";
import { renderSubscribe } from "./views/subscribe.js";
import { renderAccount } from "./views/account.js";
import { state, loadState } from "./state.js";

const root = document.getElementById("app");

function route() {
  const hash = window.location.hash || "#/matches";
  if (hash.startsWith("#/match/")) {
    const fixtureId = hash.replace("#/match/", "");
    renderPrediction(root, fixtureId);
    return;
  }
  if (hash === "#/subscribe") {
    renderSubscribe(root);
    return;
  }
  if (hash === "#/account") {
    renderAccount(root);
    return;
  }
  renderMatches(root);
}

loadState();
document.getElementById("accountButton").addEventListener("click", () => {
  window.location.hash = "#/account";
});
document.querySelectorAll("[data-route]").forEach((button) => {
  button.addEventListener("click", () => {
    window.location.hash = button.dataset.route;
  });
});
window.addEventListener("hashchange", route);
route();
```

## Task 2: API Client And State

**Files:**

- Create: `frontend/h5/api.js`
- Create: `frontend/h5/state.js`
- Create: `frontend/h5/components/toast.js`

- [ ] **Step 1: Create API client**

`frontend/h5/api.js`:

```js
export class ApiClientError extends Error {
  constructor(payload, status) {
    super(payload?.error?.message || "API request failed");
    this.code = payload?.error?.code || "unknown_error";
    this.details = payload?.error?.details || {};
    this.status = status;
  }
}

function headers(userId) {
  const result = { "Content-Type": "application/json" };
  if (userId) result["X-User-Id"] = userId;
  return result;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: headers(options.userId),
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!payload.ok) throw new ApiClientError(payload, response.status);
  return { data: payload.data, meta: payload.meta };
}

export const api = {
  matches(userId) {
    return request("/api/matches/today?limit=50", { userId });
  },
  prediction(fixtureId, userId) {
    return request(`/api/predictions/${encodeURIComponent(fixtureId)}`, { userId });
  },
  vipPrediction(fixtureId, userId) {
    return request(`/api/predictions/${encodeURIComponent(fixtureId)}/vip`, { userId });
  },
  subscription(userId) {
    return request("/api/me/subscription", { userId });
  },
  checkout(userId, planId) {
    return request("/api/billing/checkout", {
      method: "POST",
      userId,
      body: { plan_id: planId },
    });
  },
};
```

- [ ] **Step 2: Create state module**

`frontend/h5/state.js`:

```js
export const state = {
  userId: null,
  subscription: null,
  selectedFixtureId: null,
};

export function loadState() {
  state.userId = localStorage.getItem("wc_predict_user_id");
  state.selectedFixtureId = localStorage.getItem("wc_predict_last_fixture_id");
}

export function setUserId(userId) {
  const cleaned = (userId || "").trim();
  state.userId = cleaned || null;
  if (state.userId) {
    localStorage.setItem("wc_predict_user_id", state.userId);
  } else {
    localStorage.removeItem("wc_predict_user_id");
  }
}

export function setSelectedFixture(fixtureId) {
  state.selectedFixtureId = fixtureId;
  localStorage.setItem("wc_predict_last_fixture_id", fixtureId);
}

export function isVip() {
  return state.subscription?.tier === "vip" || state.subscription?.tier === "admin";
}
```

- [ ] **Step 3: Create toast helper**

`frontend/h5/components/toast.js`:

```js
export function showToast(message) {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 2400);
}
```

Add to `styles.css`:

```css
.toast {
  position: fixed;
  left: 50%;
  bottom: 84px;
  transform: translateX(-50%);
  max-width: min(340px, calc(100% - 32px));
  background: #14201b;
  color: white;
  padding: 10px 12px;
  border-radius: var(--radius);
  box-shadow: 0 10px 28px rgba(0,0,0,.18);
  z-index: 20;
}
```

## Task 3: Shared Components

**Files:**

- Create: `frontend/h5/components/layout.js`
- Create: `frontend/h5/components/probability.js`
- Create: `frontend/h5/components/scorelines.js`
- Create: `frontend/h5/components/vipGate.js`

- [ ] **Step 1: Layout components**

`frontend/h5/components/layout.js`:

```js
export function loading(label = "Loading...") {
  return `<section class="loading-view">${label}</section>`;
}

export function emptyState(label) {
  return `<section class="empty">${label}</section>`;
}

export function errorState(error) {
  return `<section class="error">${error.message || "Request failed"}</section>`;
}

export function sectionTitle(title, action = "") {
  return `<div class="section-title"><h1>${title}</h1>${action}</div>`;
}
```

- [ ] **Step 2: Probability components**

`frontend/h5/components/probability.js`:

```js
function pct(value) {
  if (typeof value !== "number") return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function probabilityGrid(probabilities) {
  return `
    <div class="prob-grid">
      <div class="prob-box"><div class="prob-label">home win</div><div class="prob-value">${pct(probabilities.home)}</div></div>
      <div class="prob-box"><div class="prob-label">draw</div><div class="prob-value">${pct(probabilities.draw)}</div></div>
      <div class="prob-box"><div class="prob-label">away win</div><div class="prob-value">${pct(probabilities.away)}</div></div>
    </div>
  `;
}

export function confidenceBadge(confidence) {
  return `<span class="badge">Confidence ${(confidence * 100).toFixed(0)}%</span>`;
}
```

Add to `styles.css`:

```css
.badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 8px;
  background: #e3f3ed;
  color: #09634f;
  font-size: 12px;
  font-weight: 800;
}
```

- [ ] **Step 3: Scoreline components**

`frontend/h5/components/scorelines.js`:

```js
function pct(value) {
  return `${(value * 100).toFixed(2)}%`;
}

export function scorelineList(items = [], max = 3) {
  const rows = items.slice(0, max).map((item) => {
    const home = item[0];
    const away = item[1];
    const prob = item[2];
    return `<li><strong>${home}-${away}</strong><span>${pct(prob)}</span></li>`;
  }).join("");
  return `<ol class="scorelines">${rows}</ol>`;
}
```

Add to `styles.css`:

```css
.scorelines {
  list-style: none;
  padding: 0;
  margin: 0;
}

.scorelines li {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
```

- [ ] **Step 4: VIP gate component**

`frontend/h5/components/vipGate.js`:

```js
export function vipGate(title, body = "Opens the VIP to view the heat fractional effort, market differences, BTTS, balls and array effects.") {
  return `
    <section class="card locked">
      <h2>${title}</h2>
      <p>${body}</p>
      <button class="primary-button" data-route="#/subscribe">Activate VIP</button>
    </section>
  `;
}
```

## Task 4: Matches Page

**Files:**

- Create: `frontend/h5/views/matches.js`

- [ ] **Step 1: Implement matches view**

```js
import { api } from "../api.js";
import { state, setSelectedFixture } from "../state.js";
import { emptyState, errorState, loading, sectionTitle } from "../components/layout.js";

function matchCard(match) {
  return `
    <article class="card match-card" data-fixture-id="${match.sportmonks_id || match.fixture_id}">
      <div class="match-row">
        <div class="team">${match.home_team}</div>
        <div class="kickoff">vs</div>
        <div class="team away">${match.away_team}</div>
      </div>
      <div class="kickoff">${match.tournament || ""} · ${match.kickoff_utc || ""}</div>
    </article>
  `;
}

export async function renderMatches(root) {
  root.innerHTML = loading("Load Game...");
  try {
    const { data, meta } = await api.matches(state.userId);
    const warning = meta?.warning ? `<div class="card locked">Data sources are not available for the time being, showing an empty list.</div>` : "";
    if (!data.matches.length) {
      root.innerHTML = sectionTitle("Today's matches") + warning + emptyState("There's no show for the game");
      return;
    }
    root.innerHTML = sectionTitle("Today's matches") + warning + data.matches.map(matchCard).join("");
    root.querySelectorAll("[data-fixture-id]").forEach((node) => {
      node.addEventListener("click", () => {
        const fixtureId = node.dataset.fixtureId;
        setSelectedFixture(fixtureId);
        window.location.hash = `#/match/${fixtureId}`;
      });
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
```

## Task 5: Prediction Detail Page

**Files:**

- Create: `frontend/h5/views/prediction.js`

- [ ] **Step 1: Implement basic + VIP detail view**

```js
import { api } from "../api.js";
import { state, isVip } from "../state.js";
import { errorState, loading, sectionTitle } from "../components/layout.js";
import { probabilityGrid, confidenceBadge } from "../components/probability.js";
import { scorelineList } from "../components/scorelines.js";
import { vipGate } from "../components/vipGate.js";

function metaLine(data, meta) {
  return `
    <div class="kickoff">
      ${data.model_version || ""} · ${data.data_updated_at || data.generated_at || ""} · ${meta?.source || ""}
    </div>
  `;
}

function vipSection(data) {
  return `
    <section class="card">
      <h2>VIP data</h2>
      <div class="prob-grid">
        <div class="prob-box"><div class="prob-label">Over 2.5</div><div class="prob-value">${((data.over25 || 0) * 100).toFixed(1)}%</div></div>
        <div class="prob-box"><div class="prob-label">BTTS</div><div class="prob-value">${((data.btts || 0) * 100).toFixed(1)}%</div></div>
        <div class="prob-box"><div class="prob-label">Market differences</div><div class="prob-value">${data.market_edge ? "Yes" : "-"}</div></div>
      </div>
      <h3>Top 15 scorelines</h3>
      ${scorelineList(data.top_scorelines || [], 15)}
    </section>
  `;
}

export async function renderPrediction(root, fixtureId) {
  root.innerHTML = loading("Load Forecasts...");
  try {
    const basic = await api.prediction(fixtureId, state.userId);
    let vip = null;
    if (state.userId) {
      try {
        vip = await api.vipPrediction(fixtureId, state.userId);
      } catch (error) {
        vip = null;
      }
    }
    const data = vip?.data || basic.data;
    root.innerHTML = `
      ${sectionTitle(`${data.home_team} vs ${data.away_team}`)}
      <section class="card">
        ${confidenceBadge(data.confidence || 0)}
        ${metaLine(data, basic.meta)}
        ${probabilityGrid(data.probabilities || {})}
      </section>
      <section class="card">
        <h2>Most likely scores</h2>
        ${scorelineList(data.top_scorelines || [], 3)}
      </section>
      ${vip ? vipSection(data) : vipGate("Unlock complete forecast")}
    `;
    root.querySelectorAll("[data-route]").forEach((button) => {
      button.addEventListener("click", () => {
        window.location.hash = button.dataset.route;
      });
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
```

## Task 6: Subscription And Checkout Page

**Files:**

- Create: `frontend/h5/views/subscribe.js`
- Create: `frontend/h5/views/account.js`

- [ ] **Step 1: Implement subscribe view**

`frontend/h5/views/subscribe.js`:

```js
import { api } from "../api.js";
import { state } from "../state.js";
import { errorState, sectionTitle } from "../components/layout.js";
import { showToast } from "../components/toast.js";

const plans = [
  { id: "vip_day", name: "Day pass", price: "9.90", desc: "Access for one match day" },
  { id: "vip_week", name: "Week pass", price: "29.90", desc: "Access throughout a busy fixture week" },
  { id: "vip_month", name: "Month pass", price: "99.00", desc: "Follow the full match cycle" },
];

function planCard(plan) {
  return `
    <article class="card">
      <h2>${plan.name}</h2>
      <div class="price">¥${plan.price}</div>
      <p>${plan.desc}</p>
      <button class="primary-button" data-plan="${plan.id}">Activate</button>
    </article>
  `;
}

export function renderSubscribe(root) {
  root.innerHTML = `
    ${sectionTitle("Activate VIP")}
    <section class="card">
      <label>User ID</label>
      <input id="userIdInput" class="text-input" value="${state.userId || ""}" placeholder="Enter your user ID">
    </section>
    ${plans.map(planCard).join("")}
  `;
  root.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", async () => {
      const userId = document.getElementById("userIdInput").value.trim();
      if (!userId) {
        showToast("Enter your user ID");
        return;
      }
      localStorage.setItem("wc_predict_user_id", userId);
      state.userId = userId;
      try {
        const { data } = await api.checkout(userId, button.dataset.plan);
        showToast(`Order created:${data.order_id}`);
        window.location.hash = "#/account";
      } catch (error) {
        root.insertAdjacentHTML("afterbegin", errorState(error));
      }
    });
  });
}
```

Add CSS:

```css
.text-input {
  width: 100%;
  margin-top: 8px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  font: inherit;
}

.price {
  font-size: 28px;
  font-weight: 900;
  color: var(--accent);
}
```

- [ ] **Step 2: Implement account view**

`frontend/h5/views/account.js`:

```js
import { api } from "../api.js";
import { state, setUserId } from "../state.js";
import { errorState, loading, sectionTitle } from "../components/layout.js";
import { showToast } from "../components/toast.js";

export async function renderAccount(root) {
  root.innerHTML = `
    ${sectionTitle("Account")}
    <section class="card">
      <label>User ID</label>
      <input id="accountUserId" class="text-input" value="${state.userId || ""}" placeholder="Enter User ID">
      <button id="saveUserId" class="primary-button">Save</button>
    </section>
    <section id="subscriptionCard" class="card">Load Subscriptions...</section>
  `;
  document.getElementById("saveUserId").addEventListener("click", () => {
    setUserId(document.getElementById("accountUserId").value);
    showToast("Saved");
    renderAccount(root);
  });
  const card = document.getElementById("subscriptionCard");
  if (!state.userId) {
    card.innerHTML = "No User Setup ID";
    return;
  }
  try {
    const { data } = await api.subscription(state.userId);
    state.subscription = data;
    card.innerHTML = `
      <h2>${data.tier.toUpperCase()}</h2>
      <p>Status: ${data.status}</p>
      <p>_Other Organiser${data.expires_at || "-"}</p>
    `;
  } catch (error) {
    card.innerHTML = errorState(error);
  }
}
```

## Task 7: Optional Admin Refresh Page

**Files:**

- Create: `frontend/h5/views/adminRefresh.js`
- Modify: `frontend/h5/app.js`

- [ ] **Step 1: Implement admin view**

`frontend/h5/views/adminRefresh.js`:

```js
import { state } from "../state.js";
import { errorState, loading, sectionTitle } from "../components/layout.js";

async function adminRequest(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": state.userId || "",
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error.message);
  return payload.data;
}

export async function renderAdminRefresh(root) {
  root.innerHTML = loading("Loading refresh status...");
  try {
    const data = await adminRequest("/api/admin/jobs/refresh/latest");
    root.innerHTML = `
      ${sectionTitle("Refresh jobs")}
      <section class="card">
        <h2>${data.run ? data.run.status : "No records"}</h2>
        <pre>${JSON.stringify(data, null, 2)}</pre>
        <button id="runRefresh" class="primary-button">Refresh now</button>
      </section>
    `;
    document.getElementById("runRefresh").addEventListener("click", async () => {
      await adminRequest("/api/admin/jobs/refresh/run", {
        method: "POST",
        body: { window_hours: 48, force: false },
      });
      renderAdminRefresh(root);
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
```

Modify `app.js`:

```js
import { renderAdminRefresh } from "./views/adminRefresh.js";
```

Add route branch:

```js
if (hash === "#/admin-refresh") {
  renderAdminRefresh(root);
  return;
}
```

## Task 8: Serve H5 From Flask

**Files:**

- Modify: `src/api/app.py`
- Create: `tests/test_h5_static.py`

- [ ] **Step 1: Add tests**

`tests/test_h5_static.py`:

```python
import unittest

from src.api.app import app


class H5StaticTests(unittest.TestCase):
    def test_h5_index_served(self):
        with app.test_client() as c:
            r = c.get("/h5")
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/html", r.content_type)

    def test_h5_asset_served(self):
        with app.test_client() as c:
            r = c.get("/h5/styles.css")
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/css", r.content_type)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Modify Flask static routes**

In `src/api/app.py`, import:

```python
from flask import Flask, jsonify, request, send_from_directory
```

Add near endpoint section:

```python
_H5_DIR = Path(__file__).parent.parent.parent / "frontend" / "h5"


@app.route("/h5")
def h5_index():
    return send_from_directory(_H5_DIR, "index.html")


@app.route("/h5/<path:filename>")
def h5_asset(filename):
    return send_from_directory(_H5_DIR, filename)
```

- [ ] **Step 3: Run tests**

```powershell
python -m unittest tests.test_h5_static
```

Expected: OK.

## Task 9: Frontend Smoke Tests

**Files:**

- Create: `tests/test_h5_files.py`

- [ ] **Step 1: Add static file smoke tests**

`tests/test_h5_files.py`:

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent / "frontend" / "h5"


class H5FilesTests(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            "index.html",
            "styles.css",
            "app.js",
            "api.js",
            "state.js",
            "views/matches.js",
            "views/prediction.js",
            "views/subscribe.js",
            "views/account.js",
        ]
        for rel in required:
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_app_uses_api_envelope_client(self):
        api_js = (ROOT / "api.js").read_text(encoding="utf-8")
        self.assertIn("if (!payload.ok)", api_js)
        self.assertIn("payload.data", api_js)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run smoke tests**

```powershell
python -m unittest tests.test_h5_files tests.test_h5_static
```

Expected: OK.

## Task 10: Documentation And Launch Checklist

**Files:**

- Create: `docs/h5_frontend_contract.md`

- [ ] **Step 1: Write frontend contract**

`docs/h5_frontend_contract.md`:

```markdown
# H5 Frontend Contract

## Routes

| route | purpose |
|---|---|
| `/h5#/matches` | Match list |
| `/h5#/match/<fixture_id>` | Prediction detail |
| `/h5#/subscribe` | VIP checkout |
| `/h5#/account` | Subscription status |
| `/h5#/admin-refresh` | Admin refresh status |

## User State

`wc_predict_user_id` stores the current user id in local storage.

## API Envelope

All API calls expect:

```json
{"ok": true, "data": {}, "meta": {}}
```

Errors are displayed from:

```json
{"ok": false, "error": {"code": "...", "message": "..."}}
```

## Basic vs VIP

Basic users see HDA probabilities, top 3 scorelines, confidence, model version, and data update time.

VIP users additionally see top 15 scorelines, over/under, BTTS, market edge, lineup adjustment, and score heatmap when available.

## Launch Checklist

- `/h5` returns the H5 shell.
- `/api/matches/today` returns envelope data.
- `/api/predictions/<id>` renders for anonymous/basic users.
- `/api/predictions/<id>/vip` unlocks for VIP users.
- `/api/billing/checkout` creates pending orders.
- `/api/me/subscription` updates account state.
```

## Task 11: Verification

**Files:**

- All Step 10E files

- [ ] **Step 1: Run H5 tests**

```powershell
python -m unittest tests.test_h5_files tests.test_h5_static
```

Expected: OK.

- [ ] **Step 2: Run API regression tests**

```powershell
python -m unittest tests.test_api_contract tests.test_api_app tests.test_api_billing tests.test_api_refresh_jobs
```

Expected: OK.

- [ ] **Step 3: Run full suite**

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 4: Manual H5 smoke**

Start backend:

```powershell
python src/api/app.py
```

Open:

```text
http://localhost:5000/h5
```

Manual checks:

- Match list loads or shows empty state.
- Invalid API state shows readable error.
- User ID can be saved in account page.
- Subscribe page creates an order.
- Prediction page shows basic fields.
- VIP locked section appears for non-VIP user.

## Implementation Order

1. Create H5 shell.
2. Add API client and state.
3. Add shared UI components.
4. Add match list page.
5. Add prediction detail page.
6. Add subscribe/account pages.
7. Add optional admin refresh page.
8. Serve H5 through Flask.
9. Add static smoke tests.
10. Write docs and run full verification.

## Acceptance Criteria

- `/h5` opens a usable mobile-first prediction experience.
- H5 consumes Step 10C API envelope consistently.
- Basic users see only Basic fields.
- VIP route unlocks richer sections when subscription is active.
- Checkout flow creates pending orders.
- Account page displays subscription status.
- Static serving tests pass.
- Full backend regression suite still passes.

## Known Follow-Ups For Step 10F

- Add production monitoring dashboards.
- Add frontend error telemetry.
- Add payment provider integration.
- Add real login/session handling.
- Add CDN/static asset deployment path.
