export function loading(label = "正在加载...") {
  return `<section class="loading-view">${label}</section>`;
}

export function emptyState(label) {
  return `<section class="empty">${label}</section>`;
}

export function errorState(error) {
  return `<section class="error">${error.message || "请求失败"}</section>`;
}

export function sectionTitle(title, action = "") {
  return `<div class="section-title"><h1>${title}</h1>${action}</div>`;
}
