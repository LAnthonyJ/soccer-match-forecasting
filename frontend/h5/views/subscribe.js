import { api } from "../api.js";
import { state } from "../state.js";
import { errorState, sectionTitle } from "../components/layout.js";
import { showToast } from "../components/toast.js";

const plans = [
  { id: "vip_day", name: "日卡", price: "9.90", desc: "适合单日比赛" },
  { id: "vip_week", name: "周卡", price: "29.90", desc: "覆盖密集赛程" },
  { id: "vip_month", name: "月卡", price: "99.00", desc: "完整周期跟踪" },
];

function planCard(plan) {
  return `
    <article class="card">
      <h2>${plan.name}</h2>
      <div class="price">¥${plan.price}</div>
      <p>${plan.desc}</p>
      <button class="primary-button" data-plan="${plan.id}">开通</button>
    </article>
  `;
}

export function renderSubscribe(root) {
  root.innerHTML = `
    ${sectionTitle("开通 VIP")}
    <section class="card">
      <label>用户 ID</label>
      <input id="userIdInput" class="text-input" value="${state.userId || ""}" placeholder="输入你的用户 ID">
    </section>
    ${plans.map(planCard).join("")}
  `;
  root.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", async () => {
      const userId = document.getElementById("userIdInput").value.trim();
      if (!userId) {
        showToast("请先输入用户 ID");
        return;
      }
      localStorage.setItem("wc_predict_user_id", userId);
      state.userId = userId;
      try {
        const { data } = await api.checkout(userId, button.dataset.plan, state.sessionToken);
        if (data.payment_url) {
          window.location.href = data.payment_url;
        } else {
          showToast(`订单已创建：${data.order_id}`);
          window.location.hash = "#/account";
        }
      } catch (error) {
        root.insertAdjacentHTML("afterbegin", errorState(error));
      }
    });
  });
}
