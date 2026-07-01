import { renderLogin } from "./views/login.js";
import { renderSearchPrediction } from "./views/searchPrediction.js";
import { renderMatches } from "./views/matches.js";
import { renderPrediction } from "./views/prediction.js";
import { renderAccount } from "./views/account.js";
import { renderAdminRefresh } from "./views/adminRefresh.js";
import { state, loadState } from "./state.js";

const root = document.getElementById("app");

function isLoggedIn() {
  return Boolean(state.userId && state.sessionToken && state.sessionUnlocked);
}

function requiresLogin(hash) {
  return hash !== "#/login";
}

function route() {
  const hash = window.location.hash || "#/login";
  if (requiresLogin(hash) && !isLoggedIn()) {
    window.location.hash = "#/login";
    renderLogin(root);
    return;
  }
  if (hash === "#/login") {
    renderLogin(root);
    return;
  }
  if (hash === "#/account") {
    renderAccount(root);
    return;
  }
  if (hash === "#/search") {
    renderSearchPrediction(root);
    return;
  }
  if (hash.startsWith("#/match/")) {
    const fixtureId = hash.replace("#/match/", "");
    renderPrediction(root, fixtureId);
    return;
  }
  if (hash === "#/admin-refresh") {
    renderAdminRefresh(root);
    return;
  }
  renderMatches(root);
}

loadState();
document.getElementById("accountButton").addEventListener("click", () => {
  window.location.hash = isLoggedIn() ? "#/account" : "#/login";
});
document.querySelectorAll("[data-route]").forEach((button) => {
  button.addEventListener("click", () => {
    const routeTarget = button.dataset.route;
    if (routeTarget === "#/account" && !isLoggedIn()) {
      window.location.hash = "#/login";
      return;
    }
    window.location.hash = routeTarget;
  });
});
window.addEventListener("hashchange", route);
route();
