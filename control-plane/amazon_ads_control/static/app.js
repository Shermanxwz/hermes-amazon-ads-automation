const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let csrf = "";
let dashboard = null;
let readiness = null;
let noticeTimer = 0;

const modeText = {
  autopilot: ["全托管运行中", "Hermes 正在自动分析、执行和独立回读；只有异常才需要你处理。"],
  observe: ["仅观察", "继续读取和分析广告数据，但不会修改任何广告。"],
  paused: ["已暂停", "Amazon Ads 自动活动已停止，适合排查异常或维护。"],
};

const esc = value => String(value ?? "—").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

const number = (value, digits = 2) => {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString(undefined, {maximumFractionDigits: digits})
    : esc(value);
};

function showNotice(message, kind = "error") {
  const node = $("#notice");
  node.textContent = message;
  node.className = `notice ${kind}`;
  node.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { node.hidden = true; }, 5000);
}

async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {credentials: "same-origin", ...options, headers});
  const data = await response.json().catch(() => ({error: "invalid_response"}));
  if (!response.ok) throw new Error(data.error || data.reason || `HTTP ${response.status}`);
  return data;
}

async function mutate(button, operation, success) {
  const buttons = $$('button');
  buttons.forEach(item => { item.disabled = true; });
  try {
    const result = await operation();
    if (success && result !== false) showNotice(success, "success");
    return result;
  } catch (error) {
    showNotice(error.message || String(error));
    throw error;
  } finally {
    buttons.forEach(item => { item.disabled = false; });
  }
}

function readinessClass(state) {
  return ["writable", "ready"].includes(state) ? "good"
    : ["blocked", "unavailable"].includes(state) ? "bad" : "warn";
}

function statusClass(value) {
  const text = String(value || "").toLowerCase();
  if (["success", "succeeded", "verified", "completed", "autopilot", "ready", "writable", "downloaded", "ingested"].includes(text)) return "good";
  if (["critical", "failed", "failure", "blocked", "paused", "mismatch", "quarantined", "unavailable"].includes(text)) return "bad";
  return "warn";
}

function renderKpis() {
  const settings = dashboard.settings || {};
  const kpis = dashboard.latest_cycle?.kpis || {};
  const currency = dashboard.profiles?.[0]?.currency || "";
  const metrics = [
    ["广告花费", kpis.spend, currency],
    ["广告销售额", kpis.sales, currency],
    ["当前 ACOS", kpis.acos, "%"],
    ["目标 ACOS", settings.target_acos, "%"],
  ];
  $("#kpis").innerHTML = metrics.map(([label, value, suffix]) => `
    <article class="metric-card">
      <span>${esc(label)}</span>
      <strong>${number(value)}${value === null || value === undefined ? "" : esc(suffix)}</strong>
    </article>`).join("");
}

function cyclePoints() {
  return (dashboard.recent_cycles || [])
    .filter(item => item?.kpis && Number.isFinite(Number(item.kpis.acos)))
    .slice(0, 30)
    .reverse();
}

function renderTrend() {
  const rows = cyclePoints();
  const target = Number(dashboard.settings?.target_acos || 0);
  if (!rows.length) {
    $("#trend-chart").innerHTML = '<div class="empty-state">等待首个成熟广告数据周期</div>';
    $("#trend-summary").textContent = "暂无成熟数据";
    return;
  }
  const width = 960;
  const height = 260;
  const pad = {left: 48, right: 22, top: 20, bottom: 38};
  const values = rows.map(item => Number(item.kpis.acos));
  if (target > 0) values.push(target);
  const max = Math.max(10, Math.ceil(Math.max(...values) / 10) * 10);
  const min = Math.max(0, Math.floor(Math.min(...values) / 10) * 10);
  const span = Math.max(10, max - min);
  const x = index => pad.left + (rows.length === 1 ? (width - pad.left - pad.right) / 2 : index * (width - pad.left - pad.right) / (rows.length - 1));
  const y = value => pad.top + (max - value) * (height - pad.top - pad.bottom) / span;
  const path = rows.map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(Number(item.kpis.acos)).toFixed(1)}`).join(" ");
  const grid = [0, 0.25, 0.5, 0.75, 1].map(ratio => {
    const value = max - span * ratio;
    const yy = pad.top + (height - pad.top - pad.bottom) * ratio;
    return `<line x1="${pad.left}" y1="${yy}" x2="${width - pad.right}" y2="${yy}" class="grid-line"/><text x="8" y="${yy + 4}" class="axis-label">${number(value, 0)}%</text>`;
  }).join("");
  const labels = rows.map((item, index) => {
    if (rows.length > 8 && index % Math.ceil(rows.length / 6) !== 0 && index !== rows.length - 1) return "";
    const date = new Date(item.created_at || item.window_end || "");
    const label = Number.isNaN(date.getTime()) ? String(index + 1) : `${date.getMonth() + 1}/${date.getDate()}`;
    return `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" class="axis-label">${esc(label)}</text>`;
  }).join("");
  const points = rows.map((item, index) => `<circle cx="${x(index)}" cy="${y(Number(item.kpis.acos))}" r="4"><title>${esc(item.created_at || "")} · ACOS ${number(item.kpis.acos)}%</title></circle>`).join("");
  const targetLine = target > 0
    ? `<line x1="${pad.left}" y1="${y(target)}" x2="${width - pad.right}" y2="${y(target)}" class="target-line"/><text x="${width - pad.right}" y="${y(target) - 7}" text-anchor="end" class="target-label">目标 ${number(target)}%</text>`
    : "";
  $("#trend-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}${targetLine}<path d="${path}" class="acos-line"/>${points}${labels}</svg>`;
  const latest = rows[rows.length - 1].kpis;
  $("#trend-summary").textContent = `当前 ${number(latest.acos)}% · ${rows.length} 个周期`;
}

function activityRows() {
  const decisions = (dashboard.recent_cycles || []).flatMap(cycle => (cycle.decisions || []).map(item => ({
    time: item.created_at || cycle.created_at,
    state: item.status || "planned",
    title: actionLabel(item.action_type),
    detail: item.reason || "Hermes 已生成确定性广告调整",
    meta: item.entity_id || item.entity_type,
  })));
  const actions = (dashboard.recent_actions || [])
    .filter(item => item.phase === "after" && item.operation === "write")
    .map(item => ({
      time: item.created_at,
      state: item.outcome_status || (item.success ? "success" : "unknown"),
      title: item.success ? "已执行广告调整" : "广告调整待确认",
      detail: item.reason || item.result_summary || item.tool_name,
      meta: item.tool_name,
    }));
  const verifications = (dashboard.recent_verifications || []).map(item => ({
    time: item.created_at,
    state: item.status,
    title: item.status === "verified" ? "独立回读已确认" : "独立回读发现差异",
    detail: item.message || "Amazon 实际状态已重新查询",
    meta: item.decision_id,
  }));
  return [...actions, ...verifications, ...decisions]
    .sort((a, b) => String(b.time || "").localeCompare(String(a.time || "")))
    .slice(0, 12);
}

function actionLabel(action) {
  const labels = {
    update_bid: "调整竞价",
    increase_budget: "提高预算",
    decrease_budget: "降低预算",
    update_placement: "调整 Placement",
    add_negative_exact: "添加否定精准",
    harvest_exact_keyword: "收割精准关键词",
    create_campaign: "创建 Campaign",
    create_ad_group: "创建 Ad Group",
    create_ad: "创建 Product Ad",
    create_target: "创建 Target",
    create_keyword: "创建 Keyword",
    pause: "暂停高风险实体",
    enable: "恢复已验证实体",
  };
  return labels[action] || action || "广告调整";
}

function feedItem(item, alert = false) {
  const state = item.state || item.severity || "info";
  return `<div class="feed-item">
    <span class="feed-dot ${statusClass(state)}"></span>
    <div><div class="feed-title"><strong>${esc(item.title || item.code || "系统消息")}</strong><span>${esc(item.time || item.created_at || "")}</span></div>
    <p>${esc(item.detail || item.message || "")}</p>${item.meta && !alert ? `<small>${esc(item.meta)}</small>` : ""}</div>
  </div>`;
}

function renderFeeds() {
  const activities = activityRows();
  $("#activity-count").textContent = String(activities.length);
  $("#activity-list").innerHTML = activities.length
    ? activities.map(item => feedItem(item)).join("")
    : '<div class="empty-state">暂无操作。Hermes 会在获得成熟数据后自动运行。</div>';

  const alerts = dashboard.alerts || [];
  $("#alert-count").textContent = String(alerts.length);
  $("#alert-list").innerHTML = alerts.length
    ? alerts.slice(0, 10).map(item => feedItem({...item, title: item.code || "异常", detail: item.message, time: item.created_at}, true)).join("")
    : '<div class="healthy-state"><strong>运行正常</strong><span>没有需要你处理的异常</span></div>';
}

function renderHealth() {
  const catalog = dashboard.catalog || {};
  const reports = dashboard.reports || {};
  const counts = reports.counts || {};
  const pendingReports = ["REQUESTED", "SUBMITTED", "IN_PROGRESS", "SUCCEEDED", "DOWNLOADED", "VALIDATED"]
    .reduce((total, key) => total + Number(counts[key] || 0), 0);
  const failedReports = Number(counts.FAILED || 0) + Number(counts.QUARANTINED || 0);
  const storage = dashboard.storage || {};
  const pressure = storage.latest_maintenance?.pressure || "normal";
  $("#execution-health").textContent = readiness?.writable ? "执行 可写" : `执行 ${String(readiness?.operational_state || "等待").toUpperCase()}`;
  $("#execution-health").className = readinessClass(String(readiness?.operational_state || ""));
  $("#catalog-health").textContent = `MCP ${number(catalog.tools, 0)} 工具 / ${number(catalog.drifted, 0)} 漂移`;
  $("#catalog-health").className = catalog.tools && !catalog.drifted ? "good" : "warn";
  $("#report-health").textContent = failedReports ? `报告 ${failedReports} 异常` : pendingReports ? `报告 ${pendingReports} 处理中` : "报告 正常";
  $("#report-health").className = failedReports ? "bad" : pendingReports ? "warn" : "good";
  $("#storage-health").textContent = `存储 ${number(storage.files?.total_mb || 0)}MB`;
  $("#storage-health").className = pressure === "hard" ? "bad" : pressure === "soft" ? "warn" : "good";
}

function render() {
  const settings = dashboard.settings || {};
  const mode = settings.mode || "observe";
  const [title, copy] = modeText[mode] || [mode, "未知运行模式"];
  $("#mode-title").textContent = title;
  $("#mode-copy").textContent = copy;
  $("#mode-pill").textContent = mode === "autopilot" ? "AUTOPILOT" : mode.toUpperCase();
  $("#mode-pill").className = `status-pill ${statusClass(mode)}`;
  $$("[data-mode]").forEach(button => button.classList.toggle("active", button.dataset.mode === mode));
  const state = String(readiness?.operational_state || "unknown");
  $("#readiness-pill").textContent = state.toUpperCase();
  $("#readiness-pill").className = `status-pill ${readinessClass(state)}`;
  $("#readiness-pill").title = (readiness?.blocking_checks || []).join(", ") || "无阻断项";
  $("#generated").textContent = `更新于 ${new Date(dashboard.generated_at).toLocaleString()}`;
  $("#target-acos").value = settings.target_acos ?? 30;
  $("#max-campaign-budget").value = settings.sealed_sp_max_campaign_budget ?? 50;
  $("#currency-label").textContent = dashboard.profiles?.[0]?.currency || "账户货币";
  renderKpis();
  renderTrend();
  renderFeeds();
  renderHealth();
}

async function readinessStatus() {
  try {
    const response = await fetch("/health/ready", {credentials: "same-origin", headers: {Accept: "application/json"}});
    const data = await response.json().catch(() => ({operational_state: "unavailable", blocking_checks: ["invalid_response"]}));
    return {...data, http_status: response.status};
  } catch (error) {
    return {operational_state: "unavailable", writable: false, blocking_checks: ["readiness_unavailable"], detail: String(error)};
  }
}

async function refresh() {
  [dashboard, readiness] = await Promise.all([api("/api/dashboard"), readinessStatus()]);
  render();
}

async function boot() {
  const session = await api("/api/session");
  if (session.authenticated) {
    csrf = session.csrf;
    $("#login").hidden = true;
    $("#app").hidden = false;
    await refresh();
  }
}

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const result = await api("/api/login", {method: "POST", body: JSON.stringify({password: $("#password").value})});
    csrf = result.csrf;
    $("#login").hidden = true;
    $("#app").hidden = false;
    await refresh();
  } catch (error) {
    $("#login-error").textContent = error.message || String(error);
  }
});

$("#refresh").addEventListener("click", event => mutate(event.currentTarget, refresh, "已刷新").catch(() => {}));
$("#logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", {method: "POST", body: "{}"});
    location.reload();
  } catch (error) {
    showNotice(error.message || String(error));
  }
});

$$("[data-mode]").forEach(button => button.addEventListener("click", () => mutate(button, async () => {
  const mode = button.dataset.mode;
  await api("/api/settings", {method: "PUT", body: JSON.stringify({mode, execution_enabled: mode === "autopilot"})});
  await refresh();
}, mode === "autopilot" ? "全托管已启动" : mode === "paused" ? "自动化已暂停" : "已切换为仅观察").catch(() => {})));

$("#strategy-form").addEventListener("submit", event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  mutate(button, async () => {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        target_acos: Number($("#target-acos").value),
        sealed_sp_max_campaign_budget: Number($("#max-campaign-budget").value),
      }),
    });
    $("#strategy-message").textContent = "已保存";
    await refresh();
  }, "运营目标已更新").catch(() => {});
});

boot().catch(error => showNotice(error.message || String(error)));
setInterval(() => { if (!$("#app").hidden) refresh().catch(error => showNotice(error.message || String(error))); }, 30000);
