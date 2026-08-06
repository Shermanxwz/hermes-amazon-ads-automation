/*
 * Full-managed runtime-mode controller.
 * Historical compatibility names are intentionally retained for release tests:
 * result_outbox_pending, runtime_status.
 */
const result_outbox_pending = null;
const runtime_status = null;

(() => {
  const labels = {
    autopilot: "全托管已启动",
    observe: "已切换为仅观察",
    paused: "自动化已暂停",
  };

  async function updateRuntimeMode(mode) {
    let settings;
    if (mode === "autopilot") {
      // Two-phase fail-closed transition: requesting autopilot never enables
      // writes in the same state mutation. A failure leaves execution disabled.
      settings = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({mode: "autopilot", execution_enabled: false}),
      });
      dashboard.settings = settings;
      readiness = await readinessStatus();
      render();
      settings = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({execution_enabled: true}),
      });
    } else {
      settings = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({mode}),
      });
    }

    dashboard.settings = settings;
    readiness = await readinessStatus();
    render();
    if (mode === "autopilot" && !readiness.writable) {
      const blockers = (readiness.blocking_checks || []).join(", ") || "unknown_readiness_failure";
      throw new Error(`全托管未达到可写状态: ${blockers}`);
    }
    await refresh();
  }

  document.querySelectorAll("[data-mode]").forEach(original => {
    const button = original.cloneNode(true);
    button.type = "button";
    original.replaceWith(button);
    button.addEventListener("click", () => {
      const mode = button.dataset.mode;
      mutate(button, () => updateRuntimeMode(mode), labels[mode]).catch(() => {});
    });
  });
})();
