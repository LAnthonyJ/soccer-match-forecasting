import { api } from "../api.js";
import { state, setSelectedFixture } from "../state.js";
import { formatBeijingTime } from "../components/time.js";
import { emptyState, errorState, loading, sectionTitle } from "../components/layout.js";

function matchCard(match) {
  return `
    <article class="card match-card" data-fixture-id="${match.sportmonks_id || match.fixture_id}">
      <div class="match-row">
        <div class="team">${match.home_team}</div>
        <div class="kickoff">vs</div>
        <div class="team away">${match.away_team}</div>
      </div>
      <div class="kickoff">${match.tournament || "国际赛事"} · ${formatBeijingTime(match.kickoff_utc)}</div>
    </article>
  `;
}

export async function renderMatches(root) {
  root.innerHTML = loading("正在加载比赛...");
  try {
    const { data, meta } = await api.matches(state.userId, state.sessionToken);
    const warning = meta?.warning ? `<div class="card locked">数据源暂时不可用，当前显示空列表。</div>` : "";
    if (!data.matches.length) {
      root.innerHTML = sectionTitle("今日比赛") + warning + emptyState("暂时没有可展示的比赛");
      return;
    }
    root.innerHTML = sectionTitle("今日比赛") + warning + data.matches.map(matchCard).join("");
    root.querySelectorAll("[data-fixture-id]").forEach((node) => {
      node.addEventListener("click", () => {
        const fixtureId = node.dataset.fixtureId;
        setSelectedFixture(fixtureId);
        window.location.hash = `#/match/${fixtureId}`;
      });
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
