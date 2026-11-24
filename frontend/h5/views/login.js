import { api } from "../api.js";
import { state, setSessionToken, setSessionUnlocked, setUserId } from "../state.js";
import { sectionTitle } from "../components/layout.js";
import { showToast } from "../components/toast.js";

export function renderLogin(root) {
  root.innerHTML = `
    ${sectionTitle("Sign in")}
    <section class="card">
      <p class="login-copy">Enter your username and access code to view World Cup forecasts.</p>
      <label>Username</label>
      <input id="loginUserId" class="text-input" value="${state.userId || ""}" placeholder="Enter your username" autocomplete="username">
      <label>Access code</label>
      <input id="loginCardCode" class="text-input" value="" placeholder="Enter your access code" autocomplete="one-time-code">
      <button id="loginSubmit" class="primary-button full-width">Sign in</button>
    </section>
  `;

  root.querySelector("#loginSubmit").addEventListener("click", async () => {
    const userId = root.querySelector("#loginUserId").value.trim();
    const cardCode = root.querySelector("#loginCardCode").value.trim();
    if (!userId) {
      showToast("Enter your username");
      return;
    }
    if (!cardCode) {
      showToast("Enter your access code");
      return;
    }
    try {
      const { data } = await api.redeemCard(userId, cardCode);
      setUserId(data.user_id);
      setSessionToken(data.token);
      setSessionUnlocked(true);
      showToast("Signed in");
      window.location.hash = "#/search";
    } catch (error) {
      showToast(`Sign-in failed: ${error.message}`);
    }
  });
}
