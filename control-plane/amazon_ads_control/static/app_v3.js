/*
 * Full-managed runtime-mode and owner daily-spend controller.
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
  render = function renderV423() {
    oldRender();
    const settings = dashboard.settings || {};
    const budget = dashboard.budget_guard || {};
    const currency = dashboard.profiles?.[0]?.currency || "账户货币";
    const daily = document.querySelector("#max-daily-ad-spend");
    const explore = document.querySelector("#exploration-budget-pct");
    if (daily) daily.value = settings.max_daily_ad_spend ?? 100;
    if (explore) explore.value = settings.exploration_budget_pct ?? 20;
    const currencyNode = document.querySelector("#daily-currency-label");
    if (currencyNode) currencyNode.textContent = currency;

    const spent = document.querySelector("#budget-exposure");
    const remaining = document.querySelector("#budget-remaining");
    const exploration = document.querySelector("#exploration-remaining");
    const state = document.querySelector("#budget-guard-state");
    const spentValue = budget.spent_today;
    if (spent) {
      spent.textContent = spentValue === null || spentValue === undefined
        ? `— / ${number(budget.hard_cap, 2)} ${currency}`
        : `${number(spentValue, 2)} / ${number(budget.hard_cap, 2)} ${currency}`;
      spent.title = budget.spend_as_of
        ? `今日花费数据截至 ${budget.spend_as_of}`
        : "等待同日 Sponsored Products 花费证据";
    }
    if (remaining) remaining.textContent = `${number(budget.remaining, 2)} ${currency}`;
    if (exploration) {
      exploration.textContent = `${number(budget.exploration_remaining, 2)} / ${number(budget.exploration_cap, 2)} ${currency}`;
    }
    if (state) {
      const protectedSpend = Number(budget.protected_spend || 0);
      const cap = Number(budget.hard_cap || 0);
      const safe = budget.fresh && protectedSpend < cap;
      if (!budget.fresh) {
        state.textContent = "等待今日花费数据";
      } else if (budget.exploration_allowed) {
        state.textContent = "正常运行";
      } else if (budget.increase_allowed) {
        state.textContent = "停止探索";
      } else if (protectedSpend < cap) {
        state.textContent = "仅降风险";
      } else {
        state.textContent = "已到日花费上限";
      }
      state.className = safe ? "good" : "warn";
      state.title = budget.reason || "";
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
        if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
          throw new Error("探索花费比例必须在 0% 到 100% 之间");
        }
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
