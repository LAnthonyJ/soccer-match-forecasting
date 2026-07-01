export function vipGate(title, body = "使用 VIP 卡密查看比分热力图、市场差异、BTTS、大小球和阵容影响。") {
  return `
    <section class="card locked">
      <h2>${title}</h2>
      <p>${body}</p>
      <button class="primary-button" data-route="#/account">前往登录</button>
    </section>
  `;
}
