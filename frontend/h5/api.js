export class ApiClientError extends Error {
  constructor(payload, status) {
    super(payload?.error?.message || "API request failed");
    this.code = payload?.error?.code || "unknown_error";
    this.details = payload?.error?.details || {};
    this.status = status;
  }
}

function headers(opts = {}) {
  const result = { "Content-Type": "application/json" };
  if (opts.token) result["Authorization"] = `Bearer ${opts.token}`;
  return result;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: headers({ userId: options.userId, token: options.token }),
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!payload.ok) throw new ApiClientError(payload, response.status);
  return { data: payload.data, meta: payload.meta };
}

export const api = {
  matches(userId, token) {
    return request("/api/matches/today?limit=50", { userId, token });
  },
  searchFixtures({ home, away, userId, token }) {
    const params = new URLSearchParams({ home, away, limit: "50" });
    return request(`/api/search/fixtures?${params.toString()}`, { userId, token });
  },
  searchTeams(query) {
    const params = new URLSearchParams({ q: query });
    return request(`/api/teams/search?${params.toString()}`);
  },
  prediction(fixtureId, userId, token) {
    return request(`/api/predictions/${encodeURIComponent(fixtureId)}`, { userId, token });
  },
  vipPrediction(fixtureId, userId, token) {
    return request(`/api/predictions/${encodeURIComponent(fixtureId)}/vip`, { userId, token });
  },
  analysis(fixtureId, userId, token) {
    return request(`/api/predictions/${encodeURIComponent(fixtureId)}/analysis`, {
      method: "POST",
      userId,
      token,
    });
  },
  subscription(userId, token) {
    return request("/api/me/subscription", { userId, token });
  },
  checkout(userId, planId, token) {
    return request("/api/billing/checkout", {
      method: "POST",
      userId,
      token,
      body: { plan_id: planId },
    });
  },
  redeemCard(username, cardCode) {
    return request("/api/auth/redeem", {
      method: "POST",
      body: { username, card_code: cardCode },
    });
  },
  createSession(userId) {
    return request("/api/auth/session", {
      method: "POST",
      body: { user_id: userId },
    });
  },
};
