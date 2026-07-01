function pct(value) {
  if (typeof value !== "number") return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function probabilityGrid(probabilities) {
  return `
    <div class="prob-grid">
      <div class="prob-box"><div class="prob-label">胜</div><div class="prob-value">${pct(probabilities.home)}</div></div>
      <div class="prob-box"><div class="prob-label">平</div><div class="prob-value">${pct(probabilities.draw)}</div></div>
      <div class="prob-box"><div class="prob-label">负</div><div class="prob-value">${pct(probabilities.away)}</div></div>
    </div>
  `;
}

export function confidenceBadge(confidence) {
  return `<span class="badge">置信度 ${(confidence * 100).toFixed(0)}%</span>`;
}
