function pct(value) {
  if (typeof value !== "number") return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function probabilityGrid(probabilities) {
  return `
    <div class="prob-grid">
      <div class="prob-box"><div class="prob-label">Home win</div><div class="prob-value">${pct(probabilities.home)}</div></div>
      <div class="prob-box"><div class="prob-label">Draw</div><div class="prob-value">${pct(probabilities.draw)}</div></div>
      <div class="prob-box"><div class="prob-label">Away win</div><div class="prob-value">${pct(probabilities.away)}</div></div>
    </div>
  `;
}

export function confidenceBadge(confidence) {
  return `<span class="badge">Confidence ${(confidence * 100).toFixed(0)}%</span>`;
}
