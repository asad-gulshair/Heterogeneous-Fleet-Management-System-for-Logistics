// Analytics, Scenario comparison, What-if / ROI and Integration pages.
import { $, api, S, fmtT, KC } from './app.js';

const C = { text: '#93a8cc', grid: 'rgba(120,160,255,.10)' };
let charts = {}, timer = null, inited = {};
const KPI_ROWS = [
  ['completed_in_shift', 'Orders completed in shift', ''], ['orders_per_hour', 'Throughput', 'orders/h'],
  ['avg_wait', 'Average wait', 's'], ['avg_cycle', 'Average cycle time', 's'], ['utilization', 'Fleet utilization', '%'],
  ['energy_kwh', 'Energy used', 'kWh'], ['traffic_delay_min', 'Robot time lost to traffic', 'min'],
  ['operator_interventions', 'Operator interventions', ''], ['deadlocks', 'Deadlocks detected & auto-resolved', ''],
  ['permanent_deadlocks', 'Permanent deadlocks', ''], ['collisions', 'Collisions', ''], ['faults', 'Robot faults', ''],
  ['reassigned', 'Orders reassigned after faults', ''],
];
const LOWER = new Set(['avg_wait', 'avg_cycle', 'energy_kwh', 'traffic_delay_min', 'operator_interventions', 'permanent_deadlocks', 'collisions']);
// context-dependent KPIs are shown without a good/bad colour (e.g. TusCore detects and resolves MORE deadlocks because it can see all robots)
const NEUTRAL = new Set(['deadlocks', 'utilization', 'faults', 'reassigned']);
const fmt = (v, d = 1) => v == null ? '—' : (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (+v).toFixed(d));

function baseOpts(extra = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 }, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { color: C.text, boxWidth: 10, boxHeight: 10, font: { size: 11 } } }, tooltip: { backgroundColor: '#06122d', borderColor: '#24438a', borderWidth: 1 } },
    scales: { x: { ticks: { color: C.text, maxTicksLimit: 8, font: { size: 10 } }, grid: { color: C.grid } }, y: { ticks: { color: C.text, font: { size: 10 } }, grid: { color: C.grid }, beginAtZero: true } },
    ...extra,
  };
}
function chart(id, cfg) { if (charts[id]) { charts[id].destroy(); } charts[id] = new Chart($(id), cfg); return charts[id]; }

export function initPages() {
  $('cmpRun').onclick = runCompare; $('wiRun').onclick = runWhatIf;
  $('wiLoad').oninput = e => $('wiLoadLbl').textContent = '×' + (+e.target.value).toFixed(2);
  S.listeners.push(what => { if (what === 'map') inited.whatif = false; });
}

export function showPage(p) {
  clearInterval(timer); timer = null;
  if (p === 'analytics') { refreshAnalytics(); timer = setInterval(refreshAnalytics, 4000); }
  if (p === 'compare' && !inited.compare) { inited.compare = true; loadLatest('comparison', renderCompare); }
  if (p === 'whatif' && !inited.whatif) { inited.whatif = true; buildWhatIf(); loadLatest('whatif', renderWhatIf); }
  if (p === 'integration') renderIntegration();
}
async function loadLatest(kind, render) { try { const r = await api.get('/api/experiments/latest/' + kind); if (r) render(r); } catch (e) { /* none yet */ } }

// ------------------------------------------------------------------ analytics
async function refreshAnalytics() {
  if (!S.MAP) return;
  const [hist, rep] = await Promise.all([api.get('/api/history'), api.get('/api/report')]);
  const labels = hist.map(h => fmtT(h.t).slice(0, 5));
  const line = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color + '33', pointRadius: 0, borderWidth: 2, tension: .3, ...extra });
  if (!charts.chThroughput) {
    chart('chThroughput', { type: 'line', data: { labels, datasets: [line('Throughput (orders/h, last hour)', [], '#3ddc97', { fill: true }), line('Orders waiting', [], '#f5a524', { yAxisID: 'y1' })] },
      options: baseOpts({ scales: { ...baseOpts().scales, y1: { position: 'right', ticks: { color: C.text }, grid: { display: false }, beginAtZero: true } } }) });
    chart('chUtil', { type: 'line', data: { labels, datasets: Object.entries(S.MAP.types).map(([k, t]) => line(t.label, [], t.color)) }, options: baseOpts({ scales: { ...baseOpts().scales, y: { ...baseOpts().scales.y, max: 100, ticks: { color: C.text, callback: v => v + '%' } } } }) });
    chart('chBattery', { type: 'line', data: { labels, datasets: [line('Average battery %', [], '#38bdf8', { fill: true }), line('Robots faulted', [], '#ff5d6c', { yAxisID: 'y1', stepped: true })] },
      options: baseOpts({ scales: { ...baseOpts().scales, y: { ...baseOpts().scales.y, max: 100 }, y1: { position: 'right', ticks: { color: C.text, stepSize: 1 }, grid: { display: false }, beginAtZero: true, suggestedMax: 3 } } }) });
    chart('chMix', { type: 'bar', data: { labels: ['Box pick', 'High-shelf pick', 'Pallet move', 'Inventory scan'], datasets: [{ label: 'Completed', data: [], backgroundColor: Object.values(KC), borderRadius: 6 }] },
      options: baseOpts({ plugins: { legend: { display: false } } }) });
  }
  const T = charts.chThroughput; T.data.labels = labels; T.data.datasets[0].data = hist.map(h => h.throughput); T.data.datasets[1].data = hist.map(h => h.queued); T.update('none');
  const U = charts.chUtil; U.data.labels = labels; Object.keys(S.MAP.types).forEach((k, i) => { const raw = hist.map(h => (h.busy || {})[k] ?? null); U.data.datasets[i].data = raw.map((v, j) => { const w = raw.slice(Math.max(0, j - 2), j + 1).filter(x => x != null); return w.length ? Math.round(w.reduce((a, b) => a + b, 0) / w.length) : null; }); }); U.update('none');
  const B = charts.chBattery; B.data.labels = labels; B.data.datasets[0].data = hist.map(h => h.battery); B.data.datasets[1].data = hist.map(h => h.faulted); B.update('none');
  const bk = rep.kpis.by_kind; charts.chMix.data.datasets[0].data = [bk.box_pick, bk.high_pick, bk.pallet_move, bk.inventory_scan]; charts.chMix.update('none');
  $('robotTable').innerHTML = `<tr><th>Robot</th><th>Vendor</th><th>Interface</th><th>Busy</th><th>Distance</th><th>Energy</th><th>Battery</th><th>Faults</th><th>State</th></tr>` +
    rep.robots.map(r => `<tr><td><b>${r.id.replace(/_/g, ' ')}</b></td><td>${r.vendor}</td><td class="mono" style="font-size:11px">${r.interface}</td><td>${r.busy_pct}%</td><td>${r.distance_cells} m</td><td>${r.energy_kwh} kWh</td><td>${r.battery_pct}%</td><td>${r.faults}</td><td><span class="chip ${r.state}">${r.state.replace('_', ' ')}</span></td></tr>`).join('');
}

// ------------------------------------------------------------------ jobs
async function poll(job, bar, status, done) {
  for (;;) {
    const j = await api.get('/api/experiments/' + job);
    $(bar).style.width = (100 * j.done / j.total) + '%';
    $(status).textContent = j.status === 'running' ? `Running ${j.done}/${j.total} shifts…` : j.status === 'error' ? 'Error: ' + j.error : `Finished ${j.total} shifts`;
    if (j.status !== 'running') { if (j.result) done(j.result); return j; }
    await new Promise(r => setTimeout(r, 1000));
  }
}

// ------------------------------------------------------------------ comparison
async function runCompare() {
  const b = $('cmpRun'); b.disabled = true;
  try {
    const { job } = await api.post('/api/experiments/compare', { runs: +$('cmpRuns').value, shift_hours: +$('cmpHours').value, load_factor: +$('cmpLoad').value });
    await poll(job, 'cmpBar', 'cmpStatus', renderCompare);
  } catch (e) { $('cmpStatus').textContent = e.message; }
  b.disabled = false;
}
function deltaCell(key, d) {
  if (d == null) return '<td>—</td>';
  const good = LOWER.has(key) ? d < 0 : d > 0, cls = Math.abs(d) < 0.5 || NEUTRAL.has(key) ? '' : good ? 'good' : 'badv';
  return `<td class="${cls}">${d > 0 ? '+' : ''}${d}%</td>`;
}
function renderCompare(r) {
  const A = r.summary.A, B = r.summary.B, d = r.delta_pct, m = r.meta;
  $('cmpMeta').textContent = `${m.runs} × ${m.shift_hours} h shifts per scenario · order volume ×${m.load_factor} · ${m.layout} · ${r.finished}`;
  $('cmpBar').style.width = '100%';
  const as = m.assumptions || {};
  $('assumptions').innerHTML = `<b>Model of scenario A</b> (editable in the layout YAML): a deadlock between two vendors' robots is invisible to both managers and lasts until an operator clears it (<b>${as.operator_response_s ?? 90} s</b>);
    a high-shelf pick is two separate jobs - the lift's manager reports done, the WMS then creates the tote job (<b>${as.wms_handoff_delay_s ?? 20} s</b> later); each vendor has <b>dedicated chargers</b>; idle robots of another vendor cannot be asked to make way.`;
  const head = (label, val, sub, cls = '') => `<div class="headline"><label>${label}</label><div class="big ${cls}">${val}</div><div class="sub">${sub}</div></div>`;
  const sign = v => (v > 0 ? '+' : '') + v + '%';
  $('cmpHead').innerHTML =
    head('Order cycle time', sign(d.avg_cycle), `${fmt(A.avg_cycle.mean)} s → ${fmt(B.avg_cycle.mean)} s`, d.avg_cycle <= 0 ? '' : 'neg') +
    head('Customer wait time', sign(d.avg_wait), `${fmt(A.avg_wait.mean)} s → ${fmt(B.avg_wait.mean)} s`, d.avg_wait <= 0 ? '' : 'neg') +
    head('Robot time lost to traffic', sign(d.traffic_delay_min), `${fmt(A.traffic_delay_min.mean, 0)} → ${fmt(B.traffic_delay_min.mean, 0)} min per shift`, d.traffic_delay_min <= 0 ? '' : 'neg') +
    head('Operator interventions', `${fmt(A.operator_interventions.mean, 0)} → ${fmt(B.operator_interventions.mean, 0)}`, 'per shift · zero collisions in both', 'neu');
  $('cmpTable').innerHTML = `<tr><th>KPI</th><th>A · separate</th><th>B · TusCore</th><th>Change</th></tr>` +
    KPI_ROWS.map(([k, label, unit]) => `<tr><td>${label}${unit ? ` <small>(${unit})</small>` : ''}</td><td>${fmt(A[k].mean)} <small>±${fmt(A[k].std)}</small></td><td>${fmt(B[k].mean)} <small>±${fmt(B[k].std)}</small></td>${deltaCell(k, d[k])}</tr>`).join('');
  const runsA = r.runs.A, runsB = r.runs.B;
  chart('chCmp', { type: 'bar', data: { labels: runsA.map(x => 'seed ' + x.seed), datasets: [
      { label: 'A · separate managers', data: runsA.map(x => x.avg_cycle), backgroundColor: '#f5a524cc', borderRadius: 4 },
      { label: 'B · TusCore', data: runsB.map(x => x.avg_cycle), backgroundColor: '#2E9FE8cc', borderRadius: 4 }] },
    options: baseOpts({ scales: { ...baseOpts().scales, y: { ...baseOpts().scales.y, title: { display: true, text: 'avg cycle time (s)', color: C.text } } } }) });
  const thr = d.completed_in_shift;
  $('cmpFoot').innerHTML = `How to read this: both scenarios received identical orders and identical robot faults. ` +
    (Math.abs(thr) < 1 ? `Throughput is about equal because at this order volume demand, not the fleet, is the limit - the gain shows up as faster order completion, far less congestion and no operator interventions. Raise the order volume to see capacity differences. ` : `TusCore completed ${sign(thr)} orders within the shift. `) +
    `All numbers come from the simulated digital twin and should be validated on site.`;
}

// ------------------------------------------------------------------ what-if + ROI
const COST = { tote_amr: 35000, scissor_lift: 65000, pallet_stacker: 80000, drone: 20000 };
function buildWhatIf() {
  const T = S.MAP.types;
  $('wiRobots').innerHTML = Object.entries(T).map(([k, t]) => `<div class="field"><label>${t.label}</label><div class="stepper">
    <button class="btn sm" data-step="${k}" data-d="-1">−</button><input id="wi_${k}" type="number" min="1" max="40" value="${t.count}"><button class="btn sm" data-step="${k}" data-d="1">+</button></div></div>`).join('');
  $('wiRobots').onclick = e => { const b = e.target.closest('[data-step]'); if (!b) return; const i = $('wi_' + b.dataset.step); i.value = Math.max(1, +i.value + +b.dataset.d); };
  $('wiBays').value = (S.MAP.chargers || []).length;
  $('wiLayout').innerHTML = (S.layouts || []).map(l => `<option value="${l.file}" ${l.active ? 'selected' : ''}>${l.name}</option>`).join('');
  $('roiInputs').innerHTML = Object.entries(T).map(([k, t]) => `<div class="field"><label>${t.label.split(' (')[0]} unit cost</label><input id="cost_${k}" type="number" step="1000" value="${COST[k] ?? 30000}"></div>`).join('') +
    `<div class="field"><label>Value per extra order</label><input id="roi_val" type="number" step="0.1" value="1.5"></div>
     <div class="field"><label>Value per hour of robot time</label><input id="roi_hr" type="number" step="1" value="12"></div>
     <div class="field"><label>Shifts per year</label><input id="roi_shifts" type="number" value="500"></div>
     <div class="field"><label>Currency</label><select id="roi_cur"><option>€</option><option>$</option><option>£</option><option>₺</option><option>PKR</option></select></div>`;
  $('roiInputs').oninput = () => renderROI(); $('roiInputs').onchange = () => renderROI();
}
async function runWhatIf() {
  const b = $('wiRun'); b.disabled = true;
  try {
    const counts = Object.fromEntries(Object.keys(S.MAP.types).map(k => [k, +$('wi_' + k).value]));
    const lay = $('wiLayout').value, active = (S.layouts || []).find(l => l.active);
    const body = { robot_counts: counts, load_factor: +$('wiLoad').value, charging_bays: +$('wiBays').value, runs: +$('wiRuns').value, shift_hours: +$('wiHours').value, layout: active && lay !== active.file ? lay : null };
    const { job } = await api.post('/api/experiments/whatif', body);
    await poll(job, 'wiBar', 'wiStatus', renderWhatIf);
  } catch (e) { $('wiStatus').textContent = e.message; }
  b.disabled = false;
}
let lastWI = null;
function renderWhatIf(r) {
  lastWI = r;
  const A = r.summary.baseline, B = r.summary.scenario, d = r.delta_pct, m = r.meta, ch = m.changes || {};
  const counts = Object.entries(ch.robot_counts || {}).map(([k, v]) => `${(S.MAP.types[k] || { label: k }).label.split(' (')[0]} ${v}`).join(', ');
  $('wiMeta').textContent = `${m.runs} × ${m.shift_hours} h per side · volume ×${m.load_factor} · ${counts}${ch.charging_bays ? ' · ' + ch.charging_bays + ' chargers' : ''} · ${m.layout}`;
  $('wiTable').innerHTML = `<tr><th>KPI</th><th>Current setup</th><th>What-if</th><th>Change</th></tr>` +
    KPI_ROWS.filter(([k]) => k !== 'operator_interventions').map(([k, label, unit]) => `<tr><td>${label}${unit ? ` <small>(${unit})</small>` : ''}</td><td>${fmt(A[k].mean)}</td><td>${fmt(B[k].mean)}</td>${deltaCell(k, d[k])}</tr>`).join('');
  $('wiBar').style.width = '100%';
  renderROI();
}
function renderROI() {
  const r = lastWI, out = $('roiOut'); if (!r || !$('roi_cur')) return;
  const cur = $('roi_cur').value, ch = (r.meta.changes || {}).robot_counts || {};
  const base = Object.fromEntries(Object.entries(S.MAP.types).map(([k, t]) => [k, t.count]));
  let capex = 0; for (const [k, v] of Object.entries(ch)) capex += Math.max(0, v - (base[k] ?? v)) * (+($('cost_' + k) || { value: 0 }).value);
  const A = r.summary.baseline, B = r.summary.scenario, shifts = +$('roi_shifts').value;
  const extraOrders = (B.completed_in_shift.mean - A.completed_in_shift.mean) * shifts;
  const hoursSaved = (A.traffic_delay_min.mean - B.traffic_delay_min.mean) / 60 * shifts;
  const value = extraOrders * +$('roi_val').value + hoursSaved * +$('roi_hr').value;
  const money = v => cur + ' ' + Math.round(v).toLocaleString();
  const payback = capex > 0 && value > 0 ? (capex / (value / 12)) : null;
  out.innerHTML = `<div class="kv"><b>Additional robot investment</b><span>${money(capex)}</span></div>
    <div class="kv"><b>Extra orders per year</b><span>${Math.round(extraOrders).toLocaleString()}</span></div>
    <div class="kv"><b>Robot hours freed from traffic per year</b><span>${Math.round(hoursSaved).toLocaleString()} h</span></div>
    <div class="kv"><b>Annual value</b><span class="${value >= 0 ? 'good' : 'badv'}">${money(value)}</span></div>
    <div class="kv"><b>Payback</b><span>${capex === 0 ? 'no investment needed' : payback ? payback.toFixed(1) + ' months' : 'no payback at this volume'}</span></div>
    <p class="note" style="margin-top:10px;font-size:11.5px">Cycle time ${r.delta_pct.avg_cycle > 0 ? '+' : ''}${r.delta_pct.avg_cycle}% · wait ${r.delta_pct.avg_wait > 0 ? '+' : ''}${r.delta_pct.avg_wait}%. Illustrative: replace the unit costs and values with your own figures.</p>`;
}

// ------------------------------------------------------------------ integration
async function renderIntegration() {
  const [spec, sys] = await Promise.all([api.get('/openapi.json'), api.get('/api/system')]);
  const rows = [];
  for (const [path, ops] of Object.entries(spec.paths)) for (const [m, op] of Object.entries(ops)) rows.push({ m: m.toUpperCase(), path, s: op.summary || '', tag: (op.tags || [''])[0] });
  const tags = [...new Set(rows.map(r => r.tag))];
  $('apiList').innerHTML = tags.map(t => `<div class="grp">${t}</div>` + rows.filter(r => r.tag === t).map(r => `<div class="endpoint"><span class="m ${r.m}">${r.m}</span><span class="mono">${r.path}</span><span class="hint" style="margin-left:auto">${r.s}</span></div>`).join('')).join('');
  const v = (S.SNAP && S.SNAP.vendors) || [];
  $('conn').innerHTML = `<div class="kv"><b>Version</b><span>TusCore ${sys.version}</span></div>
    <div class="kv"><b>MQTT broker</b><span>${sys.mqtt.label}</span></div>
    <div class="kv"><b>Live mode</b><span>${sys.mode === 'tuscore' ? 'One TusCore manager' : 'Separate managers'}</span></div>
    <div class="kv"><b>Layout</b><span>${sys.site} (${sys.layout})</span></div>
    <div class="kv"><b>Experiment workers</b><span>${sys.experiment_workers} CPU cores</span></div>` +
    v.map(x => `<div class="kv"><b>${x.vendor}</b><span class="mono">${x.protocol} · ${x.online ? 'online' : 'offline'}</span></div>`).join('');
}
