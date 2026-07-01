export const state = {
  userId: null,
  sessionToken: null,
  sessionUnlocked: false,
  subscription: null,
  selectedFixtureId: null,
};

export function loadState() {
  state.userId = localStorage.getItem("wc_predict_user_id");
  state.sessionToken = localStorage.getItem("wc_predict_session_token");
  state.sessionUnlocked = sessionStorage.getItem("wc_predict_session_unlocked") === "true";
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

export function setSessionToken(token) {
  state.sessionToken = token || null;
  if (state.sessionToken) {
    localStorage.setItem("wc_predict_session_token", state.sessionToken);
  } else {
    localStorage.removeItem("wc_predict_session_token");
  }
}

export function setSessionUnlocked(unlocked) {
  state.sessionUnlocked = Boolean(unlocked);
  if (state.sessionUnlocked) {
    sessionStorage.setItem("wc_predict_session_unlocked", "true");
  } else {
    sessionStorage.removeItem("wc_predict_session_unlocked");
  }
}

export function setSelectedFixture(fixtureId) {
  state.selectedFixtureId = fixtureId;
  localStorage.setItem("wc_predict_last_fixture_id", fixtureId);
}

export function clearAuth() {
  setUserId(null);
  setSessionToken(null);
  setSessionUnlocked(false);
  state.subscription = null;
}

export function isVip() {
  return state.subscription?.tier === "vip" || state.subscription?.tier === "admin";
}
