import { state } from "../state.js";
import { errorState, loading, sectionTitle } from "../components/layout.js";

async function adminRequest(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      Authorization: state.sessionToken ? `Bearer ${state.sessionToken}` : "",
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error.message);
  return payload.data;
}

export async function renderAdminRefresh(root) {
  root.innerHTML = loading("Loading refresh status...");
  try {
    const data = await adminRequest("/api/admin/jobs/refresh/latest");
    root.innerHTML = `
      ${sectionTitle("Refresh jobs")}
      <section class="card">
        <h2>${data.run ? data.run.status : "No records"}</h2>
        <pre>${JSON.stringify(data, null, 2)}</pre>
        <button id="runRefresh" class="primary-button">Refresh now</button>
      </section>
    `;
    document.getElementById("runRefresh").addEventListener("click", async () => {
      await adminRequest("/api/admin/jobs/refresh/run", {
        method: "POST",
        body: { window_hours: 48, force: false },
      });
      renderAdminRefresh(root);
    });
  } catch (error) {
    root.innerHTML = errorState(error);
  }
}
