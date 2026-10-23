// TusCore Fleet Console - core: live connection, header, KPIs, twin views, side panels, page routing.
import { Plan2D } from './plan2d.js';
import { initPages, showPage } from './pages.js';

export const $ = id => document.getElementById(id);
// Small notice in the corner - used for errors (and confirmations) so nothing fails silently.
export function toast(msg, ok = false) {
  const t = document.getElementById('toast'); if (!t) return;
  t.textContent = msg; t.className = 'toast' + (ok ? ' ok' : ''); t.style.display = 'block';
  clearTimeout(toast._h); toast._h = setTimeout(() => { t.style.display = 'none'; }, 4000);
}
async function failText(r) { try { const j = await r.json(); return typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { return r.statusText; } }
export const api = {
  get: async u => { const r = await fetch(u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); },
  post: async (u, b = {}) => {
    const r = await fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    if (!r.ok) { const m = await failText(r); toast('Not possible: ' + m); throw new Error(m); }
    return r.json();
  },
};
const PAGES = ['ops', 'overview', 'analytics', 'compare', 'whatif', 'integration'];
export const S = { MAP: null, SNAP: null, sel: null, tab: 'robots', view: '3d', twin: null, plan: null, page: 'ops', listeners: [] };
export const fmtT = t => { t = Math.floor(t); return [t / 3600, t % 3600 / 60, t % 60].map(v => String(Math.floor(v)).padStart(2, '0')).join(':'); };
export const fmtDur = x => { x = Math.round(x || 0); return x < 60 ? x + 's' : x < 3600 ? Math.floor(x / 60) + 'm ' + String(x % 60).padStart(2, '0') + 's' : (x / 3600).toFixed(1) + 'h'; };
export const KC = { box_pick: '#2E9FE8', high_pick: '#f5a524', pallet_move: '#e5484d', inventory_scan: '#46a758' };
export const KN = { box_pick: 'Box pick', high_pick: 'High-shelf pick (lift + tote)', pallet_move: 'Pallet move', inventory_scan: 'Inventory scan' };
const batColor = b => b > 40 ? '#3ddc97' : b > 20 ? '#f5a524' : '#ff5d6c';
const GLYPH = {
  tote_amr: '<svg width="16" height="16" viewBox="0 0 16 16"><rect x="3" y="5" width="10" height="7" rx="1.5" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M3 8h10" stroke="#fff"/></svg>',
  scissor_lift: '<svg width="16" height="16" viewBox="0 0 16 16"><path d="M3 13h10M4 11l8-5M12 11L4 6M3 4h10" stroke="#fff" stroke-width="1.4" fill="none"/></svg>',
  pallet_stacker: '<svg width="16" height="16" viewBox="0 0 16 16"><path d="M2 12h12M4 12V6h5v6M11 12V9" stroke="#fff" stroke-width="1.5" fill="none"/></svg>',
  drone: '<svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="2" fill="#fff"/><path d="M3 3l3 3M13 3l-3 3M3 13l3-3M13 13l-3-3" stroke="#fff" stroke-width="1.4"/></svg>',
};
export const glyph = t => GLYPH[t] || GLYPH.tote_amr;
const nice = id => id.replace(/_/g, ' ');

// ------------------------------------------------------------------ boot
async function init() {
  await loadMap();
  connect();
  bindHeader(); bindPanel();
  initPages();
  window.addEventListener('hashchange', route); route();
}

async function loadMap() {
  S.MAP = await api.get('/api/map');
  $('legend').innerHTML = Object.values(S.MAP.types).map(t => `<span><i style="background:${t.color}"></i>${t.label}</span>`).join('');
  const lays = await api.get('/api/layouts');
  $('layoutSel').innerHTML = lays.map(l => `<option value="${l.file}" ${l.active ? 'selected' : ''} title="${l.width}×${l.height} m · ${l.robots} robots">${l.name} · ${l.robots} robots</option>`).join('');
  S.layouts = lays;
  S.closedKey = null;
  buildViews();
  S.listeners.forEach(f => f('map'));
}

async function buildViews() {
  if (S.twin) { S.twin.dispose(); S.twin = null; }
  if (S.plan) { S.plan.dispose(); S.plan = null; }
  S.plan = new Plan2D($('plan2d'), $('map2d'), S.MAP, { onSelect: select, onDispatch: dispatch2d, onHover: tip, getSelected: () => S.sel });
  try {
    const { Twin3D } = await import('./twin3d.js');
    S.twin = new Twin3D($('view3d'), S.MAP, { onSelect: select, onHover: tip });
  } catch (e) {
    console.warn('3D view unavailable, using 2D plan', e);
    $('viewSeg').querySelector('[data-v="3d"]').disabled = true; S.view = '2d';
  }
  applyView();
}

function applyView() {
  const is3d = S.view === '3d' && S.twin;
  $('view3d').style.display = is3d ? 'block' : 'none';
  $('plan2d').style.display = is3d ? 'none' : 'flex';
  $('camctl').style.display = is3d ? 'flex' : 'none';
  $('twinHint').textContent = is3d ? 'Drag to orbit · right-drag to pan · scroll to zoom · click a robot' : 'Click a robot to inspect · click the floor to send an idle robot there';
  if (S.twin) { S.twin.active = is3d && S.page === 'ops'; if (is3d) S.twin.resize(); }
  if (S.plan) { S.plan.active = !is3d && S.page === 'ops'; if (!is3d) S.plan.resize(); }
  document.querySelectorAll('#viewSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === (is3d ? '3d' : '2d')));
}

function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onopen = () => $('live').classList.add('live');
  ws.onclose = () => { $('live').classList.remove('live'); $('livetxt').textContent = 'Reconnecting'; setTimeout(connect, 1000); };
  ws.onmessage = e => { S.SNAP = JSON.parse(e.data); onSnap(); };
}

function route() {
  const p = (location.hash || '#ops').slice(1);
  S.page = PAGES.includes(p) ? p : 'ops';
  document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('on', b.dataset.p === S.page));
  document.querySelectorAll('.page').forEach(s => s.classList.toggle('on', s.id === 'p-' + S.page));
  applyView(); showPage(S.page);
}

// ------------------------------------------------------------------ live snapshot
function onSnap() {
  const D = S.SNAP, R = D.robots, k = D.kpis, n = R.length, on = R.filter(r => r.state !== 'fault').length;
  if (D.site !== S.MAP.name || D.mode !== S.MAP.mode) { S.MAP.name = D.site; if (D.mode !== S.MAP.mode) { S.MAP.mode = D.mode; } }
  $('clock').textContent = fmtT(D.t);
  $('shiftlbl').textContent = 'Shift ' + Math.min(100, Math.round(D.t / D.shift * 100)) + '%';
  $('livetxt').textContent = `Live · ${on}/${n}`;
  const sep = D.mode === 'separate';
  $('modePill').className = 'pill ' + (sep ? 'mode-s' : 'mode-t');
  $('modeTxt').textContent = sep ? 'Separate managers' : 'TusCore · one manager';
  $('pauseBar').style.display = D.paused ? 'flex' : 'none';
  $('pauseBtn').textContent = D.paused ? 'Resume fleet' : 'Pause fleet';
  $('pauseBtn').classList.toggle('danger', !D.paused);
  const nA = (D.alerts || []).length, badge = $('navAlerts'); badge.textContent = nA; badge.style.display = nA ? 'block' : 'none';
  const ck = (D.closed_rows || []).join(',');
  if (ck !== S.closedKey) { S.closedKey = ck; buildAisleSel(D.closed_rows || []); }
  $('modeBanner').style.display = sep ? 'block' : 'none';
  $('k_done').innerHTML = `${k.completed}<small>/ ${k.created}</small>`;
  $('k_donesub').textContent = `${k.on_time_pct}% on time · ${k.queued} queued · ${k.active} active`;
  $('k_oph').innerHTML = `${k.orders_per_hour}<small>orders/h</small>`;
  $('k_ophsub').textContent = `Box ${k.by_kind.box_pick} · High ${k.by_kind.high_pick} · Pallet ${k.by_kind.pallet_move} · Scan ${k.by_kind.inventory_scan}`;
  $('k_wait').innerHTML = `${Math.round(k.avg_wait)}<small>s</small>`;
  $('k_waitsub').textContent = 'avg cycle ' + fmtDur(k.avg_cycle);
  $('k_util').innerHTML = `${Math.round(k.utilization)}<small>%</small>`;
  const busyNow = R.filter(r => ['moving', 'working', 'waiting'].includes(r.state)).length;
  $('k_utilsub').textContent = `${busyNow} of ${n} robots busy right now`;
  $('k_util').title = Object.entries(k.util_by_type || {}).map(([t, v]) => `${S.MAP.types[t] ? S.MAP.types[t].label : t}: ${Math.round(v)}%`).join('\n');
  $('k_en').innerHTML = `${k.energy_kwh}<small>kWh</small>`;
  $('k_ensub').textContent = `${k.mismatches} stock mismatches flagged`;
  const sf = (v, l, cls = '') => `<div class="sf"><i class="${cls}"></i><b>${v}</b><span>${l}</span></div>`;
  $('safety').innerHTML = sf(k.collisions, 'Collisions', k.collisions ? 'bad' : '') +
    sf(k.deadlocks, 'Deadlocks auto-resolved') +
    sf(sep ? k.operator_interventions : k.permanent_deadlocks, sep ? 'Operator interventions' : 'Permanent deadlocks', (sep ? k.operator_interventions : k.permanent_deadlocks) ? 'bad' : '') +
    sf(`${k.faults} <small style="color:var(--muted);font-weight:500">/ ${k.reassigned} reassigned</small>`, 'Robot faults', k.faults ? 'warn' : '') +
    sf(k.traffic_delay_min + ' min', 'Time lost to traffic', 'warn');
  document.querySelectorAll('#speed button').forEach(b => b.classList.toggle('on', +b.dataset.s === D.scale));
  document.querySelectorAll('#alloc button').forEach(b => b.classList.toggle('on', b.dataset.a === D.allocator));
  $('autoBtn').textContent = D.auto_orders ? 'Orders: auto' : 'Orders: manual';
  $('autoBtn').classList.toggle('on', !!D.auto_orders);
  if (S.twin) S.twin.update(D);
  if (S.plan) S.plan.update(D);
  if (S.page === 'ops') { if (S.tab === 'tasks') updateTasks(); else renderPanel(); }
  S.listeners.forEach(f => f('snap'));
}

// ------------------------------------------------------------------ interaction
function select(id) { S.sel = id || null; if (S.twin) S.twin.select(S.sel); if (S.tab !== 'robots') setTab('robots'); else renderPanel(true); }
async function dispatch2d(x, y) {
  if (!S.SNAP) return;
  let id = S.sel;
  const me = id && S.SNAP.robots.find(r => r.id === id);
  if (!me || me.state !== 'idle' || me.task) {
    let best = 1e9; id = null;
    for (const r of S.SNAP.robots) { if (r.state !== 'idle' || r.task || S.MAP.types[r.type].flying) continue; const d = Math.hypot(r.x - x, r.y - y); if (d < best) { best = d; id = r.id; } }
  }
  if (id) await api.post(`/api/robots/${id}/goto`, { x, y });
}
function tip(r, x, y) {
  const el = $('tip');
  if (!r) { el.style.display = 'none'; return; }
  const t = S.MAP.types[r.type];
  el.style.display = 'block'; el.style.left = (x + 18) + 'px'; el.style.top = (y + 12) + 'px';
  el.innerHTML = `<b>${nice(r.id)}</b> · ${r.state}${r.action ? ' · ' + r.action : ''}<br><span style="color:var(--muted)">${t.vendor} · ${t.interface} · battery ${Math.round(r.battery)}%</span>`;
}

function buildAisleSel(closed) {
  const n = (S.MAP.aisles || []).length;
  $('aisleSel').innerHTML = `<option value="">Maintenance…</option>` +
    Array.from({ length: n }, (_, i) => closed.includes(i) ? `<option value="o${i}">Re-open aisle A${i + 1}</option>` : `<option value="c${i}">Close aisle A${i + 1}</option>`).join('');
}
async function refreshHeat() {
  if (!S.heat) return;
  try { const h = await api.get('/api/heatmap'); if (S.twin) S.twin.setHeat(h); if (S.plan) S.plan.setHeat(h); } catch (e) { /* next round */ }
}
function bindHeader() {
  // left menu -> pages (each page has its own URL, e.g. #analytics, so it can be bookmarked or shared)
  $('nav').addEventListener('click', e => { const b = e.target.closest('button[data-p]'); if (b) { if (location.hash === '#' + b.dataset.p) route(); else location.hash = b.dataset.p; } });
  $('heatBtn').onclick = () => {
    S.heat = !S.heat; $('heatBtn').classList.toggle('on', S.heat); $('heatLegend').style.display = S.heat ? 'flex' : 'none';
    clearInterval(S.heatTimer);
    if (S.heat) { refreshHeat(); S.heatTimer = setInterval(refreshHeat, 3000); } else { if (S.twin) S.twin.setHeat(null); if (S.plan) S.plan.setHeat(null); }
  };
  $('aisleSel').onchange = async e => {
    const v = e.target.value; if (!v) return;
    try { await api.post(`/api/aisles/${v.slice(1)}`, { closed: v[0] === 'c' }); toast(v[0] === 'c' ? `Aisle A${+v.slice(1) + 1} closed - robots are rerouting` : `Aisle A${+v.slice(1) + 1} re-opened`, true); } catch (er) { /* toast shown */ }
    e.target.value = '';
  };
  const pause = () => api.post('/api/fleet/pause', { enabled: !(S.SNAP && S.SNAP.paused) });
  $('pauseBtn').onclick = pause; $('resumeBtn').onclick = pause;
  $('speed').addEventListener('click', e => { const b = e.target.closest('button'); if (b) api.post('/api/sim/speed', { scale: +b.dataset.s }); });
  $('alloc').addEventListener('click', e => { const b = e.target.closest('button'); if (b) api.post('/api/sim/allocator', { name: b.dataset.a }); });
  $('autoBtn').onclick = () => api.post('/api/sim/auto-orders', { enabled: !(S.SNAP && S.SNAP.auto_orders) });
  $('resetBtn').onclick = () => api.post('/api/sim/reset', {});
  $('modePill').style.cursor = 'pointer'; $('modePill').title = 'Click to switch the live twin between one TusCore manager and separate vendor managers';
  $('modePill').onclick = async () => { await api.post('/api/sim/mode', { mode: S.SNAP && S.SNAP.mode === 'separate' ? 'tuscore' : 'separate' }); };
  $('layoutSel').onchange = async e => { await api.post('/api/sim/layout', { name: e.target.value }); S.sel = null; await loadMap(); };
  $('viewSeg').addEventListener('click', e => { const b = e.target.closest('button'); if (b && !b.disabled) { S.view = b.dataset.v; applyView(); } });
  $('camctl').addEventListener('click', e => {
    const b = e.target.closest('[data-cam]'); if (!b || !S.twin) return;
    if (b.dataset.cam === 'follow' && !S.sel) { const first = S.SNAP && S.SNAP.robots.find(r => r.state === 'moving'); if (first) select(first.id); }
    S.twin.view(b.dataset.cam);
  });
}

// ------------------------------------------------------------------ side panel
function setTab(t) { S.tab = t; document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x.dataset.t === t)); $('panel').innerHTML = ''; $('panel').dataset.key = ''; renderPanel(true); }
function bindPanel() {
  $('tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.t); });
  $('panel').addEventListener('pointerdown', () => { S.hold = Date.now(); });
  $('panel').addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (act) {
      const id = act.dataset.id;
      if (act.dataset.act === 'fault') api.post(`/api/robots/${id}/fault`);
      else if (act.dataset.act === 'repair') api.post(`/api/robots/${id}/repair`);
      else if (act.dataset.act === 'charge') api.post(`/api/robots/${id}/charge`).then(() => toast(`${nice(id)} is going to a charger`, true)).catch(() => {});
      else if (act.dataset.act === 'vendor') api.post(`/api/vendors/${id}/online`, { enabled: act.dataset.on === '1' });
      else if (act.dataset.act === 'follow' && S.twin) { S.view = '3d'; applyView(); S.twin.select(id); S.twin.view('follow'); }
      return;
    }
    const el = e.target.closest('.rob'); if (el) select(S.sel === el.dataset.id ? null : el.dataset.id);
  });
}
// Re-render at most every 400 ms, and never while the user is pressing a button/row (clicks must not hit a replaced element).
function renderPanel(force = false) {
  const now = Date.now();
  if (!force && S.tab !== 'robots' && (now - (S.lastPanel || 0) < 400 || now - (S.hold || 0) < 700)) return;
  S.lastPanel = now; const P = $('panel'), st = P.scrollTop; panelInner(P); P.scrollTop = st;
}
function rowHTML(r) {
  const t = S.MAP.types[r.type];
  return `<div class="rob ${r.id === S.sel ? 'sel' : ''}" data-id="${r.id}">
    <div class="ic" style="background:${t.color}">${glyph(r.type)}</div>
    <div style="min-width:0"><div class="nm">${nice(r.id)}</div><div class="meta">${r.action ? r.action : t.vendor + ' · ' + t.interface}</div></div>
    <div class="r"><span class="chip ${r.state}">${r.state.replace('_', ' ')}</span><div class="bar"><i style="width:${r.battery}%;background:${batColor(r.battery)}"></i></div></div></div>`;
}
function panelInner(P) {
  if (!S.MAP || !S.SNAP) return;
  const D = S.SNAP;
  if (S.tab === 'robots') {
    // Structure is built once (and when the selection changes); afterwards rows are updated IN PLACE,
    // so the elements a user is clicking are never replaced.
    const me = D.robots.find(x => x.id === S.sel) || {};
    const key = `${S.sel || ''}|${D.robots.length}|${me.state === 'fault'}|${me.state === 'idle' && !me.task}`;
    if (P.dataset.key !== key || !$('rlist')) {
      P.dataset.key = key;
      let h = '<div id="rdetail"></div><div id="rlist">';
      for (const [k, t] of Object.entries(S.MAP.types)) {
        const list = D.robots.filter(x => x.type === k);
        if (list.length) h += `<div class="grp">${t.label} · ${t.vendor} · ${list.length}</div>` + list.map(rowHTML).join('');
      }
      P.innerHTML = h + '</div>';
      const r = D.robots.find(x => x.id === S.sel);
      if (r) $('rdetail').innerHTML = `<div class="detail"><div id="rdinfo"></div>
        <div style="margin-top:10px;display:flex;gap:8px">${r.state === 'fault' ? `<button class="btn sm" data-act="repair" data-id="${r.id}">Repair now</button>` : `<button class="btn danger sm" data-act="fault" data-id="${r.id}">Inject fault</button>`}
        <button class="btn sm" data-act="follow" data-id="${r.id}">Follow in 3D</button>${r.state === 'idle' && !r.task ? `<button class="btn sm" data-act="charge" data-id="${r.id}">Send to charger</button>` : ''}</div></div>`;
    }
    for (const r of D.robots) {
      const el = P.querySelector(`.rob[data-id="${r.id}"]`); if (!el) continue;
      const t = S.MAP.types[r.type];
      el.classList.toggle('sel', r.id === S.sel);
      el.querySelector('.meta').textContent = r.action ? r.action : t.vendor + ' · ' + t.interface;
      const chip = el.querySelector('.chip'); chip.className = 'chip ' + r.state; chip.textContent = r.state.replace('_', ' ');
      const bar = el.querySelector('.bar i'); bar.style.width = r.battery + '%'; bar.style.background = batColor(r.battery);
    }
    const r = D.robots.find(x => x.id === S.sel), info = $('rdinfo');
    if (r && info) {
      const t = S.MAP.types[r.type];
      info.innerHTML = `<h3><span class="ic" style="background:${t.color}">${glyph(r.type)}</span>${nice(r.id)}</h3>
      <div class="kv"><b>Type</b><span>${t.label}</span></div><div class="kv"><b>Vendor</b><span>${t.vendor}</span></div>
      <div class="kv"><b>Interface</b><span class="mono">${t.interface}</span></div><div class="kv"><b>State</b><span class="chip ${r.state}">${r.state.replace('_', ' ')}</span></div>
      <div class="kv"><b>Order</b><span>${r.task ? '#' + r.task : '—'}</span></div><div class="kv"><b>Action</b><span>${r.action || '—'}</span></div>
      <div class="kv"><b>Carrying</b><span>${r.load || '—'}</span></div>
      <div class="kv"><b>Position</b><span class="mono">${Math.round(r.x)}, ${Math.round(r.y)}</span></div>
      <div class="kv"><b>Speed · priority</b><span>${t.speed} m/s · P${r.priority}</span></div>
      <div class="kv"><b>Odometer</b><span>${r.dist} m</span></div>
      ${r.blocked_for >= 1 ? `<div class="kv"><b>Waiting for</b><span style="color:var(--warn)">${r.blocked_by ? nice(r.blocked_by) : '—'} · ${Math.round(r.blocked_for)}s</span></div>` : ''}
      <div class="kv"><b>Battery</b><span style="color:${batColor(r.battery)}">${r.battery}%</span></div>
      <div class="bar"><i style="width:${r.battery}%;background:${batColor(r.battery)}"></i></div>`;
    }
  } else if (S.tab === 'vendors') {
    P.innerHTML = D.vendors.map(v => {
      const t = S.MAP.types[v.id];
      return `<div class="vend"><div class="top"><span style="width:10px;height:10px;border-radius:50%;background:${t.color}"></span><b>${v.vendor}</b>
      <span class="on-dot ${v.online ? '' : 'off'}"><i></i>${v.online ? 'Online' : 'Offline'}</span></div>
      <div class="kv" style="margin-top:8px"><b>Fleet</b><span>${v.label} ×${v.robots}${v.faulted ? ` · <span style="color:var(--bad)">${v.faulted} faulted</span>` : ''}</span></div>
      <div class="kv"><b>Protocol</b><span class="mono">${v.protocol}</span></div><div class="kv"><b>Adapter</b><span class="badge">${v.kind.toUpperCase()}</span></div>
      <div class="kv"><b>Messages</b><span class="mono">↑ ${v.sent} · ↓ ${v.received}</span></div>
      <div class="kv"><b>Last heartbeat</b><span>${v.last_seen_age == null ? '—' : v.last_seen_age < 2 ? 'now' : fmtDur(v.last_seen_age) + ' ago'}</span></div>
      <div class="kv"><b>Latency</b><span>${v.latency_ms} ms</span></div>
      <div style="margin-top:10px"><button class="btn sm ${v.online ? 'danger' : ''}" data-act="vendor" data-id="${v.id}" data-on="${v.online ? 0 : 1}">${v.online ? 'Simulate outage' : 'Restore connection'}</button></div></div>`;
    }).join('') + `<p class="note" style="font-size:11px">Four vendors, four protocols, one TusCore core. A new vendor is one adapter class. During an outage robots finish their current mission; no new missions are sent.</p>`;
  } else if (S.tab === 'events') {
    P.innerHTML = D.events.map(e => `<div class="ev"><span class="sv ${e.sev}"></span><time class="mono">${fmtT(e.t)}</time><div>${e.msg}</div></div>`).join('') || '<div class="empty"><b>No events yet</b></div>';
  } else if (S.tab === 'protocol') {
    P.innerHTML = `<p class="note" style="font-size:11px;margin-bottom:6px">Live wire messages between TusCore and each vendor's robots.</p>` +
      D.messages.map(m => { const t = S.MAP.types[m.vendor]; return `<div class="msg"><div class="h"><span class="dir ${m.dir}">${m.dir === 'out' ? '→ robot' : '← robot'}</span><span style="width:8px;height:8px;border-radius:50%;background:${t.color}"></span><b>${m.vendor_name}</b><span class="mono">${m.topic}</span><span style="margin-left:auto" class="mono">${fmtT(m.t)}</span></div><code>${m.body.replace(/</g, '&lt;')}</code></div>`; }).join('');
  } else renderTasks();
}
function renderTasks() {
  const P = $('panel');
  if (!$('tlist')) {
    const rows = [...S.MAP.shelves].sort((a, b) => a.y - b.y).map((s, i) => `<option value="${i}">A${i + 1}</option>`).join('');
    const maxBay = Math.max(...S.MAP.shelves.map(s => s.w));
    P.innerHTML = `<div class="tsum" id="tsum"></div>
    <form class="oform" id="oform">
      <div class="full"><label>New order</label><select id="o_kind">${S.MAP.kinds.map(k => `<option value="${k}">${KN[k]}</option>`).join('')}</select></div>
      <div><label>Shelf row</label><select id="o_row"><option value="">Random</option>${rows}</select></div>
      <div><label>Bay (1-${maxBay})</label><input id="o_bay" type="number" min="1" max="${maxBay}" placeholder="Random"></div>
      <div class="full"><button class="btn primary" style="width:100%" type="submit">Submit order</button></div>
    </form><div id="tlist"></div>`;
    const sync = () => { const k = $('o_kind').value; $('o_row').disabled = k === 'pallet_move'; $('o_bay').disabled = !(k === 'box_pick' || k === 'high_pick'); };
    $('o_kind').onchange = sync; sync();
    $('oform').onsubmit = async e => {
      e.preventDefault(); const row = $('o_row').value, bay = $('o_bay').value;
      await api.post('/api/orders', { kind: $('o_kind').value, row: row === '' || $('o_row').disabled ? null : +row, bay: bay === '' || $('o_bay').disabled ? null : +bay - 1 });
    };
  }
  updateTasks();
}
function updateTasks() {
  if (!S.SNAP) return;
  const now = Date.now(); if (now - (S.lastTasks || 0) < 400 || now - (S.hold || 0) < 700) return; S.lastTasks = now;
  if (!$('tlist')) return renderTasks();
  const k = S.SNAP.kpis, label = { queued: 'Queued', in_progress: 'Active', done: 'Done' };
  $('tsum').innerHTML = `<div><b>${k.queued}</b><span>Queued</span></div><div><b>${k.active}</b><span>Active</span></div><div><b>${k.completed}</b><span>Done</span></div>`;
  const T = S.SNAP.tasks, rows = [...T.filter(t => t.status === 'in_progress'), ...T.filter(t => t.status === 'queued'), ...S.SNAP.recent.slice(0, 8)].slice(0, 40);
  $('tlist').innerHTML = rows.map(t => {
    const who = Object.values(t.robots).map(nice).join(' + ');
    const meta = t.status === 'queued' ? 'waiting ' + fmtDur(t.wait) : t.status === 'done' ? `done in ${fmtDur(t.cycle)} · ${who}` : who;
    return `<div class="trow"><span class="kd" style="background:${KC[t.kind]}"></span>
      <div style="min-width:0"><div class="nm">#${t.id} ${t.title}${t.manual ? ' <span class="badge">MANUAL</span>' : ''}${t.reassigned ? ' <span class="badge" style="color:var(--warn)">REASSIGNED</span>' : ''}${t.mismatch && t.status === 'done' ? ' <span class="badge" style="color:var(--warn)">MISMATCH</span>' : ''}</div><div class="meta">${meta}</div></div>
      <div style="margin-left:auto;text-align:right;min-width:70px"><span class="chip ${t.status}">${label[t.status]}</span><div class="bar"><i style="width:${t.progress * 100}%"></i></div></div></div>`;
  }).join('') || '<div class="empty"><b>No orders yet</b>Orders arrive automatically during the shift, or submit one above.</div>';
}

window.tuscore = S;   // handy for demos and debugging in the browser console
init();
