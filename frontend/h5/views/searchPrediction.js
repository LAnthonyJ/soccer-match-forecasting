import { api } from "../api.js";
import { state, setSelectedFixture } from "../state.js";
import { formatBeijingTime } from "../components/time.js";
import { emptyState, errorState, loading, sectionTitle } from "../components/layout.js";

function matchCard(match) {
  const direction = match.match_direction === "reversed" ? "Team order corrected" : "World Cup fixture found";
  return `
    <article class="card match-card" data-fixture-id="${match.sportmonks_id || match.fixture_id}">
      <div class="match-row">
        <div class="team">${match.home_team}</div>
        <div class="kickoff">vs</div>
        <div class="team away">${match.away_team}</div>
      </div>
      <div class="kickoff">${match.tournament || "World Cup"} · ${formatBeijingTime(match.kickoff_utc)}</div>
      <div class="kickoff">${direction} · View forecast details</div>
    </article>
  `;
}

function formTemplate() {
  return `
    <section class="card search-card">
      <label>Team 1</label>
      <input id="homeTeam" class="text-input" placeholder="Enter a national team" autocomplete="off">
      <div id="homeTeamSuggestions" class="teamSuggestions"></div>
      <label>Team 2</label>
      <input id="awayTeam" class="text-input" placeholder="Enter a national team" autocomplete="off">
      <div id="awayTeamSuggestions" class="teamSuggestions"></div>
      <button id="searchFixtures" class="primary-button full-width">Find match forecast</button>
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
    statusRoot.innerHTML = statusPanel("neutral", "Ready to search", "Enter two national teams to search the 2026 World Cup schedule first.");
    return;
  }
  if (current.kind === "loading") {
    statusRoot.innerHTML = statusPanel("loading", "Querying live Sportmonks data", "Checking the cached schedule for an exact match.");
    return;
  }
  if (current.kind === "success") {
    const source = current.source === "sportmonks_world_cup" ? "Source: Sportmonks World Cup feed" : "Source: Sportmonks live feed";
    const cache = current.cache === "hit" ? "Using the cached World Cup schedule." : "World Cup schedule refreshed.";
    statusRoot.innerHTML = statusPanel("success", source, `Found ${current.count} matching fixtures.${cache}`);
    return;
  }
  if (current.kind === "empty") {
    statusRoot.innerHTML = statusPanel("neutral", "Search complete", "No fixture between these teams was found in the 2026 World Cup schedule.");
    return;
  }
  if (current.kind === "warning") {
    statusRoot.innerHTML = statusPanel("warning", "Partial results", "Sportmonks returned partial results. Try again later.");
    return;
  }
  statusRoot.innerHTML = statusPanel("warning", "Search failed", current.message || "Could not complete the fixture search.");
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
  root.innerHTML = sectionTitle("Pre-match forecasts") + formTemplate();
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
      renderSearchStatus(statusRoot, { kind: "error", message: "Enter both national teams first." });
      results.innerHTML = errorState({ message: "Enter both national teams first." });
      return;
    }

    renderSearchStatus(statusRoot, { kind: "loading" });
    results.innerHTML = loading("Searching for match forecasts...");
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
        results.innerHTML = emptyState("No fixture found. Select the teams from the suggestions and try again.");
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
