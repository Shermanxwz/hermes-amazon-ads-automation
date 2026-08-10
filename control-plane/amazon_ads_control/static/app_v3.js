/*
 * Full-managed runtime-mode and budget controller.
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

  const oldRender = render;
  render = function renderV421() {
    oldRender();
    const settings = dashboard.settings || {};
    const budget = dashboard.budget_guard || {};
    const currency = dashboard.profiles?.[0]?.currency || "账户货币";
    const multiplier = Number(budget.amazon_overdelivery_multiplier || 2);
    const daily = document.querySelector("#max-daily-ad-spend");
    const explore = document.querySelector("#exploration-budget-pct");
    if (daily) daily.value = settings.max_daily_ad_spend ?? 100;
    if (explore) explore.value = settings.exploration_budget_pct ?? 20;
    const currencyNode = document.querySelector("#daily-currency-label");
    if (currencyNode) currencyNode.textContent = currency;

    const exposure = document.querySelector("#budget-exposure");
    const remaining = document.querySelector("#budget-remaining");
    const exploration = document.querySelector("#exploration-remaining");
    const state = document.querySelector("#budget-guard-state");
    if (exposure) {
      exposure.textContent = `${number(budget.projected_exposure, 2)} / ${number(budget.hard_cap, 2)} ${currency}`;
      exposure.title = `按 Amazon 高流量日最坏 ${number(multiplier, 2)}× 日预算花费系数计算`;
    }
    if (remaining) remaining.textContent = `${number(budget.remaining, 2)} ${currency}`;
    if (exploration) exploration.textContent = `${number(budget.exploration_remaining, 2)} / ${number(budget.exploration_cap, 2)} ${currency}`;
    if (state) {
      const safe = budget.fresh && Number(budget.projected_exposure || 0) < Number(budget.hard_cap || 0);
      state.textContent = budget.fresh ? (budget.exploration_allowed ? "可探索" : budget.increase_allowed ? "保守运行" : "仅降风险") : "等待实时预算读取";
      state.className = safe ? "good" : "warn";
      state.title = `${budget.reason || ""} · Amazon 最坏花费系数 ${number(multiplier, 2)}×`;
    }
  };

  const budgetForm = document.querySelector("#budget-form");
  if (budgetForm) {
    budgetForm.addEventListener("submit", event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button");
      mutate(button, async () => {
        const cap = Number(document.querySelector("#max-daily-ad-spend").value);
        const pct = Number(document.querySelector("#exploration-budget-pct").value);
        if (!Number.isFinite(cap) || cap < 1) throw new Error("每日最大花费必须大于 0");
        if (!Number.isFinite(pct) || pct < 0 || pct > 100) throw new Error("探索花费比例必须在 0% 到 100% 之间");
        await api("/api/settings", {
          method: "PUT",
          body: JSON.stringify({max_daily_ad_spend: cap, exploration_budget_pct: pct}),
        });
        document.querySelector("#budget-message").textContent = "已保存";
        await refresh();
      }, "每日最大花费硬上限已更新").catch(() => {});
    });
  }
})();
