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
        const { data } = await api.checkout(userId, button.dataset.plan, state.sessionToken);
        if (data.payment_url) {
          window.location.href = data.payment_url;
        } else {
          showToast(`Order created:${data.order_id}`);
          window.location.hash = "#/account";
        }
      } catch (error) {
        root.insertAdjacentHTML("afterbegin", errorState(error));
      }
    });
  });
}
