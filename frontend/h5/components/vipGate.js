export function vipGate(title, body = "Use a VIP access code to view score heatmaps, market differences, BTTS, goal totals and lineup effects.") {
  return `
    <section class="card locked">
      <h2>${title}</h2>
      <p>${body}</p>
      <button class="primary-button" data-route="#/account">Go to sign in</button>
    </section>
  `;
}
