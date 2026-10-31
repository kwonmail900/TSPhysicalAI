"use strict";

const $ = (id) => document.getElementById(id);
const state = { records: [], demo: null, demoSeed: null, source: "synthetic", resolution: "hourly", result: null, index: 0, timer: null, busy: false };
const names = { no_ess: "ESS 없음", reactive: "현재값 반응형", day_ahead: "하루 전 계획 유지", feedback: "예측 + 피드백" };
const scenarios = { normal: "정상 운전", load_surge: "부하 증가", pv_drop: "태양광 발전 감소", sensor_freeze: "SOC 센서값 정체" };
const fmt = (v, digits = 1) => v == null || !Number.isFinite(v) ? "—" : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
const pct = (v) => v == null ? "—" : `${fmt(v * 100)}%`;
const stamp = (v) => v ? String(v).replace("T", " ").slice(0, 16) : "—";
const setText = (id, value) => { $(id).textContent = value; };

function notify(message, mode = "") {
  setText("status", message);
  $("status").className = `status ${mode}`;
}
function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle("busy", busy);
  for (const id of ["run-button", "demo-button", "csv-file", "resolution", "example-button"]) $(id).disabled = busy;
}
async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  let payload;
  try { payload = await response.json(); } catch (_) { throw new Error(`서버 응답을 읽을 수 없습니다 (${response.status})`); }
  if (!response.ok) throw new Error(payload.error || payload.message || `요청 실패 (${response.status})`);
  return payload;
}
function readConfig() {
  const value = (id) => Number($(id).value);
  return { resolution: state.resolution, forecast_model: $("forecast-model").value, capacity_kwh: value("capacity"), max_power_kw: value("power"), initial_soc: value("initial-soc") / 100, min_soc: value("min-soc") / 100, max_soc: value("max-soc") / 100, efficiency: value("efficiency") / 100, reserve_soc: value("reserve-soc") / 100, scenario: $("scenario").value, scenario_strength: value("strength"), seed: value("seed") };
}
function renderSource() {
  const synthetic = state.source === "synthetic";
  setText("source-badge", synthetic ? "합성 시연 자료" : "사용자 업로드 자료");
  $("source-badge").className = `source-badge${synthetic ? "" : " uploaded"}`;
  if (!state.records.length) return;
  const interval = state.resolution === "daily" ? "일별" : "시간별";
  setText("data-description", `${stamp(state.records[0].timestamp)} ~ ${stamp(state.records.at(-1).timestamp)} · ${interval} 에너지`);
  setText("data-note", synthetic ? "재현 가능한 합성 데이터입니다. 실제 캠퍼스·태양광 실측 결과가 아닙니다." : "업로드 자료를 사용합니다. 현장·기간·단위 일치 여부와 이용 권한은 사용자가 확인하세요.");
  setText("data-count", `${state.records.length.toLocaleString("ko-KR")}개 구간`);
}
async function loadDemo() {
  stopPlayback(); setBusy(true); notify("합성 데이터를 준비하고 있습니다…", "busy");
  const selectedResolution = $("resolution").value;
  try {
    const seed = Number($("seed").value);
    const payload = await api(`/api/demo?resolution=${encodeURIComponent(selectedResolution)}&seed=${seed}`);
    state.records = payload.records; state.demo = payload.records; state.demoSeed = seed; state.source = "synthetic"; state.resolution = payload.resolution || selectedResolution;
    renderSource(); await runExperiment(true);
  } catch (error) { notify(error.message, "error"); } finally { setBusy(false); }
}
async function uploadFile(event) {
  const file = event.target.files[0]; if (!file) return;
  stopPlayback(); setBusy(true); notify("CSV의 시간축과 단위를 검사하고 있습니다…", "busy");
  try {
    if (file.size > 4 * 1024 * 1024) throw new Error("CSV 파일은 4MB 이하로 준비하세요.");
    const payload = await api("/api/upload", { csv_text: await file.text(), resolution: $("resolution").value });
    state.records = payload.records; state.source = "uploaded"; state.resolution = payload.resolution || $("resolution").value;
    renderSource(); await runExperiment(true);
  } catch (error) { notify(`자료를 불러오지 못했습니다: ${error.message}`, "error"); } finally { setBusy(false); event.target.value = ""; }
}
async function runExperiment(alreadyBusy = false) {
  if (state.busy && !alreadyBusy) return;
  if (!state.records.length) { notify("먼저 합성 데이터 또는 CSV를 불러오세요.", "error"); return; }
  stopPlayback(); if (!alreadyBusy) setBusy(true);
  notify("동일한 조건으로 네 운영 정책을 실행하고 있습니다…", "busy");
  try {
    const config = readConfig();
    if (state.source === "synthetic" && state.demoSeed !== config.seed) {
      const payload = await api(`/api/demo?resolution=${encodeURIComponent(state.resolution)}&seed=${config.seed}`);
      state.records = payload.records; state.demo = payload.records; state.demoSeed = config.seed; renderSource();
    }
    const result = await api("/api/run", { records: state.records, source: state.source, config });
    state.result = result; state.index = 0;
    state.result.frontend_provenance = { source: state.source, input_resolution: state.resolution, input_rows: state.records.length, synthetic_seed: state.source === "synthetic" ? state.demoSeed : null };
    renderExperiment();
    const e = result.evaluation;
    notify(`실험 완료 · ${e.steps.toLocaleString("ko-KR")}개 평가 구간 · ${scenarios[result.config.scenario]} · 앞 ${fmt(e.warmup_steps * e.dt_hours / 24, 0)}일은 예측 이력으로 사용했습니다.`);
  } catch (error) { notify(`실험을 실행하지 못했습니다: ${error.message}`, "error"); } finally { if (!alreadyBusy) setBusy(false); }
}
function currentRun() { return state.result?.runs.find((run) => run.policy === $("policy").value) || state.result?.runs[0]; }
function renderExperiment() {
  const result = state.result; if (!result) return;
  const run = currentRun(); const metric = result.comparison.find((m) => m.policy === run.policy);
  const daily = result.resolution === "daily";
  setText("metric-import", `${fmt(metric.import_kwh)} kWh`);
  setText("metric-self", pct(metric.pv_self_consumption_rate));
  document.querySelectorAll(".metric")[1].querySelector("small").textContent = daily ? "일별 자료로 실제 자가소비율 산출 불가" : "발전량 중 현지 소비·충전 비율";
  setText("metric-peak-label", daily ? "일평균 구매 전력 최댓값" : "최대 구매 전력");
  setText("metric-peak", `${fmt(metric.peak_import_kw)} kW`);
  setText("metric-peak-note", daily ? "일중 피크를 의미하지 않습니다" : "시간 구간 평균의 최댓값 · kW");
  setText("metric-soc", run.policy === "no_ess" ? "—" : pct(metric.final_soc));
  setText("metric-soc-note", run.policy === "no_ess" ? "ESS를 사용하지 않는 비교 기준" : `종료 저장량 ${fmt(metric.final_energy_kwh)} kWh`);
  setText("table-peak", daily ? "최대 일평균 kW" : "최대 구매 kW");
  setText("forecast-description", `${result.forecast_model === "weekday_mean" ? "최근 같은 요일 평균" : "전주 같은 시점"} · 일 시작 시 발행한 기본 예측`);
  const m = result.forecast_metrics;
  setText("forecast-error", `기본 예측 MAE: 부하 ${fmt(m.load_mae_kwh)} / 태양광 ${fmt(m.pv_mae_kwh)} kWh · WAPE: ${pct(m.load_wape)} / ${pct(m.pv_wape)} · ${m.n}개 구간`);
  const tbody = $("comparison-body"); tbody.replaceChildren();
  for (const row of result.comparison) {
    const tr = document.createElement("tr"); if (row.policy === run.policy) tr.className = "selected";
    const values = [row.label || names[row.policy], fmt(row.import_kwh), fmt(row.export_kwh), pct(row.pv_self_consumption_rate), fmt(row.peak_import_kw), row.policy === "no_ess" ? "—" : pct(row.final_soc), fmt(row.constraint_clips, 0)];
    for (const value of values) { const td = document.createElement("td"); td.textContent = value; tr.append(td); }
    tbody.append(tr);
  }
  $("storage-note").replaceChildren();
  for (const row of result.comparison.filter((item) => item.policy !== "no_ess")) {
    const note = document.createElement("p");
    note.textContent = `${names[row.policy]} · 저장량 ${fmt(row.initial_energy_kwh)} → ${fmt(row.final_energy_kwh)} kWh · 배터리 손실 ${fmt(row.battery_loss_kwh)} kWh`;
    $("storage-note").append(note);
  }
  $("timeline").max = run.rows.length - 1;
  for (const id of ["play-button", "reset-button", "timeline", "csv-export", "json-export"]) $(id).disabled = false;
  state.index = Math.min(state.index, run.rows.length - 1);
  renderStep(); renderCharts();
}
function renderStep() {
  const run = currentRun(); if (!run?.rows.length) return;
  const row = run.rows[state.index];
  $("timeline").value = state.index;
  setText("playback-time", stamp(row.timestamp)); setText("step-count", `${state.index + 1} / ${run.rows.length}`);
  const noBattery = run.policy === "no_ess";
  if (run.policy === "reactive") {
    setText("flow-observe", `현재 구간 순부하 관측 ${fmt(row.observed_net_kwh)} kWh\n판단 시 관측 SOC ${pct(row.decision_soc)}`);
  } else if (noBattery) {
    setText("flow-observe", "ESS 제어 없음\n구간 에너지 수지만 계산");
  } else {
    setText("flow-observe", `사전 예측: 부하 ${fmt(row.forecast_load_kwh)} · 발전 ${fmt(row.forecast_pv_kwh)} kWh\n직전 관측 SOC ${pct(row.decision_soc)}`);
  }
  setText("flow-decide", `${names[run.policy]}\n제안 ${signedPower(row.proposed_power_kw)}`);
  setText("flow-action", `적용 ${signedPower(row.applied_power_kw)}\n충전 ${fmt(row.charge_kwh)} · 방전 ${fmt(row.discharge_kwh)} kWh`);
  setText("flow-feedback", noBattery ? `실측 부하 ${fmt(row.load_kwh)} · 발전 ${fmt(row.pv_kwh)} kWh\n구매 ${fmt(row.import_kwh)} kWh` : `실제 SOC ${pct(row.soc)} · 관측 ${pct(row.observed_soc)}\n실측 부하 ${fmt(row.load_kwh)} · 발전 ${fmt(row.pv_kwh)} kWh\n구매 ${fmt(row.import_kwh)} kWh`);
  const events = String(row.event || "").split(";").filter(Boolean).map((event) => ({ load_surge: "부하 증가 구간", pv_drop: "발전 감소 구간", sensor_freeze: "SOC 센서값 정체 구간", constraint_clip: "물리적 제약에 따라 명령 보정" }[event] || event));
  const rule = run.policy === "reactive" ? "구간의 부하·발전을 즉시 측정할 수 있다고 가정한 반응형 제어입니다." : run.policy === "day_ahead" ? "일 시작 시 만든 계획을 유지합니다. 표시된 직전 SOC로 재계획하지 않습니다." : run.policy === "feedback" ? "직전 구간까지 확인한 오차와 관측 SOC로 재계획합니다." : "ESS 없이 부하와 발전의 차이를 전력망이 공급·수용합니다.";
  setText("event-description", `${rule}${events.length ? ` · ${events.join(" · ")}` : " · 정상 상태"}`);
  for (const cursor of document.querySelectorAll(".chart-cursor")) {
    const ratio = run.rows.length > 1 ? state.index / (run.rows.length - 1) : 0;
    const x = 42 + ratio * 490; cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
  }
}
function signedPower(value) { return value == null ? "—" : `${value >= 0 ? "+" : ""}${fmt(value)} kW${Math.abs(value) < 1e-8 ? " · 대기" : value > 0 ? " · 충전" : " · 방전"}`; }
function stopPlayback() { clearInterval(state.timer); state.timer = null; setText("play-button", "▶ 재생"); }
function play() {
  if (state.timer) { stopPlayback(); return; }
  const run = currentRun(); if (!run?.rows.length) return;
  if (state.index >= run.rows.length - 1) state.index = 0;
  setText("play-button", "Ⅱ 일시정지");
  state.timer = setInterval(() => { state.index += 1; renderStep(); if (state.index >= run.rows.length - 1) stopPlayback(); }, 1000 / Number($("speed").value));
}

const NS = "http://www.w3.org/2000/svg";
function svgNode(tag, attributes = {}, content) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (content !== undefined) node.textContent = content;
  return node;
}
function chart(target, rows, series, hasRightAxis = false) {
  const host = $(target); host.replaceChildren(); if (!rows.length) return;
  const svg = svgNode("svg", { viewBox: "0 0 572 218", "aria-hidden": "true" });
  const left = 42, right = 532, top = 10, bottom = 180;
  const leftValues = rows.flatMap((r) => series.filter((s) => !s.right).map((s) => r[s.key])).filter((v) => v != null && Number.isFinite(v));
  const max = Math.max(1, ...leftValues) * 1.1;
  for (let i = 0; i <= 4; i++) {
    const y = top + (bottom - top) * i / 4;
    svg.append(svgNode("line", { x1: left, y1: y, x2: right, y2: y, stroke: "#e9eff3", "stroke-width": 1 }));
    svg.append(svgNode("text", { x: left - 8, y: y + 3, "text-anchor": "end", fill: "#8295a1", "font-size": 9 }, fmt(max * (1 - i / 4), max > 100 ? 0 : 1)));
    if (hasRightAxis) svg.append(svgNode("text", { x: right + 7, y: y + 3, fill: "#8295a1", "font-size": 9 }, `${100 - i * 25}%`));
  }
  const stride = Math.max(1, Math.floor(rows.length / 1200));
  for (const s of series) {
    let path = "";
    rows.forEach((row, index) => {
      if (index % stride !== 0 && index !== rows.length - 1) return;
      const v = row[s.key]; if (v == null || !Number.isFinite(v)) return;
      const x = left + (right - left) * index / Math.max(1, rows.length - 1);
      const y = bottom - (bottom - top) * Math.max(0, v) / (s.right ? 1 : max);
      path += `${path ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    });
    svg.append(svgNode("path", { d: path, stroke: s.color, fill: "none", "stroke-width": s.dash ? 1.1 : 1.7, "stroke-dasharray": s.dash ? "4 4" : "none", "stroke-opacity": s.dash ? .6 : .95, "stroke-linejoin": "round" }));
  }
  for (let i = 0; i < 3; i++) {
    const index = Math.round((rows.length - 1) * i / 2);
    svg.append(svgNode("text", { x: left + (right - left) * i / 2, y: 203, "text-anchor": i === 0 ? "start" : i === 2 ? "end" : "middle", fill: "#8295a1", "font-size": 9 }, stamp(rows[index].timestamp).slice(0, 10)));
  }
  const cursorX = left + (right - left) * state.index / Math.max(1, rows.length - 1);
  svg.append(svgNode("line", { class: "chart-cursor", x1: cursorX, x2: cursorX, y1: top, y2: bottom, stroke: "#789596", "stroke-dasharray": "3 3", "stroke-width": 1 }));
  host.append(svg);
  host.setAttribute("aria-label", `${host.getAttribute("aria-label").split(" · ")[0]} · ${rows.length}개 구간. 상세 수치는 로그 CSV로 확인할 수 있습니다.`);
}
function renderCharts() {
  const run = currentRun(); if (!run) return;
  chart("forecast-chart", run.rows, [{ key: "load_kwh", color: "#087e80" }, { key: "pv_kwh", color: "#e89b46" }, { key: "base_forecast_load_kwh", color: "#087e80", dash: true }, { key: "base_forecast_pv_kwh", color: "#e89b46", dash: true }]);
  const batterySeries = run.policy === "no_ess" ? [] : [{ key: "soc", color: "#087e80", right: true }, { key: "observed_soc", color: "#087e80", right: true, dash: true }];
  chart("operation-chart", run.rows, [{ key: "import_kwh", color: "#507ab5" }, ...batterySeries], true);
}

function download(filename, text, type) {
  const blob = new Blob([text], { type }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function csv(records) {
  if (!records.length) return "";
  const columns = Object.keys(records[0]);
  const quote = (v) => `"${String(v == null ? "" : v).replaceAll('"', '""')}"`;
  return "\uFEFF" + columns.map(quote).join(",") + "\r\n" + records.map((row) => columns.map((key) => quote(row[key])).join(",")).join("\r\n");
}
$("config-form").addEventListener("submit", (event) => { event.preventDefault(); runExperiment(); });
$("config-form").addEventListener("input", (event) => {
  if (state.result && !state.busy && !["csv-file", "resolution"].includes(event.target.id)) notify("설정을 변경했습니다. ‘실험 실행’을 누르면 새 조건을 적용합니다. 현재 결과는 직전 실험입니다.");
});
$("demo-button").addEventListener("click", loadDemo);
$("resolution").addEventListener("change", loadDemo);
$("csv-file").addEventListener("change", uploadFile);
$("strength").addEventListener("input", () => setText("strength-value", `${Math.round(Number($("strength").value) * 100)}%`));
$("policy").addEventListener("change", () => { stopPlayback(); renderExperiment(); });
$("play-button").addEventListener("click", play);
$("reset-button").addEventListener("click", () => { stopPlayback(); state.index = 0; renderStep(); });
$("timeline").addEventListener("input", () => { stopPlayback(); state.index = Number($("timeline").value); renderStep(); });
$("speed").addEventListener("change", () => { if (state.timer) { stopPlayback(); play(); } });
$("example-button").addEventListener("click", async () => {
  try { const seed = Number($("seed").value); const records = state.demo && state.demoSeed === seed ? state.demo : (await api(`/api/demo?resolution=${state.resolution}&seed=${seed}`)).records; download(`synthetic_${state.resolution}_seed${seed}.csv`, csv(records), "text/csv;charset=utf-8"); } catch (error) { notify(error.message, "error"); }
});
$("csv-export").addEventListener("click", () => { const run = currentRun(); if (run) download(`energy_loop_${run.policy}_${state.result.resolution}.csv`, csv(run.rows), "text/csv;charset=utf-8"); });
$("json-export").addEventListener("click", () => { if (state.result) download("energy_loop_experiment.json", JSON.stringify(state.result, null, 2), "application/json;charset=utf-8"); });
$("reflection-export").addEventListener("click", () => download("energy_loop_learning_note.txt", `에너지 루프 · 나의 실험 노트\n\n설정\n${JSON.stringify(state.result?.config || readConfig(), null, 2)}\n\n데이터\n${JSON.stringify(state.result?.frontend_provenance || { source: state.source }, null, 2)}\n\n나의 기록\n${$("reflection").value}\n`, "text/plain;charset=utf-8"));
window.addEventListener("pagehide", stopPlayback);
loadDemo();
