import { api } from "../api.js";
import { clearAuth, state } from "../state.js";
import { errorState, sectionTitle } from "../components/layout.js";
import { formatBeijingTime } from "../components/time.js";

function tierLabel(tier) {
  if (tier === "vip") return "VIP 版";
  if (tier === "admin") return "管理员";
  return "基础版";
}

export async function renderAccount(root) {
  root.innerHTML = `
    ${sectionTitle("我的账户")}
    <section id="accountProfile" class="card">正在加载账户信息...</section>
    <section class="card">
      <button id="logoutButton" class="secondary-button full-width">退出登录</button>
    </section>
  `;

  root.querySelector("#logoutButton").addEventListener("click", () => {
    clearAuth();
    window.location.hash = "#/login";
  });

  const card = root.querySelector("#accountProfile");
  if (!state.userId || !state.sessionToken || !state.sessionUnlocked) {
    card.innerHTML = "<p>请先登录</p>";
    return;
  }

  try {
    const { data } = await api.subscription(state.userId, state.sessionToken);
    state.subscription = data;
    card.innerHTML = `
      <h2>${state.userId}</h2>
      <p>等级：${tierLabel(data.tier || "basic")}</p>
      <p>状态：${data.status}</p>
      <p>到期时间：${formatBeijingTime(data.expires_at, "未设置")}</p>
    `;
  } catch (error) {
    card.innerHTML = errorState(error);
  }
}
