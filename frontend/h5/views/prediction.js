import { api } from "../api.js";
import { state } from "../state.js";
import { formatBeijingTime } from "../components/time.js";
import { errorState, loading, sectionTitle } from "../components/layout.js";
import { probabilityGrid, confidenceBadge } from "../components/probability.js";
import { scorelineList } from "../components/scorelines.js";
import { vipGate } from "../components/vipGate.js";

function metaLine(data, meta) {
  return `
    <div class="kickoff">
      ${data.model_version || ""} · ${formatBeijingTime(data.data_updated_at || data.generated_at, "更新时间待定")} · ${meta?.source || ""}
    </div>
  `;
}

function vipSection(data) {
  return `
    <section class="card">
      <h2>VIP 数据</h2>
      <div class="prob-grid">
        <div class="prob-box"><div class="prob-label">大 2.5 球</div><div class="prob-value">${((data.over25 || 0) * 100).toFixed(1)}%</div></div>
        <div class="prob-box"><div class="prob-label">双方进球</div><div class="prob-value">${((data.btts || 0) * 100).toFixed(1)}%</div></div>
        <div class="prob-box"><div class="prob-label">市场偏差</div><div class="prob-value">${data.market_edge ? "有" : "-"}</div></div>
      </div>
      <h3>Top 15 比分</h3>
      ${scorelineList(data.top_scorelines || [], 15)}
    </section>
  `;
}

function aiSection(analysisText) {
  return `
    <section class="card">
      <h2>AI 分析</h2>
      <p>${analysisText}</p>
    </section>
  `;
}

export async function renderPrediction(root, fixtureId) {
  root.innerHTML = loading("正在加载预测...");
  try {
    const basic = await api.prediction(fixtureId, state.userId, state.sessionToken);
    let vip = null;
    let analysis = null;
    try {
      vip = await api.vipPrediction(fixtureId, state.userId, state.sessionToken);
      const analysisResult = await api.analysis(fixtureId, state.userId, state.sessionToken);
      analysis = analysisResult.data?.analysis || null;
    } catch (_) {
      vip = null;
      analysis = null;
    }

    const data = vip?.data || basic.data;
    root.innerHTML = `
      ${sectionTitle(`${data.home_team} vs ${data.away_team}`)}
      <section class="card">
        ${confidenceBadge(data.confidence || 0)}
        ${metaLine(data, basic.meta)}
        ${probabilityGrid(data.probabilities || {})}
      </section>
      <section class="card">
        <h2>最可能比分</h2>
        ${scorelineList(data.top_scorelines || [], 3)}
      </section>
      ${vip ? vipSection(data) : vipGate("使用 VIP 卡密解锁完整预测")}
      ${vip ? aiSection(analysis || "AI 分析暂时不可用。") : vipGate("AI 分析", "登录 VIP 卡密后可查看模型结合赛前信息生成的中文解读。")}
    `;
    root.querySelectorAll("[data-route]").forEach((button) => {
      button.addEventListener("click", () => {
        window.location.hash = button.dataset.route;
      });
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
