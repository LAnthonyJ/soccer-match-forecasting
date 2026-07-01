function pct(value) {
  return `${(value * 100).toFixed(2)}%`;
}

export function scorelineList(items = [], max = 3) {
  const rows = items.slice(0, max).map((item) => {
    const home = item[0];
    const away = item[1];
    const prob = item[2];
    return `<li><strong>${home}-${away}</strong><span>${pct(prob)}</span></li>`;
  }).join("");
  return `<ol class="scorelines">${rows}</ol>`;
}
