import { api } from "../api.js";
import { clearAuth, state } from "../state.js";
import { errorState, sectionTitle } from "../components/layout.js";
import { formatBeijingTime } from "../components/time.js";

function tierLabel(tier) {
  if (tier === "vip") return "VIP plan";
  if (tier === "admin") return "Administrator";
  return "Basic";
}

export async function renderAccount(root) {
  root.innerHTML = `
    ${sectionTitle("My account")}
    <section id="accountProfile" class="card">Loading account details...</section>
    <section class="card">
      <button id="logoutButton" class="secondary-button full-width">Sign out</button>
    </section>
  `;

  root.querySelector("#logoutButton").addEventListener("click", () => {
    clearAuth();
    window.location.hash = "#/login";
  });

  const card = root.querySelector("#accountProfile");
  if (!state.userId || !state.sessionToken || !state.sessionUnlocked) {
    card.innerHTML = "<p>Sign in first</p>";
    return;
  }

  try {
    const { data } = await api.subscription(state.userId, state.sessionToken);
    state.subscription = data;
    card.innerHTML = `
      <h2>${state.userId}</h2>
      <p>Tier: ${tierLabel(data.tier || "basic")}</p>
      <p>Status: ${data.status}</p>
      <p>Expires: ${formatBeijingTime(data.expires_at, "Not set")}</p>
    `;
  } catch (error) {
    card.innerHTML = errorState(error);
  }
}
