// TusCore Layout Designer: draw a warehouse, check it, save versions, run a shift on it.
// Units are metres; everything snaps to the 1 m simulation grid, so what you draw is exactly what robots get.
import { $, api, toast } from './app.js';

const NS = 'http://www.w3.org/2000/svg';
const SOLID = new Set(['rack', 'wall', 'nogo']);
const TOOL_ORDER = ['rack', 'wall', 'packing', 'charging', 'pallet_area', 'dock', 'nogo'];
const ORDER_NAMES = { box_pick: 'Box picks', high_pick: 'High-shelf picks', pallet_move: 'Pallet moves', inventory_scan: 'Inventory scans' };
const DRAFT_KEY = 'tuscore.designer.draft';
const SHORT = { packing: 'Packing', charging: 'Charging', pallet_area: 'Pallets', dock: 'Dock' };

const D = {
  cat: null, L: null, did: null, version: null, template: false, dirty: false,
  sel: new Set(), tool: 'select', undo: [], redo: [], view: { s: 12, ox: 40, oy: 40 },
  rep: null, checkTimer: null, drag: null, inited: false, hover: null, nextId: 1,
  measure: null, bgImg: null,                          // scale tool points; the plan <image> node (kept, never reloaded)
};
const PLAN_TOOLS = { scale: 'measure', plan: 'plan' };

// ------------------------------------------------------------------ public
export async function showDesigner() {
  if (!D.inited) { D.inited = true; await init(); }
  resize();
}

async function init() {
  D.cat = await api.get('/api/catalog');
  buildTools(); bindTop(); bindCanvas(); bindKeys(); bindPlan(); bindRobotDialog();
  window.addEventListener('resize', resize);
  window.addEventListener('beforeunload', e => { if (D.dirty) { e.preventDefault(); e.returnValue = ''; } });
  await refreshOpen();
  const draft = loadDraft();
  if (draft) { setLayout(draft.layout, draft.did, draft.version, draft.template); markDirty(true); toast('Restored your unsaved layout', true); }
  else await openDesign('demo-warehouse');
}

// ------------------------------------------------------------------ model helpers
const els = () => D.L.elements;
const byId = id => els().find(e => e.id === id);
const newId = () => 'e' + Date.now().toString(36) + (D.nextId++);
const snap = v => Math.round(v);
const clampX = v => Math.max(0, Math.min(D.L.width_m, v)), clampY = v => Math.max(0, Math.min(D.L.height_m, v));   // keep drawing on the floor
function names() {                                    // same naming as the back end: racks A1.. top to bottom
  const out = {}, cnt = {};
  els().filter(e => e.type === 'rack').sort((a, b) => a.y - b.y || a.x - b.x).forEach((e, i) => { out[e.id] = e.name || 'A' + (i + 1); });
  els().filter(e => !SOLID.has(e.type)).sort((a, b) => a.y - b.y || a.x - b.x).forEach(e => {
    cnt[e.type] = (cnt[e.type] || 0) + 1; out[e.id] = `${SHORT[e.type] || D.cat.elements[e.type].label} ${cnt[e.type]}`;
  });
  return out;
}
function setLayout(L, did, version, template) {
  D.L = JSON.parse(JSON.stringify(L));
  D.L.elements = D.L.elements || []; D.L.robots = D.L.robots || {}; D.L.orders = D.L.orders || {};
  for (const [k, r] of Object.entries(D.cat.robots)) if (D.L.robots[k] == null) D.L.robots[k] = r.default_count;
  for (const [k, v] of Object.entries(D.cat.orders)) if (D.L.orders[k] == null) D.L.orders[k] = v;
  D.did = did; D.version = version; D.template = !!template;
  D.sel.clear(); D.undo = []; D.redo = []; markDirty(false);
  $('dzName').value = D.L.name || '';
  D.L.custom_robots = D.L.custom_robots || {};
  D.measure = null; $('dzScaleBox').hidden = true; if (PLAN_TOOLS[D.tool]) D.tool = 'select';
  buildRobots(); buildPlan(); fit(); render(); props(); check(); refreshVersions();
}
function snapshot() { return JSON.stringify(D.L); }
function change(fn) {                                 // every edit goes through here: undo + dirty + re-check
  D.undo.push(snapshot()); if (D.undo.length > 150) D.undo.shift(); D.redo = [];
  fn(); markDirty(true); render(); props(); check();
}
function restore(json) { D.L = JSON.parse(json); D.sel = new Set([...D.sel].filter(byId)); markDirty(true); buildRobots(); buildPlan(); render(); props(); check(); }
function undo() { if (!D.undo.length) return; D.redo.push(snapshot()); restore(D.undo.pop()); }
function redo() { if (!D.redo.length) return; D.undo.push(snapshot()); restore(D.redo.pop()); }
function markDirty(on) {
  D.dirty = on; $('dzDirty').classList.toggle('on', on);
  if (on) saveDraft(); else clearDraft();
}
function saveDraft() { try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ layout: D.L, did: D.did, version: D.version, template: D.template })); } catch (e) { /* private mode */ } }
function loadDraft() { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) { return null; } }
function clearDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ } }

// ------------------------------------------------------------------ open / save / run
async function refreshOpen() {
  const list = await api.get('/api/designs');
  const opt = d => `<option value="${d.id}">${esc(d.name)}${d.template ? '' : ` · v${d.versions}`}</option>`;
  $('dzOpen').innerHTML = '<option value="">Open…</option>'
    + `<optgroup label="Examples">${list.filter(d => d.template).map(opt).join('')}</optgroup>`
    + (list.some(d => !d.template) ? `<optgroup label="My layouts">${list.filter(d => !d.template).map(opt).join('')}</optgroup>` : '');
}
async function refreshVersions() {
  const sel = $('dzVersions');
  if (!D.did || D.template) { sel.innerHTML = `<option>${D.template ? 'Example (save to keep your copy)' : 'Not saved yet'}</option>`; sel.disabled = true; return; }
  const vs = await api.get(`/api/designs/${D.did}/versions`);
  sel.disabled = false;
  sel.innerHTML = vs.slice().reverse().map(v => `<option value="${v.version}" ${v.version === D.version ? 'selected' : ''}>v${v.version} · ${new Date(v.saved * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}${v.note ? ' · ' + esc(v.note) : ''}</option>`).join('');
}
async function openDesign(id, version) {
  const d = await api.get(`/api/designs/${id}` + (version ? `?version=${version}` : ''));
  setLayout(d.layout, d.id, d.version, d.template);
}
function confirmDiscard() { return !D.dirty || confirm('You have unsaved changes. Discard them?'); }
async function save(note = '') {
  D.L.name = $('dzName').value.trim() || 'Untitled layout';
  const body = { layout: D.L, note };
  const r = (!D.did || D.template) ? await api.post('/api/designs', body) : await put(`/api/designs/${D.did}`, body);
  D.did = r.id; D.version = r.version; D.template = false; markDirty(false);
  await refreshOpen(); await refreshVersions();
  toast(`Saved ${D.L.name} · version ${r.version}`, true);
  return r;
}
async function put(u, b) {
  const r = await fetch(u, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
  if (!r.ok) { toast('Not saved: ' + r.statusText); throw new Error(r.statusText); }
  return r.json();
}
async function run() {
  if (D.rep && !D.rep.ok) { toast(`Fix the ${D.rep.errors} error${D.rep.errors > 1 ? 's' : ''} in Problems first.`); return; }
  if (!confirm('Replace the running shift with this layout?')) return;
  if (D.dirty || !D.did || D.template) await save('run');
  const r = await api.post(`/api/designs/${D.did}/apply`, { version: D.version });
  window.dispatchEvent(new CustomEvent('tuscore:layout'));
  toast(`Shift started on ${r.site}${r.warnings ? ` · ${r.warnings} warning${r.warnings > 1 ? 's' : ''}` : ''}`, true);
  location.hash = 'ops';
}

function bindTop() {
  $('dzName').addEventListener('change', () => change(() => { D.L.name = $('dzName').value.trim() || 'Untitled layout'; }));
  $('dzOpen').onchange = async e => {
    const id = e.target.value; e.target.value = '';
    if (id && confirmDiscard()) await openDesign(id);
  };
  $('dzVersions').onchange = async e => { if (confirmDiscard()) await openDesign(D.did, +e.target.value); else e.target.value = D.version; };
  $('dzNew').onclick = () => {
    if (!confirmDiscard()) return;
    setLayout({ name: 'New layout', width_m: 60, height_m: 40, elements: [] }, null, null, false);
    markDirty(true);
  };
  $('dzUndo').onclick = undo; $('dzRedo').onclick = redo;
  $('dzFit').onclick = () => { fit(); render(); };
  $('dzSave').onclick = () => save();
  $('dzRun').onclick = run;
}

// ------------------------------------------------------------------ left panel
function buildTools() {
  const t = D.cat.elements;
  $('dzTools').innerHTML = `<button class="dz-tool wide on" data-t="select" title="Select, move and resize (V)"><i style="background:#93a8cc"></i>Select / move</button>`
    + TOOL_ORDER.map(k => `<button class="dz-tool" data-t="${k}" title="${esc(t[k].hint)}"><i style="background:${t[k].color}"></i>${t[k].label}</button>`).join('');
  $('dzTools').onclick = e => { const b = e.target.closest('[data-t]'); if (b) setTool(b.dataset.t); };
}
function setTool(t) {
  if (t !== 'scale') { D.measure = null; $('dzScaleBox').hidden = true; }
  D.tool = t;
  document.querySelectorAll('.dz-tool').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  const svg = $('dzSvg');
  svg.classList.toggle('draw', !!D.cat.elements[t]); svg.classList.toggle('measure', t === 'scale'); svg.classList.toggle('plan', t === 'plan');
  $('dzStatus').textContent = toolHint();
  if (D.L) { buildPlan(); render(); }
}
function toolHint() {
  if (D.tool === 'scale') return D.measure && D.measure.a ? 'Now click the second point (Shift = straight line) · Esc to cancel'
    : 'Click the first point of a distance you know on the plan, e.g. one end of a dimensioned wall';
  if (D.tool === 'plan') return 'Drag to move the floor plan under your drawing · arrows nudge 10 cm (Shift 1 m) · Esc when done';
  if (D.cat.elements[D.tool]) return `Drag on the floor to draw a ${D.cat.elements[D.tool].label.toLowerCase()} · Shift keeps the tool · Esc to stop`;
  return '';
}
function allRobots() {                               // built-in catalog + this layout's own robot types
  const out = { ...D.cat.robots };
  for (const [k, r] of Object.entries(D.L.custom_robots || {})) out[k] = { ...r, custom: true };
  return out;
}
function buildRobots() {
  const R = allRobots();
  $('dzRobots').innerHTML = Object.entries(R).map(([k, r]) => `
    <div class="dz-robot"><b>${r.custom ? `<span class="dot" style="background:${esc(r.color)}"></span>` : ''}${esc(r.label)}${r.custom ? '<span class="tag">custom</span>' : ''}</b>
      <span class="dz-step"><button data-r="${k}" data-d="-1">−</button><input data-r="${k}" type="number" min="0" max="200" value="${D.L.robots[k] || 0}"><button data-r="${k}" data-d="1">+</button></span>
      <small>${r.width_m} × ${r.length_m} m · ${r.custom ? `${esc(D.cat.roles[r.role].label.toLowerCase())} · ${r.speed_mps} m/s · €${Math.round(r.cost_eur / 1000)}k`
        : esc(r.does)}${r.custom ? ` <button class="ed" data-edit="${k}" title="Edit">Edit</button><button class="ed" data-del="${k}" title="Remove from this layout">Remove</button>` : ''}</small></div>`).join('');
  $('dzRobots').onclick = e => {
    const ed = e.target.closest('[data-edit]'); if (ed) { openRobotDlg(ed.dataset.edit); return; }
    const del = e.target.closest('[data-del]');
    if (del) {
      const k = del.dataset.del, name = D.L.custom_robots[k].label;
      change(() => { delete D.L.custom_robots[k]; delete D.L.robots[k]; }); buildRobots();
      toast(`Removed ${name} from this layout (Ctrl+Z to undo)`); return;
    }
    const b = e.target.closest('button[data-r]'); if (!b) return;
    change(() => { D.L.robots[b.dataset.r] = Math.max(0, Math.min(200, (D.L.robots[b.dataset.r] || 0) + +b.dataset.d)); });
    buildRobots();
  };
  $('dzRobots').onchange = e => { const i = e.target.closest('input[data-r]'); if (i) change(() => { D.L.robots[i.dataset.r] = Math.max(0, Math.min(200, Math.round(+i.value || 0))); }); };
  const lib = Object.entries(D.cat.library || {}).filter(([k]) => !(k in (D.L.custom_robots || {})));
  $('dzLib').hidden = !lib.length;
  $('dzLib').innerHTML = '<option value="">Add a saved robot type…</option>' + lib.map(([k, r]) => `<option value="${k}">${esc(r.label)} · ${esc(r.vendor)}</option>`).join('');
  $('dzOrders').innerHTML = Object.keys(D.cat.orders).map(k => `<span>${ORDER_NAMES[k] || k}</span><input data-o="${k}" type="number" min="0" max="5000" value="${D.L.orders[k] ?? 0}">`).join('');
  $('dzOrders').onchange = e => { const i = e.target.closest('input[data-o]'); if (i) change(() => { D.L.orders[i.dataset.o] = Math.max(0, +i.value || 0); }); };
}

// ------------------------------------------------------------------ custom robot types
const ROBOT_NUM = ['width_m', 'length_m', 'speed_mps', 'battery_kwh', 'cost_eur'];
const aisleNeed = w => Math.max(1, Math.ceil(w + 2 * D.cat.safety_m - 1e-9));
function roleDefaults(role) {
  const r = D.cat.robots[D.cat.roles[role].model];
  return { width_m: r.width_m, length_m: r.length_m, speed_mps: r.speed_mps, battery_kwh: r.battery_kwh, cost_eur: r.cost_eur };
}
function openRobotDlg(key = null) {
  const f = $('dzRobotForm'), dlg = $('dzRobotDlg'), cur = key ? D.L.custom_robots[key] : null;
  D.editRobot = key;
  $('dzRobotTitle').textContent = cur ? `Edit ${cur.label}` : 'New robot type';
  $('dzRobotOk').textContent = cur ? 'Save changes' : 'Add robot type';
  const v = cur || { label: '', role: 'carry_tote', vendor: '', adapter: 'vda5050', color: '#a78bfa', ...roleDefaults('carry_tote') };
  f.label.value = v.label; f.role.value = v.role; f.vendor.value = v.vendor === 'Custom' ? '' : v.vendor; f.adapter.value = v.adapter;
  ROBOT_NUM.forEach(n => { f[n].value = v[n]; }); f.color.value = v.color || '#a78bfa';
  robotNeed(); dlg.showModal(); f.label.focus();
}
function robotNeed() {
  const f = $('dzRobotForm'), w = +f.width_m.value || 0, role = f.role.value;
  $('dzRobotNeed').textContent = w > 0 ? `Needs aisles at least ${aisleNeed(w)} m wide (${w} m + ${D.cat.safety_m} m each side).`
    + (role === 'scan' ? ' Flies over racks, like the drone.' : role === 'high_pick' ? ' Hands its totes to a tote carrier, like the TL-65.' : '') : '';
}
function uniqueKey(label) {
  const base = 'c_' + ((label || 'robot').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 24) || 'robot');
  let k = base, i = 2;
  while (k in (D.L.custom_robots || {}) || k in D.cat.robots) k = `${base.slice(0, 27)}_${i++}`;
  return k;
}
function bindRobotDialog() {
  const f = $('dzRobotForm'), dlg = $('dzRobotDlg');
  f.role.innerHTML = Object.entries(D.cat.roles).map(([k, r]) => `<option value="${k}">${esc(r.label)}: ${esc(r.does.toLowerCase())}</option>`).join('');
  f.adapter.innerHTML = Object.entries(D.cat.adapters).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join('');
  f.role.onchange = () => { if (!D.editRobot) { const d = roleDefaults(f.role.value); ROBOT_NUM.forEach(n => { f[n].value = d[n]; }); } robotNeed(); };
  f.width_m.oninput = robotNeed;
  $('dzNewRobot').onclick = () => openRobotDlg();
  $('dzLib').onchange = e => {
    const k = e.target.value; e.target.value = ''; if (!k) return;
    const r = D.cat.library[k];
    change(() => { D.L.custom_robots[k] = { ...r }; D.L.robots[k] = D.L.robots[k] || 1; });
    buildRobots(); toast(`Added ${r.label}`, true);
  };
  dlg.addEventListener('close', async () => {
    if (dlg.returnValue !== 'ok') return;
    const lim = D.cat.limits, num = n => Math.max(lim[n][0], Math.min(lim[n][1], +f[n].value || lim[n][0]));
    const role = f.role.value, d = {
      label: f.label.value.trim().slice(0, 40) || D.cat.roles[role].label, role, vendor: f.vendor.value.trim().slice(0, 40) || 'Custom',
      adapter: f.adapter.value, interface: D.cat.adapters[f.adapter.value], color: f.color.value,
      flying: role === 'scan', model: D.cat.roles[role].model, does: D.cat.roles[role].does,
    };
    ROBOT_NUM.forEach(n => { d[n] = n === 'cost_eur' ? Math.round(num(n)) : Math.round(num(n) * 100) / 100; });
    const key = D.editRobot || uniqueKey(d.label);
    change(() => { D.L.custom_robots[key] = d; if (D.L.robots[key] == null) D.L.robots[key] = 1; });
    buildRobots();
    toast(`${D.editRobot ? 'Updated' : 'Added'} ${d.label}`, true);
    if (f.lib.checked) {
      try {
        const r = await fetch(`/api/robot-types/${key}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) });
        if (r.ok) { D.cat.library = await api.get('/api/robot-types'); buildRobots(); }
      } catch (e) { /* library is optional; the layout keeps its own copy */ }
    }
  });
}

// ------------------------------------------------------------------ floor plan (background image)
const r2 = v => Math.round(v * 100) / 100;
const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
const axisLock = (a, w) => Math.abs(w.x - a.x) >= Math.abs(w.y - a.y) ? { x: w.x, y: a.y } : { x: a.x, y: w.y };
const bgSize = b => ({ w: b.w_px * b.m_per_px, h: b.h_px * b.m_per_px });
const bgCenter = b => { const z = bgSize(b); return { x: b.x + z.w / 2, y: b.y + z.h / 2 }; };
function rotPt(p, c, deg) {                           // same rotation as SVG rotate(deg cx cy), y pointing down
  const a = deg * Math.PI / 180, cs = Math.cos(a), sn = Math.sin(a), dx = p.x - c.x, dy = p.y - c.y;
  return { x: c.x + dx * cs - dy * sn, y: c.y + dx * sn + dy * cs };
}
const toImg = (b, p) => { const q = rotPt(p, bgCenter(b), -b.rot); return { u: (q.x - b.x) / b.m_per_px, v: (q.y - b.y) / b.m_per_px }; };
function placeKeeping(b, uv, world, m) {              // new scale m, image pixel uv stays on the world point
  const o = rotPt({ x: (uv.u - b.w_px / 2) * m, y: (uv.v - b.h_px / 2) * m }, { x: 0, y: 0 }, b.rot);
  b.m_per_px = m; b.x = world.x - o.x - b.w_px * m / 2; b.y = world.y - o.y - b.h_px * m / 2;
}
function bgBox(b) {
  const z = bgSize(b), c = bgCenter(b);
  const P = [[b.x, b.y], [b.x + z.w, b.y], [b.x, b.y + z.h], [b.x + z.w, b.y + z.h]].map(([x, y]) => rotPt({ x, y }, c, b.rot));
  return { x0: Math.min(...P.map(p => p.x)), y0: Math.min(...P.map(p => p.y)), x1: Math.max(...P.map(p => p.x)), y1: Math.max(...P.map(p => p.y)) };
}
function renderPlan(g, s) {
  const b = D.L.background; if (!b || !b.show) return;
  const z = bgSize(b), c = bgCenter(b);
  if (!D.bgImg) D.bgImg = document.createElementNS(NS, 'image');
  const img = D.bgImg, href = `/api/backgrounds/${b.id}`;
  if (img.getAttribute('href') !== href) img.setAttribute('href', href);
  for (const [k, v] of Object.entries({ x: b.x, y: b.y, width: z.w, height: z.h, preserveAspectRatio: 'none', opacity: b.opacity,
    transform: `rotate(${b.rot} ${c.x} ${c.y})`, 'pointer-events': 'none' })) img.setAttribute(k, v);
  img.setAttribute('style', b.invert ? 'filter:invert(1) hue-rotate(180deg)' : '');
  g.appendChild(img);
  if (D.tool === 'plan') {
    el('rect', { x: b.x, y: b.y, width: z.w, height: z.h, fill: 'none', stroke: '#f5a524', 'stroke-width': 2 / s, 'stroke-dasharray': `${6 / s} ${4 / s}`,
      transform: `rotate(${b.rot} ${c.x} ${c.y})`, 'pointer-events': 'none' }, g);
  }
}
function renderMeasure(g, s) {
  const m = D.measure; if (!m || !m.a) return;
  const e = m.b || m.cur; if (!e) { el('circle', { cx: m.a.x, cy: m.a.y, r: 5 / s, fill: '#f5a524' }, g); return; }
  el('line', { x1: m.a.x, y1: m.a.y, x2: e.x, y2: e.y, stroke: '#041225', 'stroke-width': 5 / s, 'stroke-linecap': 'round' }, g);
  el('line', { x1: m.a.x, y1: m.a.y, x2: e.x, y2: e.y, stroke: '#f5a524', 'stroke-width': 2.5 / s, 'stroke-linecap': 'round' }, g);
  for (const p of [m.a, e]) el('circle', { cx: p.x, cy: p.y, r: 5 / s, fill: '#f5a524', stroke: '#041225', 'stroke-width': 1.5 / s }, g);
  el('text', { x: (m.a.x + e.x) / 2, y: (m.a.y + e.y) / 2 - 9 / s, 'font-size': 12 / s, fill: '#f5a524', 'text-anchor': 'middle', 'font-weight': 700,
    stroke: '#041225', 'stroke-width': 3 / s, 'paint-order': 'stroke' }, g).textContent = `${dist(m.a, e).toFixed(2)} m`;
}
function measureClick(w, shift) {
  const b = D.L.background;
  if (b && b.locked) { toast('The floor plan is locked. Untick Lock to change its scale.'); return; }
  if (!D.measure || !D.measure.a || D.measure.b) { D.measure = { a: w, b: null, cur: null }; $('dzScaleBox').hidden = true; }
  else {
    const p = shift ? axisLock(D.measure.a, w) : w;
    if (dist(D.measure.a, p) < 0.05) return;
    D.measure.b = p;
    $('dzScaleNow').textContent = `On the plan this line is ${dist(D.measure.a, p).toFixed(2)} m now. Type its real length.`;
    $('dzScaleBox').hidden = false; $('dzScaleM').value = ''; setTimeout(() => $('dzScaleM').focus(), 0);
  }
  $('dzStatus').textContent = toolHint(); render();
}
function applyScale() {
  const m = D.measure, b = D.L.background, real = +$('dzScaleM').value;
  if (!m || !m.b || !b) return;
  if (!(real > 0)) { toast('Type the real distance in metres.'); return; }
  const px = dist(m.a, m.b) / b.m_per_px, mpp = real / px;
  if (Math.max(b.w_px, b.h_px) * mpp > 2000) { toast(`That makes the plan ${Math.round(b.w_px * mpp)} m wide. Check the distance.`); return; }
  const uv = toImg(b, m.a), a = m.a;
  change(() => { placeKeeping(D.L.background, uv, a, mpp); D.L.background.scaled = true; });
  setTool('select');
  const z = bgSize(D.L.background);
  toast(`Scale set: the plan is ${z.w.toFixed(1)} × ${z.h.toFixed(1)} m`, true);
  if (!els().length) fitFloorToPlan(true); else buildPlan();
}
function fitFloorToPlan(quiet = false) {
  const b = D.L.background; if (!b) return;
  const bx = bgBox(b), dx = -Math.floor(bx.x0), dy = -Math.floor(bx.y0);
  const W = Math.max(10, Math.ceil(bx.x1 + dx - 1e-6)), H = Math.max(10, Math.ceil(bx.y1 + dy - 1e-6));
  if (W > 500 || H > 500) { toast(`The plan is ${Math.round(bx.x1 - bx.x0)} × ${Math.round(bx.y1 - bx.y0)} m, over the 500 m limit. Set the scale first.`); return; }
  change(() => {
    D.L.background.x = r2(D.L.background.x + dx); D.L.background.y = r2(D.L.background.y + dy);
    for (const e of els()) { e.x += dx; e.y += dy; }                  // the drawing moves with the plan
    D.L.width_m = W; D.L.height_m = H;
  });
  fit(); render(); buildPlan();
  if (!quiet) toast(`Floor is now ${W} × ${H} m, the size of the plan`, true);
  else toast(`Floor set to ${W} × ${H} m from the plan. Now trace it: draw racks, walls and stations on top.`, true);
}
async function uploadPlan(file, page = 1) {
  if (!file) return;
  if (file.size > 40 * 2 ** 20) { toast('The file is over 40 MB. Export a smaller PDF or image.'); return; }
  $('dzPlan').innerHTML = `<div class="dz-plan"><div class="meta">Loading ${esc(file.name)}…</div></div>`;
  try {
    const r = await fetch(`/api/backgrounds?name=${encodeURIComponent(file.name)}&page=${page}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file });
    const j = await r.json(); if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText);
    const m = Math.min(D.L.width_m / j.w_px, D.L.height_m / j.h_px);      // first guess: fit on the floor; the scale tool fixes it
    change(() => { D.L.background = { ...j, x: 0, y: 0, m_per_px: m, rot: 0, opacity: 0.6, show: true, locked: false, invert: true, scaled: false }; });
    setTool('scale');
    toast(`Plan loaded${j.pages > 1 ? ` (page ${j.page} of ${j.pages})` : ''}. Now set the scale: click two points you know the distance between.`, true);
  } catch (e) { toast('Could not load the plan: ' + e.message); }
  buildPlan();
}
async function setPlanPage(page) {
  const b = D.L.background;
  try {
    const j = await api.post(`/api/backgrounds/pdf/${b.source}/page/${page}?name=${encodeURIComponent(b.name)}`);
    change(() => Object.assign(D.L.background, { id: j.id, w_px: j.w_px, h_px: j.h_px, page: j.page, pages: j.pages, paper_mm: j.paper_mm }));
  } catch (e) { /* toast shown by api.post */ }
  buildPlan();
}
function buildPlan() {
  const box = $('dzPlan'), b = D.L && D.L.background;
  if (!b) {
    box.innerHTML = `<button class="dz-upload" id="pUp">Upload floor plan<small>PDF, PNG or JPG · or drop it on the floor</small></button>`;
    $('pUp').onclick = () => $('dzFile').click(); return;
  }
  const z = bgSize(b);
  box.innerHTML = `<div class="dz-plan">
    <div class="meta"><b>${esc(b.name || 'Floor plan')}</b><br>${b.scaled ? `<span class="good">Scale set</span> · plan ${z.w.toFixed(1)} × ${z.h.toFixed(1)} m · 1 px = ${(b.m_per_px * 100).toFixed(1)} cm`
      : '<span class="warn">Scale not set yet.</span> Click Set scale, then two points of a known distance.'}</div>
    ${b.pages > 1 ? `<div class="row">Page <select id="pPage">${Array.from({ length: Math.min(b.pages, 200) }, (_, i) => `<option ${i + 1 === b.page ? 'selected' : ''}>${i + 1}</option>`).join('')}</select> of ${b.pages}</div>` : ''}
    <div class="btns"><button class="btn sm ${D.tool === 'scale' ? 'on' : ''}" id="pScale" title="Click two points you know the real distance between">Set scale</button>
      <button class="btn sm ${D.tool === 'plan' ? 'on' : ''}" id="pMove" ${b.locked ? 'disabled' : ''} title="Drag the plan so it lines up with the floor">Move plan</button>
      <button class="btn sm" id="pFloor" title="Make the floor exactly the size of the plan">Floor = plan</button>
      <button class="btn sm" id="pTurn" ${b.locked ? 'disabled' : ''} title="Turn the plan 90°">Turn 90°</button></div>
    <div class="row">Fade <input type="range" id="pOp" min="0.1" max="1" step="0.05" value="${b.opacity}"></div>
    <div class="row">Angle <input type="number" id="pRot" step="0.5" value="${b.rot}" ${b.locked ? 'disabled' : ''}> °</div>
    <div class="checks"><label class="dz-check"><input type="checkbox" id="pShow" ${b.show ? 'checked' : ''}>Show</label>
      <label class="dz-check" title="Stop the plan moving by accident"><input type="checkbox" id="pLock" ${b.locked ? 'checked' : ''}>Lock</label>
      <label class="dz-check" title="Show a white paper plan as dark lines on light"><input type="checkbox" id="pInv" ${b.invert ? 'checked' : ''}>Dark</label></div>
    <div class="btns"><button class="btn sm" id="pReplace">Replace</button><button class="btn sm danger" id="pRemove">Remove</button></div></div>`;
  $('pScale').onclick = () => setTool(D.tool === 'scale' ? 'select' : 'scale');
  $('pMove').onclick = () => setTool(D.tool === 'plan' ? 'select' : 'plan');
  $('pFloor').onclick = () => fitFloorToPlan();
  $('pTurn').onclick = () => change(() => { D.L.background.rot = ((D.L.background.rot + 90 + 180) % 360) - 180; });
  $('pRot').onchange = e => change(() => { D.L.background.rot = Math.max(-360, Math.min(360, +e.target.value || 0)); });
  $('pOp').oninput = e => { if (!D.opSnap) D.opSnap = snapshot(); D.L.background.opacity = +e.target.value; render(); };
  $('pOp').onchange = () => { if (D.opSnap) { D.undo.push(D.opSnap); D.redo = []; D.opSnap = null; } markDirty(true); };
  $('pShow').onchange = e => change(() => { D.L.background.show = e.target.checked; });
  $('pLock').onchange = e => { change(() => { D.L.background.locked = e.target.checked; }); if (e.target.checked && PLAN_TOOLS[D.tool]) setTool('select'); };
  $('pInv').onchange = e => change(() => { D.L.background.invert = e.target.checked; });
  $('pReplace').onclick = () => $('dzFile').click();
  $('pRemove').onclick = () => { if (PLAN_TOOLS[D.tool]) setTool('select'); change(() => { D.L.background = null; }); toast('Floor plan removed (Ctrl+Z to undo)'); };
  if ($('pPage')) $('pPage').onchange = e => setPlanPage(+e.target.value);
}
function bindPlan() {
  $('dzFile').onchange = e => { const f = e.target.files[0]; e.target.value = ''; uploadPlan(f); };
  const wrap = $('dzWrap'); let depth = 0;
  const has = ev => [...(ev.dataTransfer && ev.dataTransfer.types || [])].includes('Files');
  wrap.addEventListener('dragenter', ev => { if (!has(ev)) return; ev.preventDefault(); depth++; wrap.classList.add('dragover'); });
  wrap.addEventListener('dragover', ev => { if (has(ev)) ev.preventDefault(); });
  wrap.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; wrap.classList.remove('dragover'); } });
  wrap.addEventListener('drop', ev => {
    if (!has(ev)) return; ev.preventDefault(); depth = 0; wrap.classList.remove('dragover');
    uploadPlan(ev.dataTransfer.files[0]);
  });
  $('dzScaleOk').onclick = applyScale;
  $('dzScaleNo').onclick = () => setTool('select');
  $('dzScaleM').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); applyScale(); } if (e.key === 'Escape') setTool('select'); });
}

// ------------------------------------------------------------------ canvas: view
function resize() { if (D.L) render(); }
function fit() {
  const r = $('dzWrap').getBoundingClientRect(); if (!r.width || !D.L) return;
  const pad = 46, top = 40;
  const s = Math.min((r.width - 2 * pad) / D.L.width_m, (r.height - pad - top) / D.L.height_m);
  D.view = { s: Math.max(2, s), ox: (r.width - D.L.width_m * Math.max(2, s)) / 2, oy: top + (r.height - top - D.L.height_m * Math.max(2, s)) / 2 };
}
const toWorld = ev => { const r = $('dzSvg').getBoundingClientRect(); return { x: (ev.clientX - r.left - D.view.ox) / D.view.s, y: (ev.clientY - r.top - D.view.oy) / D.view.s }; };

// ------------------------------------------------------------------ canvas: render
function el(tag, attrs, parent) { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (parent) parent.appendChild(n); return n; }
function render() {
  const svg = $('dzSvg'); if (!D.L) return;
  svg.innerHTML = '';
  const { s, ox, oy } = D.view, W = D.L.width_m, H = D.L.height_m;
  const g = el('g', { transform: `translate(${ox},${oy}) scale(${s})` }, svg);
  // floor + grid (1 m minor, 5 m major)
  el('rect', { x: 0, y: 0, width: W, height: H, fill: '#081634', stroke: '#2b4c8f', 'stroke-width': 2 / s }, g);
  renderPlan(g, s);
  if (s >= 5) { const p = []; for (let x = 1; x < W; x++) if (x % 5) p.push(`M${x} 0V${H}`); for (let y = 1; y < H; y++) if (y % 5) p.push(`M0 ${y}H${W}`); el('path', { d: p.join(''), stroke: '#10264f', 'stroke-width': 1 / s }, g); }
  { const p = []; for (let x = 5; x < W; x += 5) p.push(`M${x} 0V${H}`); for (let y = 5; y < H; y += 5) p.push(`M0 ${y}H${W}`); el('path', { d: p.join(''), stroke: '#173467', 'stroke-width': 1 / s }, g); }
  // problems -> outline colour per element
  const level = {};
  for (const p of (D.rep && D.rep.problems) || []) for (const id of p.ids) if (p.level === 'error' || (p.level === 'warning' && level[id] !== 'error')) level[id] = p.level;
  const nm = names(), order = ['charging', 'packing', 'pallet_area', 'dock', 'nogo', 'rack', 'wall'];
  const sorted = els().slice().sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
  for (const e of sorted) {
    const t = D.cat.elements[e.type], gg = el('g', { class: 'dz-el', 'data-id': e.id }, g);
    const solid = SOLID.has(e.type);
    el('rect', { x: e.x, y: e.y, width: e.w, height: e.h, fill: t.color, 'fill-opacity': e.type === 'rack' ? 0.85 : e.type === 'wall' ? 1 : e.type === 'nogo' ? 0.28 : 0.42,
      stroke: e.type === 'nogo' ? t.color : solid ? '#0b1736' : t.color, 'stroke-width': (e.type === 'nogo' ? 2 : 1) / s, 'stroke-dasharray': e.type === 'nogo' ? `${4 / s} ${3 / s}` : '' }, gg);
    if (e.type === 'rack') {                         // bay marks along the long side
      const p = [], horiz = e.w >= e.h;
      if (horiz) for (let x = e.x + 1; x < e.x + e.w; x++) p.push(`M${x} ${e.y}v${e.h}`); else for (let y = e.y + 1; y < e.y + e.h; y++) p.push(`M${e.x} ${y}h${e.w}`);
      el('path', { d: p.join(''), stroke: '#0b1736', 'stroke-width': 1 / s, opacity: .55 }, gg);
    }
    if (e.type === 'charging' && s > 4) for (let x = e.x + 1; x < e.x + e.w - 1; x += 2) el('rect', { x: x + 0.2, y: e.y + e.h - 0.35, width: 0.6, height: 0.2, fill: '#6cc4ff' }, gg);
    const lab = e.type === 'wall' ? '' : e.type === 'nogo' ? (e.name || 'NO-GO') : nm[e.id] || '';
    const vertLab = e.type === 'rack' && e.h > e.w, room = (vertLab ? e.h : e.w) * 0.9;
    const fs = Math.min(1.1, room / Math.max(1, lab.length * 0.62), (vertLab ? e.w : e.h) * 0.7);
    if (lab && fs * s >= 7) {
      const vert = vertLab;
      el('text', { x: e.x + e.w / 2, y: e.y + e.h / 2, 'font-size': fs, fill: e.type === 'rack' ? '#e6eefc' : '#d4e3fb', 'text-anchor': 'middle', 'dominant-baseline': 'central',
        transform: vert ? `rotate(-90 ${e.x + e.w / 2} ${e.y + e.h / 2})` : '' }, gg).textContent = lab;
    }
    if (level[e.id]) el('rect', { x: e.x - 0.15, y: e.y - 0.15, width: e.w + 0.3, height: e.h + 0.3, fill: 'none', stroke: level[e.id] === 'error' ? '#ff5d6c' : '#f5a524', 'stroke-width': 2 / s, 'stroke-dasharray': `${5 / s} ${3 / s}` }, gg);
  }
  // selection + resize handles
  for (const id of D.sel) {
    const e = byId(id); if (!e) continue;
    el('rect', { x: e.x, y: e.y, width: e.w, height: e.h, fill: 'none', stroke: '#6cc4ff', 'stroke-width': 2 / s, 'pointer-events': 'none' }, g);
    if (D.sel.size === 1) {
      const hs = 9 / s;
      for (const [hx, hy, c] of [[0, 0, 'nw'], [1, 0, 'ne'], [0, 1, 'sw'], [1, 1, 'se'], [.5, 0, 'n'], [.5, 1, 's'], [0, .5, 'w'], [1, .5, 'e']]) {
        el('rect', { class: 'dz-handle', 'data-h': c, x: e.x + e.w * hx - hs / 2, y: e.y + e.h * hy - hs / 2, width: hs, height: hs, 'stroke-width': 1.5 / s,
          style: `cursor:${c.length === 2 ? (c === 'nw' || c === 'se' ? 'nwse' : 'nesw') : (c === 'n' || c === 's' ? 'ns' : 'ew')}-resize` }, g);
      }
      el('text', { x: e.x + e.w / 2, y: e.y - 10 / s, 'font-size': 11 / s, fill: '#6cc4ff', 'text-anchor': 'middle', 'pointer-events': 'none' }, g).textContent = `${e.w} × ${e.h} m`;
    }
  }
  // rubber band while drawing
  if (D.drag && D.drag.kind === 'draw') {
    const r = D.drag.rect, t = D.cat.elements[D.drag.type];
    el('rect', { x: r.x, y: r.y, width: r.w, height: r.h, fill: t.color, 'fill-opacity': .35, stroke: t.color, 'stroke-width': 2 / s }, g);
    el('text', { x: r.x + r.w / 2, y: r.y - 10 / s, 'font-size': 11 / s, fill: '#6cc4ff', 'text-anchor': 'middle' }, g).textContent = `${r.w} × ${r.h} m`;
  }
  renderMeasure(g, s);
  // rulers: every 5 / 10 m
  const step = s < 6 ? 10 : 5;
  for (let x = 0; x <= W; x += step) el('text', { x: ox + x * s, y: oy - 6, 'font-size': 10, fill: '#5f769f', 'text-anchor': 'middle' }, svg).textContent = x;
  for (let y = 0; y <= H; y += step) el('text', { x: ox - 6, y: oy + y * s + 3, 'font-size': 10, fill: '#5f769f', 'text-anchor': 'end' }, svg).textContent = y;
  el('text', { x: ox + W * s, y: oy + H * s + 16, 'font-size': 10.5, fill: '#93a8cc', 'text-anchor': 'end' }, svg).textContent = `${W} × ${H} m · 1 square = 1 m`;
}

// ------------------------------------------------------------------ canvas: interaction
function hitElement(target) { const g = target.closest && target.closest('.dz-el'); return g ? byId(g.dataset.id) : null; }
function bindCanvas() {
  const svg = $('dzSvg');
  svg.addEventListener('pointerdown', ev => {
    if (ev.button === 1 || ev.button === 2) { startPan(ev); return; }
    svg.focus(); svg.setPointerCapture(ev.pointerId);
    const w = toWorld(ev), h = ev.target.dataset && ev.target.dataset.h;
    if (D.tool === 'scale') { measureClick(w, ev.shiftKey); return; }
    if (D.tool === 'plan') {
      const b = D.L.background; if (!b) return;
      if (b.locked) { toast('The floor plan is locked. Untick Lock to move it.'); return; }
      D.undo.push(snapshot()); D.drag = { kind: 'plan', w0: w, x: b.x, y: b.y, moved: false }; return;
    }
    if (h && D.sel.size === 1) { const e = byId([...D.sel][0]); D.undo.push(snapshot()); D.drag = { kind: 'resize', h, e, start: { ...e }, w0: w }; return; }
    if (D.tool !== 'select') { const x = clampX(snap(w.x)), y = clampY(snap(w.y)); D.drag = { kind: 'draw', type: D.tool, x0: x, y0: y, rect: { x, y, w: 1, h: 1 }, moved: false }; return; }
    const e = hitElement(ev.target);
    if (e) {
      if (ev.shiftKey) { D.sel.has(e.id) ? D.sel.delete(e.id) : D.sel.add(e.id); render(); props(); return; }
      if (!D.sel.has(e.id)) { D.sel = new Set([e.id]); props(); }
      D.undo.push(snapshot());
      D.drag = { kind: 'move', w0: w, start: [...D.sel].map(id => ({ ...byId(id) })), moved: false };
      render(); return;
    }
    if (D.sel.size) { D.sel.clear(); props(); render(); }
    startPan(ev);
  });
  svg.addEventListener('pointermove', ev => {
    const w = toWorld(ev);
    status(w);
    if (D.tool === 'scale' && D.measure && D.measure.a && !D.measure.b) { D.measure.cur = ev.shiftKey ? axisLock(D.measure.a, w) : w; render(); }
    const d = D.drag; if (!d) return;
    if (d.kind === 'plan') {
      const b = D.L.background; b.x = r2(d.x + w.x - d.w0.x); b.y = r2(d.y + w.y - d.w0.y); d.moved = true; render(); return;
    }
    if (d.kind === 'pan') { D.view.ox = d.ox + ev.clientX - d.cx; D.view.oy = d.oy + ev.clientY - d.cy; render(); return; }
    if (d.kind === 'draw') {
      const x = clampX(snap(w.x)), y = clampY(snap(w.y));
      d.rect = { x: Math.min(d.x0, x), y: Math.min(d.y0, y), w: Math.max(1, Math.abs(x - d.x0)), h: Math.max(1, Math.abs(y - d.y0)) };
      d.moved = d.moved || Math.abs(x - d.x0) + Math.abs(y - d.y0) > 0; render(); return;
    }
    if (d.kind === 'move') {
      let dx = snap(w.x - d.w0.x), dy = snap(w.y - d.w0.y);
      const bx0 = Math.min(...d.start.map(e => e.x)), by0 = Math.min(...d.start.map(e => e.y));
      const bx1 = Math.max(...d.start.map(e => e.x + e.w)), by1 = Math.max(...d.start.map(e => e.y + e.h));
      dx = Math.max(Math.min(dx, D.L.width_m - bx1), -bx0); dy = Math.max(Math.min(dy, D.L.height_m - by1), -by0);
      d.moved = d.moved || dx || dy;
      for (const s of d.start) { const e = byId(s.id); e.x = s.x + dx; e.y = s.y + dy; }
      render(); return;
    }
    if (d.kind === 'resize') {
      const s0 = d.start, e = d.e, x = clampX(snap(w.x)), y = clampY(snap(w.y));
      let x0 = s0.x, y0 = s0.y, x1 = s0.x + s0.w, y1 = s0.y + s0.h;
      if (d.h.includes('w')) x0 = Math.min(x, x1 - 1); if (d.h.includes('e')) x1 = Math.max(x, x0 + 1);
      if (d.h.includes('n')) y0 = Math.min(y, y1 - 1); if (d.h.includes('s')) y1 = Math.max(y, y0 + 1);
      Object.assign(e, { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }); d.moved = true; render();
    }
  });
  const end = () => {
    const d = D.drag; D.drag = null; svg.classList.remove('panning');
    if (!d) return;
    if (d.kind === 'draw') {
      const t = D.cat.elements[d.type];
      let r = d.moved ? d.rect : { x: d.x0 - Math.floor(t.default[0] / 2), y: d.y0 - Math.floor(t.default[1] / 2), w: t.default[0], h: t.default[1] };
      r = { ...r, w: Math.min(r.w, D.L.width_m), h: Math.min(r.h, D.L.height_m) };
      r.x = Math.max(0, Math.min(D.L.width_m - r.w, r.x)); r.y = Math.max(0, Math.min(D.L.height_m - r.h, r.y));
      const e = { id: newId(), type: d.type, ...r };
      if (d.type === 'rack') e.levels = 4;
      change(() => { els().push(e); D.sel = new Set([e.id]); });
      if (!eventShift) setTool('select');
      return;
    }
    if (d.kind === 'plan') { if (d.moved) { D.redo = []; markDirty(true); } else D.undo.pop(); buildPlan(); return; }
    if (d.kind === 'move' || d.kind === 'resize') {
      if (d.moved) { D.redo = []; markDirty(true); props(); check(); } else D.undo.pop();
      render();
    }
  };
  let eventShift = false;
  svg.addEventListener('pointerup', ev => { eventShift = ev.shiftKey; end(); });
  svg.addEventListener('pointercancel', end);
  svg.addEventListener('contextmenu', e => e.preventDefault());
  svg.addEventListener('dblclick', ev => { const e = hitElement(ev.target); if (e) { D.sel = new Set([e.id]); props(); const i = $('dzProps').querySelector('input'); if (i) i.focus(); } });
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const k = Math.exp(-ev.deltaY * 0.0015), s = Math.max(1.5, Math.min(80, D.view.s * k)), f = s / D.view.s;
    D.view = { s, ox: mx - (mx - D.view.ox) * f, oy: my - (my - D.view.oy) * f }; render();
  }, { passive: false });
  svg.addEventListener('pointerleave', () => { $('dzStatus').textContent = ''; });
}
function startPan(ev) { const svg = $('dzSvg'); svg.setPointerCapture(ev.pointerId); svg.classList.add('panning'); D.drag = { kind: 'pan', cx: ev.clientX, cy: ev.clientY, ox: D.view.ox, oy: D.view.oy }; }
function status(w) {
  const inside = w.x >= 0 && w.y >= 0 && w.x <= D.L.width_m && w.y <= D.L.height_m;
  if (D.tool === 'scale' || D.tool === 'plan') {
    const m = D.measure, live = m && m.a ? dist(m.a, m.b || m.cur || w) : null;
    $('dzStatus').textContent = (live != null ? `${live.toFixed(2)} m on the plan now · ` : '') + toolHint(); return;
  }
  $('dzStatus').textContent = inside ? `x ${w.x.toFixed(1)} m · y ${w.y.toFixed(1)} m` + (D.cat.elements[D.tool] ? ` · drawing ${D.cat.elements[D.tool].label.toLowerCase()} (Esc to stop)` : '') : '';
}
function bindKeys() {
  document.addEventListener('keydown', ev => {
    if (!document.getElementById('p-layout').classList.contains('on')) return;
    const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    const k = ev.key.toLowerCase(), ctrl = ev.ctrlKey || ev.metaKey;
    if (ctrl && k === 's') { ev.preventDefault(); save(); return; }
    if (typing) return;
    if (ctrl && k === 'z' && !ev.shiftKey) { ev.preventDefault(); undo(); return; }
    if (ctrl && (k === 'y' || (k === 'z' && ev.shiftKey))) { ev.preventDefault(); redo(); return; }
    if (ctrl && k === 'd') { ev.preventDefault(); duplicate(); return; }
    if (ctrl && k === 'a') { ev.preventDefault(); D.sel = new Set(els().map(e => e.id)); render(); props(); return; }
    if (k === 'escape') { setTool('select'); D.sel.clear(); render(); props(); return; }
    const pmv = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[k];
    if (D.tool === 'plan' && pmv && D.L.background && !D.L.background.locked) {
      ev.preventDefault(); const st = ev.shiftKey ? 1 : 0.1;
      change(() => { D.L.background.x = r2(D.L.background.x + pmv[0] * st); D.L.background.y = r2(D.L.background.y + pmv[1] * st); }); return;
    }
    if (k === 'delete' || k === 'backspace') { ev.preventDefault(); remove(); return; }
    if (k === 'r' && !ctrl) { rotate(); return; }
    if (k === 'v') { setTool('select'); return; }
    if (k === 'f') { fit(); render(); return; }
    const mv = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[k];
    if (mv && D.sel.size) { ev.preventDefault(); change(() => { for (const id of D.sel) { const e = byId(id); e.x += mv[0]; e.y += mv[1]; } }); }
  });
}
function remove() { if (D.sel.size) change(() => { D.L.elements = els().filter(e => !D.sel.has(e.id)); D.sel.clear(); }); }
function duplicate() {
  if (!D.sel.size) return;
  change(() => {
    const copies = [...D.sel].map(id => { const e = byId(id); const c = { ...e, id: newId() }; delete c.name; if (e.w >= e.h) c.y = e.y + e.h + 2; else c.x = e.x + e.w + 2; return c; });
    D.L.elements.push(...copies); D.sel = new Set(copies.map(c => c.id));
  });
}
function rotate() {
  if (!D.sel.size) return;
  change(() => { for (const id of D.sel) { const e = byId(id), cx = e.x + e.w / 2, cy = e.y + e.h / 2; [e.w, e.h] = [e.h, e.w]; e.x = Math.round(cx - e.w / 2); e.y = Math.round(cy - e.h / 2); } });
}

// ------------------------------------------------------------------ right panel
function props() {
  const box = $('dzProps');
  if (D.sel.size === 0) {
    $('dzPropTitle').textContent = 'Floor';
    box.innerHTML = `<div class="dz-props">
      <div class="field full"><label>Site name</label><input id="pName" value="${esc(D.L.name || '')}"></div>
      <div class="field"><label>Width (m)</label><input id="pW" type="number" min="10" max="500" value="${D.L.width_m}"></div>
      <div class="field"><label>Length (m)</label><input id="pH" type="number" min="10" max="500" value="${D.L.height_m}"></div></div>
      <p class="hint" style="margin-top:8px">Pick an element on the left and drag on the floor. Click an element to edit it.</p>`;
    $('pName').onchange = e => { $('dzName').value = e.target.value; change(() => { D.L.name = e.target.value.trim() || 'Untitled layout'; }); };
    $('pW').onchange = e => change(() => { D.L.width_m = Math.max(10, Math.min(500, Math.round(+e.target.value || 10))); });
    $('pH').onchange = e => change(() => { D.L.height_m = Math.max(10, Math.min(500, Math.round(+e.target.value || 10))); });
    return;
  }
  if (D.sel.size > 1) {
    $('dzPropTitle').textContent = `${D.sel.size} elements`;
    box.innerHTML = `<p class="hint">Drag to move them together. Arrows nudge 1 m.</p><div class="dz-acts">
      <button class="btn sm" id="pRot">Rotate (R)</button><button class="btn sm" id="pDup">Duplicate</button><button class="btn sm danger" id="pDel">Delete</button></div>`;
  } else {
    const e = byId([...D.sel][0]), t = D.cat.elements[e.type], nm = names()[e.id];
    $('dzPropTitle').textContent = t.label + (e.type === 'rack' || !SOLID.has(e.type) ? ' · ' + nm : '');
    const f = (id, lab, v, extra = '') => `<div class="field"><label>${lab}</label><input id="${id}" type="number" value="${v}" ${extra}></div>`;
    box.innerHTML = `<p class="hint" style="margin-bottom:8px">${esc(t.hint)}</p><div class="dz-props">
      ${e.type === 'rack' || e.type === 'nogo' ? `<div class="field full"><label>Name</label><input id="pN" value="${esc(e.name || '')}" placeholder="${esc(e.type === 'rack' ? nm : 'No-go')}"></div>` : ''}
      ${f('pX', 'X (m)', e.x)}${f('pY', 'Y (m)', e.y)}${f('pWd', 'Width (m)', e.w, 'min="1"')}${f('pHt', 'Length (m)', e.h, 'min="1"')}
      ${e.type === 'rack' ? f('pL', 'Levels', e.levels || 4, 'min="1" max="8"') + `<div class="field"><label>Bays</label><input disabled value="${Math.max(e.w, e.h)}"></div>` : ''}
      ${e.type === 'charging' ? f('pB', 'Charger bays', e.bays || '', 'min="1" max="60" placeholder="auto"') : ''}
      </div><div class="dz-acts"><button class="btn sm" id="pRot">Rotate (R)</button><button class="btn sm" id="pDup">Duplicate</button><button class="btn sm danger" id="pDel">Delete</button></div>`;
    const num = (id, key, min = -1e9, max = 1e9) => { const i = $(id); if (i) i.onchange = () => change(() => { e[key] = Math.max(min, Math.min(max, Math.round(+i.value || 0))); }); };
    num('pX', 'x'); num('pY', 'y'); num('pWd', 'w', 1, 500); num('pHt', 'h', 1, 500); num('pL', 'levels', 1, 8);
    if ($('pB')) $('pB').onchange = ev => change(() => { const v = Math.round(+ev.target.value || 0); if (v > 0) e.bays = Math.min(60, v); else delete e.bays; });
    if ($('pN')) $('pN').onchange = ev => change(() => { const v = ev.target.value.trim(); if (v) e.name = v.slice(0, 24); else delete e.name; });
  }
  $('pRot').onclick = rotate; $('pDup').onclick = duplicate; $('pDel').onclick = remove;
}

// ------------------------------------------------------------------ problems (server check, debounced)
function check() {
  clearTimeout(D.checkTimer);
  D.checkTimer = setTimeout(async () => {
    const seq = (D.checkSeq = (D.checkSeq || 0) + 1);
    try {
      const r = await fetch('/api/designs/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(D.L) });
      const rep = await r.json(); if (seq !== D.checkSeq) return;
      D.rep = rep; renderProblems(); render();
    } catch (e) { $('dzProblems').innerHTML = '<div class="hint">Could not check the layout (server offline?)</div>'; }
  }, 250);
}
function renderProblems() {
  const rep = D.rep, P = rep.problems.slice().sort((a, b) => ['error', 'warning', 'info'].indexOf(a.level) - ['error', 'warning', 'info'].indexOf(b.level));
  $('dzSummary').innerHTML = `<span class="dz-sum">${rep.errors ? `<span class="e">${rep.errors} error${rep.errors > 1 ? 's' : ''}</span>` : '<span class="k">Ready to run</span>'}${rep.warnings ? ` · <span class="w">${rep.warnings} warning${rep.warnings > 1 ? 's' : ''}</span>` : ''}</span>`;
  $('dzRun').disabled = !rep.ok;
  $('dzRun').title = rep.ok ? 'Save, turn the layout into the simulation map and start a shift' : 'Fix the errors in Problems first';
  $('dzProblems').innerHTML = P.length ? P.map((p, i) => `<div class="dz-prob ${p.level}" data-i="${i}"><span class="ic">${p.level === 'error' ? '!' : p.level === 'warning' ? '!' : 'i'}</span>
      <div>${esc(p.msg)}${p.fix ? `<small>${esc(p.fix)}</small>` : ''}</div></div>`).join('')
    : '<div class="dz-ok">No problems found. The layout is ready to run.</div>';
  $('dzProblems').onclick = e => {
    const d = e.target.closest('.dz-prob'); if (!d) return;
    const p = P[+d.dataset.i]; if (!p.ids.length) return;
    D.sel = new Set(p.ids.filter(byId)); props(); centerOn([...D.sel].map(byId)); render();
  };
  const s = rep.stats;
  $('dzStats').innerHTML = [['Floor', `${s.floor_m2.toLocaleString()} m²`], ['Free floor', `${s.free_m2.toLocaleString()} m² (${s.reachable_m2.toLocaleString()} m² reachable)`],
    ['Racks', `${s.racks} · ${s.pickable_bays}/${s.rack_bays} bays pickable`], ['Charger bays', s.chargers], ['Robots', s.robots], ['Orders', `${s.orders_per_hour} per hour`]]
    .map(([k, v]) => `<div class="kv"><b>${k}</b><span>${v}</span></div>`).join('');
}
function centerOn(list) {
  if (!list.length) return;
  const r = $('dzWrap').getBoundingClientRect();
  const x0 = Math.min(...list.map(e => e.x)), y0 = Math.min(...list.map(e => e.y)), x1 = Math.max(...list.map(e => e.x + e.w)), y1 = Math.max(...list.map(e => e.y + e.h));
  D.view.ox = r.width / 2 - (x0 + x1) / 2 * D.view.s; D.view.oy = r.height / 2 - (y0 + y1) / 2 * D.view.s;
}
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
