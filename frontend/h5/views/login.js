import { api } from "../api.js";
import { state, setSessionToken, setSessionUnlocked, setUserId } from "../state.js";
import { sectionTitle } from "../components/layout.js";
import { showToast } from "../components/toast.js";

export function renderLogin(root) {
  root.innerHTML = `
    ${sectionTitle("登录")}
    <section class="card">
      <p class="login-copy">输入用户名和卡密后进入世界杯预测系统。</p>
      <label>用户名</label>
      <input id="loginUserId" class="text-input" value="${state.userId || ""}" placeholder="请输入用户名" autocomplete="username">
      <label>卡密</label>
      <input id="loginCardCode" class="text-input" value="" placeholder="请输入卡密" autocomplete="one-time-code">
      <button id="loginSubmit" class="primary-button full-width">登录</button>
    </section>
  `;

  root.querySelector("#loginSubmit").addEventListener("click", async () => {
    const userId = root.querySelector("#loginUserId").value.trim();
    const cardCode = root.querySelector("#loginCardCode").value.trim();
    if (!userId) {
      showToast("请输入用户名");
      return;
    }
    if (!cardCode) {
      showToast("请输入卡密");
      return;
    }
    try {
      const { data } = await api.redeemCard(userId, cardCode);
      setUserId(data.user_id);
      setSessionToken(data.token);
      setSessionUnlocked(true);
      showToast("登录成功");
      window.location.hash = "#/search";
    } catch (error) {
      showToast(`登录失败: ${error.message}`);
    }
  });
}
