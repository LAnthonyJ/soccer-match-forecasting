import { api } from "../api.js";
import { state, setSelectedFixture } from "../state.js";
import { formatBeijingTime } from "../components/time.js";
import { emptyState, errorState, loading, sectionTitle } from "../components/layout.js";

function matchCard(match) {
  const direction = match.match_direction === "reversed" ? "队伍顺序已自动校正" : "世界杯赛程命中";
  return `
    <article class="card match-card" data-fixture-id="${match.sportmonks_id || match.fixture_id}">
      <div class="match-row">
        <div class="team">${match.home_team}</div>
        <div class="kickoff">vs</div>
        <div class="team away">${match.away_team}</div>
      </div>
      <div class="kickoff">${match.tournament || "世界杯"} · ${formatBeijingTime(match.kickoff_utc)}</div>
      <div class="kickoff">${direction} · 点击进入预测详情</div>
    </article>
  `;
}

function formTemplate() {
  return `
    <section class="card search-card">
      <label>球队 1</label>
      <input id="homeTeam" class="text-input" placeholder="请输入国家队" autocomplete="off">
      <div id="homeTeamSuggestions" class="teamSuggestions"></div>
      <label>球队 2</label>
      <input id="awayTeam" class="text-input" placeholder="请输入国家队" autocomplete="off">
      <div id="awayTeamSuggestions" class="teamSuggestions"></div>
      <button id="searchFixtures" class="primary-button full-width">查询比赛预测</button>
    </section>
    <section id="searchStatus"></section>
    <section id="searchResults"></section>
  `;
}

function statusPanel(kind, title, body) {
  return `
    <section class="card status-panel status-${kind}">
      <div class="status-title">${title}</div>
      <div class="status-body">${body}</div>
    </section>
  `;
}

function renderSearchStatus(statusRoot, current) {
  if (current.kind === "idle") {
    statusRoot.innerHTML = statusPanel("neutral", "准备查询", "输入两支国家队后，系统会优先在 2026 世界杯赛程内做实时匹配。");
    return;
  }
  if (current.kind === "loading") {
    statusRoot.innerHTML = statusPanel("loading", "正在查询 Sportmonks 实时数据", "先读取世界杯赛程缓存，再做两队精确匹配。");
    return;
  }
  if (current.kind === "success") {
    const source = current.source === "sportmonks_world_cup" ? "已使用 Sportmonks 世界杯实时数据" : "已使用 Sportmonks 实时数据";
    const cache = current.cache === "hit" ? "当前命中世界杯赛程缓存。" : "当前已刷新世界杯赛程。";
    statusRoot.innerHTML = statusPanel("success", source, `找到 ${current.count} 场匹配比赛。${cache}`);
    return;
  }
  if (current.kind === "empty") {
    statusRoot.innerHTML = statusPanel("neutral", "查询完成", "没有找到这两支国家队在 2026 世界杯赛程中的对阵。");
    return;
  }
  if (current.kind === "warning") {
    statusRoot.innerHTML = statusPanel("warning", "查询结果不完整", "Sportmonks 返回了降级结果或部分结果，请稍后重试。");
    return;
  }
  statusRoot.innerHTML = statusPanel("warning", "查询失败", current.message || "比赛查询未能完成。");
}

function suggestionList(teams) {
  return teams.map((team) => `
    <button
      type="button"
      class="suggestion-item"
      data-display-name="${team.display_name}"
      data-sportmonks-name="${team.sportmonks_name}">
      <span>${team.display_name}</span>
      <small>${team.sportmonks_name}</small>
    </button>
  `).join("");
}

function bindAutocomplete(root, inputId, panelId) {
  const input = root.querySelector(`#${inputId}`);
  const panel = root.querySelector(`#${panelId}`);
  let requestId = 0;

  input.addEventListener("input", async () => {
    const query = input.value.trim();
    delete input.dataset.sportmonksName;
    if (!query) {
      panel.innerHTML = "";
      return;
    }
    const currentRequest = ++requestId;
    try {
      const { data } = await api.searchTeams(query);
      if (currentRequest !== requestId) return;
      panel.innerHTML = suggestionList(data.teams || []);
      panel.querySelectorAll("[data-sportmonks-name]").forEach((button) => {
        button.addEventListener("click", () => {
          input.value = button.dataset.displayName;
          input.dataset.sportmonksName = button.dataset.sportmonksName;
          panel.innerHTML = "";
        });
      });
    } catch (_) {
      panel.innerHTML = "";
    }
  });
}

export function renderSearchPrediction(root) {
  root.innerHTML = sectionTitle("赛前预测") + formTemplate();
  const statusRoot = root.querySelector("#searchStatus");
  const results = root.querySelector("#searchResults");
  renderSearchStatus(statusRoot, { kind: "idle" });
  bindAutocomplete(root, "homeTeam", "homeTeamSuggestions");
  bindAutocomplete(root, "awayTeam", "awayTeamSuggestions");

  root.querySelector("#searchFixtures").addEventListener("click", async () => {
    const homeInput = root.querySelector("#homeTeam");
    const awayInput = root.querySelector("#awayTeam");
    const home = homeInput.dataset.sportmonksName || homeInput.value.trim();
    const away = awayInput.dataset.sportmonksName || awayInput.value.trim();
    if (!home || !away) {
      renderSearchStatus(statusRoot, { kind: "error", message: "请先输入两支国家队。" });
      results.innerHTML = errorState({ message: "请先输入两支国家队。" });
      return;
    }

    renderSearchStatus(statusRoot, { kind: "loading" });
    results.innerHTML = loading("正在查询比赛预测...");
    try {
      const { data, meta } = await api.searchFixtures({
        home,
        away,
        userId: state.userId,
        token: state.sessionToken,
      });
      const hasWarning = Boolean(meta?.warning);
      if (data.matches.length) {
        renderSearchStatus(statusRoot, {
          kind: hasWarning ? "warning" : "success",
          count: data.matches.length,
          source: meta?.source,
          cache: meta?.cache,
        });
      } else {
        renderSearchStatus(statusRoot, { kind: hasWarning ? "warning" : "empty" });
      }
      if (!data.matches.length) {
        results.innerHTML = emptyState("未找到比赛。请从联想列表中选择标准国家队名称后重试。");
        return;
      }
      results.innerHTML = data.matches.map(matchCard).join("");
      results.querySelectorAll("[data-fixture-id]").forEach((node) => {
        node.addEventListener("click", () => {
          const fixtureId = node.dataset.fixtureId;
          setSelectedFixture(fixtureId);
          window.location.hash = `#/match/${fixtureId}`;
        });
      });
    } catch (error) {
      renderSearchStatus(statusRoot, { kind: "error", message: error.message });
      results.innerHTML = errorState(error);
    }
  });
}
