const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { result: null, editing: false };
const presets = {
  stp: { name: "550 KLD Sewage Treatment Plant, Karnal", typology: "stp_tank", start: "2026-04-25", days: 150, structures: 1 },
  mep: { name: "Road redevelopment MEP works, Narsi Village", typology: "linear_mep", start: "2026-04-25", days: 130, structures: 1 },
  rwh: { name: "Rain Water Harvesting System, Narsi Village", typology: "rwh", start: "2026-04-25", days: 90, structures: 2 },
};

const money = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const compactMoney = (value) => {
  if (Math.abs(value) >= 1e7) return `₹${(value / 1e7).toFixed(2)} cr`;
  if (Math.abs(value) >= 1e5) return `₹${(value / 1e5).toFixed(2)} L`;
  return money.format(value || 0);
};
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const titleCase = (value) => String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function selectSample(id) {
  $$(".sample").forEach((button) => button.classList.toggle("active", button.dataset.sample === id));
  $("#sampleId").value = id;
  $("#boqFile").value = "";
  $("#uploadLabel").classList.remove("has-file");
  $("#uploadLabel b").textContent = "Upload your BOQ";
  const preset = presets[id];
  $("#projectName").value = preset.name;
  $("#typology").value = preset.typology;
  $("#startDate").value = preset.start;
  $("#contractDays").value = preset.days;
  $("#structureCount").value = preset.structures;
}

$$('.sample').forEach((button) => button.addEventListener("click", () => selectSample(button.dataset.sample)));
$("#boqFile").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  $("#sampleId").value = "";
  $$(".sample").forEach((button) => button.classList.remove("active"));
  $("#uploadLabel").classList.add("has-file");
  $("#uploadLabel b").textContent = file.name;
  if (!$("#projectName").value || Object.values(presets).some((preset) => preset.name === $("#projectName").value)) {
    $("#projectName").value = file.name.replace(/\.[^.]+$/, "").replaceAll("_", " ");
  }
  $("#typology").value = "auto";
});
$("#resetBtn").addEventListener("click", () => {
  $("#analysisForm").reset();
  selectSample("stp");
});

const loadingMessages = [
  "Reading and standardising BOQ rows…",
  "Classifying work packages and detecting typology…",
  "Building dependency tracks and solving CPM…",
  "Time-phasing costs and running audit checks…",
];

$("#analysisForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $(".run-button");
  button.disabled = true;
  $("#emptyState").classList.add("hidden");
  $("#results").classList.add("hidden");
  $("#loadingState").classList.remove("hidden");
  let messageIndex = 0;
  const timer = setInterval(() => {
    messageIndex = (messageIndex + 1) % loadingMessages.length;
    $("#loadingText").textContent = loadingMessages[messageIndex];
  }, 700);
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: new FormData(event.target) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Analysis failed");
    state.result = data;
    renderResult(data);
    $("#loadingState").classList.add("hidden");
    $("#results").classList.remove("hidden");
  } catch (error) {
    $("#loadingState").classList.add("hidden");
    $("#emptyState").classList.remove("hidden");
    toast(error.message, true);
  } finally {
    clearInterval(timer);
    button.disabled = false;
  }
});

function renderResult(result) {
  const metrics = result.metrics;
  $("#resultTitle").textContent = result.project.name;
  const demonstration = result.project.data_provenance === "sanitised_demo";
  $("#provenancePill").textContent = demonstration ? "Sanitised demo" : "Uploaded source";
  $("#resultSubtitle").textContent = `${titleCase(result.typology)} · ${Math.round(result.typology_confidence * 100)}% typology confidence · job ${result.job_id}`;
  $("#exportLink").href = `/api/jobs/${result.job_id}/export.xlsx`;
  $("#metricValue").textContent = compactMoney(metrics.boq_total);
  const reconciled = Math.abs(metrics.cost_reconciliation_delta) < 1;
  $("#metricRecon").textContent = reconciled ? "✓ reconciled to schedule" : `${money.format(metrics.cost_reconciliation_delta)} delta`;
  $("#metricDuration").textContent = `${metrics.duration_working_days} days`;
  $("#metricFinish").textContent = `finish ${metrics.finish_date}`;
  $("#metricClassified").textContent = `${metrics.classified_percent}%`;
  $("#metricConfidence").textContent = `${Math.round(metrics.mean_classification_confidence * 100)}% mean confidence`;
  const reviewFlags = result.findings.filter((finding) => ["error", "high", "warning"].includes(finding.severity)).length;
  $("#metricFlags").textContent = reviewFlags;
  renderOverview(result);
  renderGantt(result.activities);
  renderCashflow(result.cashflow, result.independent_cashflow);
  renderBoq(result.items);
  renderAudit(result);
}

function renderOverview(result) {
  const critical = result.activities.filter((activity) => activity.critical);
  $("#criticalCount").textContent = `${critical.length} activities`;
  $("#criticalPath").innerHTML = critical.map((activity) => `<div class="path-node"><b>${esc(activity.id)}</b><small>${esc(activity.name)}</small></div>`).join("");
  const comparison = result.metrics.cashflow_comparison || {};
  $("#curveMae").textContent = `${comparison.monthly_mae_percent_of_value ?? 0}%`;
  $("#schedulePeak").textContent = comparison.schedule_peak_month || "—";
  $("#independentPeak").textContent = comparison.independent_peak_month || "—";
  $("#topFindings").innerHTML = result.findings.map((finding) => `<div class="finding"><span class="severity ${esc(finding.severity)}">${esc(finding.severity)}</span><div><b>${esc(finding.message)}</b><p>${esc(finding.recommendation)}</p></div></div>`).join("");
}

function monthLabels(startIso, endIso) {
  const labels = [];
  const current = new Date(`${startIso.slice(0, 7)}-01T00:00:00Z`);
  const end = new Date(`${endIso.slice(0, 7)}-01T00:00:00Z`);
  while (current <= end) {
    labels.push(current.toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" }));
    current.setUTCMonth(current.getUTCMonth() + 1);
  }
  return labels;
}

function renderGantt(activities) {
  const projectDays = Math.max(...activities.map((activity) => activity.finish_day), 1);
  const start = activities.reduce((min, activity) => activity.start_date < min ? activity.start_date : min, activities[0].start_date);
  const finish = activities.reduce((max, activity) => activity.finish_date > max ? activity.finish_date : max, activities[0].finish_date);
  const months = monthLabels(start, finish);
  const head = `<div class="gantt-head"><span>Activity / track</span><span>Days</span><div class="timeline" style="--months:${months.length}">${months.map((month) => `<span>${esc(month)}</span>`).join("")}</div></div>`;
  const rows = activities.map((activity) => {
    const left = `${100 * activity.start_day / projectDays}%`;
    const width = `${100 * activity.duration_days / projectDays}%`;
    const classes = `${activity.critical ? "critical" : ""} ${activity.duration_days === 0 ? "milestone-bar" : ""}`;
    return `<div class="gantt-row" data-id="${esc(activity.id)}"><span class="activity-name"><b>${esc(activity.name)}</b><small>${esc(activity.id)} · ${esc(activity.track)}</small></span><span class="duration-input"><input type="number" min="0" value="${activity.duration_days}" disabled aria-label="Duration for ${esc(activity.name)}" /> d</span><div class="timeline" style="--months:${months.length}"><i class="bar ${classes}" style="--left:${left};--width:${width}" data-label="${esc(activity.start_date)} → ${esc(activity.finish_date)}"></i></div></div>`;
  }).join("");
  $("#gantt").innerHTML = head + rows;
  state.editing = false;
  $("#editSchedule").textContent = "Edit durations";
  $("#replanBar").classList.add("hidden");
}

function renderCashflow(primary, independent) {
  const primaryMap = Object.fromEntries(primary.map((period) => [period.period, period.cumulative_percent]));
  const independentMap = Object.fromEntries(independent.map((period) => [period.period, period.cumulative_percent]));
  const months = [...new Set([...Object.keys(primaryMap), ...Object.keys(independentMap)])].sort();
  const width = 820, height = 250, pad = { left: 42, right: 14, top: 12, bottom: 31 };
  const x = (index) => pad.left + index * (width - pad.left - pad.right) / Math.max(months.length - 1, 1);
  const y = (value) => height - pad.bottom - value * (height - pad.top - pad.bottom) / 100;
  const points = (map) => {
    let carried = 0;
    return months.map((month, index) => {
      if (map[month] != null) carried = map[month];
      return `${x(index)},${y(carried)}`;
    }).join(" ");
  };
  const areaPoints = `${pad.left},${height - pad.bottom} ${points(primaryMap)} ${x(months.length - 1)},${height - pad.bottom}`;
  let grid = "";
  [0, 25, 50, 75, 100].forEach((tick) => { grid += `<line class="chart-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y(tick)}" y2="${y(tick)}"/><text class="chart-label" x="3" y="${y(tick) + 3}">${tick}%</text>`; });
  const labels = months.map((month, index) => `<text class="chart-label" x="${x(index)}" y="${height - 9}" text-anchor="middle">${esc(month.slice(5))}/${esc(month.slice(2, 4))}</text>`).join("");
  const secondary = independent.length ? `<polyline class="chart-line secondary" points="${points(independentMap)}"/>` : "";
  $("#cashChart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cumulative cash flow chart"><defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0e7c7b"/><stop offset="1" stop-color="#fff"/></linearGradient></defs>${grid}<polygon class="chart-area" points="${areaPoints}"/><polyline class="chart-line" points="${points(primaryMap)}"/>${secondary}${labels}</svg>`;
  $("#cashRows").innerHTML = primary.map((period) => `<tr><td><strong>${esc(period.period)}</strong></td><td>${money.format(period.gross_work_value)}</td><td>${money.format(period.planned_expenditure)}</td><td>${money.format(period.certified_receipt)}</td><td>${money.format(period.net_cash_flow)}</td><td>${period.cumulative_percent.toFixed(1)}%</td></tr>`).join("");
}

function renderBoq(items, query = "") {
  const needle = query.trim().toLowerCase();
  const visible = items.filter((item) => !needle || item.description.toLowerCase().includes(needle) || item.work_package.includes(needle));
  $("#boqRows").innerHTML = visible.map((item) => `<tr><td><strong>${esc(item.id)}</strong></td><td>${esc(item.description)}</td><td>${item.quantity.toLocaleString("en-IN")} ${esc(item.unit)}</td><td>${money.format(item.amount)}</td><td>${esc(titleCase(item.work_package))}<br><small>${esc(item.track)}</small></td><td><span class="confidence"><i style="--confidence:${Math.round(item.confidence * 100)}%"></i></span>${Math.round(item.confidence * 100)}%</td><td>${esc(item.evidence.join(", ") || "Planner review")}</td></tr>`).join("");
}

function renderAudit(result) {
  $("#traceList").innerHTML = result.trace.map((event) => `<div class="trace-item"><span class="trace-number">${event.step}</span><div><b>${esc(titleCase(event.tool))} · ${esc(event.status)}</b><p>${esc(event.summary)}</p><code>${esc(JSON.stringify(event.details))}</code></div></div>`).join("");
  $("#assumptionList").innerHTML = result.assumptions.map((assumption) => `<li>${esc(assumption)}</li>`).join("");
}

$$('.tabs button').forEach((button) => button.addEventListener("click", () => {
  $$(".tabs button").forEach((tab) => tab.classList.toggle("active", tab === button));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.tab));
}));

$("#editSchedule").addEventListener("click", () => {
  state.editing = !state.editing;
  $$("#gantt .duration-input input").forEach((input) => { input.disabled = !state.editing; });
  $("#editSchedule").textContent = state.editing ? "Cancel edits" : "Edit durations";
  $("#replanBar").classList.toggle("hidden", !state.editing);
  if (!state.editing && state.result) renderGantt(state.result.activities);
});

$("#saveReplan").addEventListener("click", async () => {
  if (!state.result) return;
  const overrides = $$("#gantt .gantt-row").map((row) => ({ id: row.dataset.id, duration_days: Number($("input", row).value) }));
  try {
    const response = await fetch(`/api/jobs/${state.result.job_id}/replan`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ overrides }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Replanning failed");
    state.result = data;
    renderResult(data);
    toast("CPM and cash flow recalculated from planner overrides.");
  } catch (error) { toast(error.message, true); }
});

$("#boqSearch").addEventListener("input", (event) => state.result && renderBoq(state.result.items, event.target.value));

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 4200);
}

selectSample("stp");
