// Step 2 of the console: login and roles, alarms, missions, WMS connection, shift history and replay.
import { $, api, toast, S, fmtT, fmtDur } from './app.js';
import { Plan2D } from './plan2d.js';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ago = w => { const s = Math.round(Date.now() / 1000 - w); return s < 60 ? 'just now' : s < 3600 ? Math.floor(s / 60) + ' min ago' : s < 86400 ? Math.floor(s / 3600) + ' h ago' : new Date(w * 1000).toLocaleDateString(); };
const when = w => new Date(w * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
export const isAdmin = () => !S.user || S.user.role === 'admin';

// ================================================================== login
let loginWaiter = null;
export async function ensureLogin() {
  try {
    const r = await fetch('/api/auth/me');
    if (r.ok) { const j = await r.json(); if (j.user) { setUser(j.user, j.auth); return; } }
  } catch (e) { /* server down: the login form shows the error */ }
  await showLogin();
}
export function showLogin(msg = '') {
  $('login').hidden = false; $('lgErr').textContent = msg; setTimeout(() => $('lgUser').focus(), 30);
  return new Promise(res => { loginWaiter = res; });
}
function setUser(u, authOn = true) {
  S.user = u; S.auth = authOn;
  document.body.classList.toggle('role-operator', u.role === 'operator');
  $('userMenu').hidden = !authOn;
  $('userName').textContent = u.username; $('userRole').textContent = u.role; $('userIcon').textContent = u.username[0].toUpperCase();
  if (authOn && u.must_change) setTimeout(() => openPassword(true), 300);
}
function bindLogin() {
  $('loginForm').onsubmit = async e => {
    e.preventDefault();
    const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('lgUser').value.trim(), password: $('lgPw').value }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { $('lgErr').textContent = j.detail || 'Login failed'; return; }
    $('lgPw').value = ''; $('login').hidden = true;
    const first = !S.user; setUser(j.user, true);
    if (loginWaiter) { const f = loginWaiter; loginWaiter = null; f(); }
    if (!first) location.reload();                       // session ran out while working: start clean
  };
  $('userBtn').onclick = e => { e.stopPropagation(); $('umenu').hidden = !$('umenu').hidden; };
  document.addEventListener('click', () => { $('umenu').hidden = true; });
  $('umenu').onclick = async e => {
    const b = e.target.closest('[data-u]'); if (!b) return;
    if (b.dataset.u === 'logout') { await fetch('/api/auth/logout', { method: 'POST' }); location.reload(); }
    if (b.dataset.u === 'password') openPassword(false);
    if (b.dataset.u === 'users') openUsers();
  };
  const f = $('pwForm');
  $('pwDlg').addEventListener('close', () => { f.reset(); $('pwErr').textContent = ''; });
  f.onsubmit = async e => {
    if (e.submitter && e.submitter.value === 'cancel') return;
    e.preventDefault();
    if (f.new.value !== f.new2.value) { $('pwErr').textContent = 'The two new passwords are not the same.'; return; }
    const r = await fetch('/api/auth/password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old: f.old.value, new: f.new.value }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { $('pwErr').textContent = j.detail || 'Not possible'; return; }
    S.user.must_change = false; $('pwDlg').close(); toast('Password changed', true);
  };
}
function openPassword(forced) {
  $('pwWhy').textContent = forced ? 'You are using the first-start password. Please choose your own now.' : '';
  $('pwCancel').hidden = forced; $('pwDlg').showModal();
}
async function openUsers() {
  const [users, audit] = await Promise.all([api.get('/api/users'), api.get('/api/audit?limit=150')]);
  $('usersTbl').innerHTML = '<tr><th>User</th><th>Role</th><th>Status</th><th></th></tr>' + users.map(u => `<tr>
    <td><b>${esc(u.username)}</b>${u.must_change ? ' <span class="hint">(first password)</span>' : ''}</td>
    <td><select data-role="${u.id}"><option ${u.role === 'operator' ? 'selected' : ''}>operator</option><option ${u.role === 'admin' ? 'selected' : ''}>admin</option></select></td>
    <td>${u.active ? '<span class="ok">active</span>' : '<span class="hint">switched off</span>'}</td>
    <td><button class="btn sm" data-act="${u.id}" data-v="${u.active ? 0 : 1}">${u.active ? 'Switch off' : 'Switch on'}</button> <button class="btn sm" data-pw="${u.id}">Reset password</button></td></tr>`).join('');
  $('auditLog').innerHTML = audit.map(a => `<div><span class="mono">${when(a.wall)}</span><b>${esc(a.user)}</b><span>${esc(a.action)}${a.detail ? ' · ' + esc(a.detail) : ''}</span></div>`).join('') || '<span class="hint">Nothing yet.</span>';
  const patch = async (id, body) => {
    const r = await fetch('/api/users/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) toast('Not possible: ' + ((await r.json().catch(() => ({}))).detail || r.statusText)); else toast('Saved', true);
    openUsers();
  };
  $('usersTbl').onchange = e => { const s = e.target.closest('[data-role]'); if (s) patch(s.dataset.role, { role: s.value }); };
  $('usersTbl').onclick = e => {
    const a = e.target.closest('[data-act]'); if (a) patch(a.dataset.act, { active: a.dataset.v === '1' });
    const p = e.target.closest('[data-pw]');
    if (p) { const pw = prompt('New first password for this user (8+ characters). They must change it at login.'); if (pw) patch(p.dataset.pw, { password: pw }); }
  };
  $('userAdd').onsubmit = async e => {
    e.preventDefault(); const f = e.target;
    try { await api.post('/api/users', { username: f.username.value.trim(), password: f.password.value, role: f.role.value }); f.reset(); toast('User added', true); openUsers(); } catch (x) { /* toast shown */ }
  };
  $('usersClose').onclick = () => $('usersDlg').close();
  if (!$('usersDlg').open) $('usersDlg').showModal();
}

// ================================================================== alarms
const LV = { critical: 'Critical', major: 'Major', minor: 'Minor', info: 'Info' };
const ST = { active_unacked: 'New', active_acked: 'Acknowledged', cleared_unacked: 'Cleared, not seen' };
let alarmKey = '';
export function alarmsSnap(D) {
  const sum = D.alarm_summary || { unacked: 0, open: 0 }, b = $('alarmBtn');
  $('alarmN').textContent = sum.unacked || sum.open || 0;
  b.className = 'alarmbtn' + (sum.unacked ? ' on lv-' + sum.worst : sum.open ? ' acked' : '');
  b.title = sum.open ? `${sum.open} open alarm(s), ${sum.unacked} not acknowledged` : 'No alarms';
  if (!$('alarmDrawer').hidden) renderAlarms(D.alarms || []);
}
function renderAlarms(list) {
  const key = list.map(a => a.id + a.state + a.detail).join('|'); if (key === alarmKey) return; alarmKey = key;
  $('alarmSum').textContent = list.length ? `${list.length} open` : '';
  $('alarmList').innerHTML = list.map(a => `<div class="alarm lv-${a.level} st-${a.state}">
      <i></i><div><b>${esc(a.title)}</b><small>${LV[a.level]} · ${ST[a.state] || a.state} · ${fmtT(a.raised)}${a.acked_by ? ' · ack ' + esc(a.acked_by) : ''}</small><span>${esc(a.detail)}</span></div>
      ${a.state.endsWith('unacked') ? `<button class="btn sm" data-ack="${a.id}">Acknowledge</button>` : ''}</div>`).join('') || '<div class="hint" style="padding:14px">No open alarms.</div>';
}
function bindAlarms() {
  $('alarmBtn').onclick = () => { const d = $('alarmDrawer'); d.hidden = !d.hidden; alarmKey = ''; if (!d.hidden && S.SNAP) renderAlarms(S.SNAP.alarms || []); };
  $('alarmClose').onclick = () => { $('alarmDrawer').hidden = true; };
  $('alarmAckAll').onclick = () => api.post('/api/alarms/ack-all').then(r => toast(`${r.acked} alarm(s) acknowledged`, true));
  $('alarmList').onclick = e => { const b = e.target.closest('[data-ack]'); if (b) { b.disabled = true; api.post(`/api/alarms/${b.dataset.ack}/ack`); } };
  $('alarmHist').onclick = () => { $('alarmDrawer').hidden = true; RP.tab = 'alarms'; };
}

// ================================================================== missions
const DO = { go: 'Drive to', load: 'Load tote', unload: 'Unload tote', lift: 'Lift pallet', set: 'Set down pallet', scan: 'Scan', wait: 'Wait' };
const ROLE = { carry_tote: 'Tote carrier', high_pick: 'High-shelf picker', move_pallet: 'Pallet mover', scan: 'Flying scanner' };
const MSTATE = { active: 'running', paused: 'paused', stopped: 'stopped', done: 'finished', invalid: 'cannot run' };
let msSteps = [], msTimer = null, msTargets = null;
const TEMPLATES = {
  replenish: { name: 'Replenish packing', robot: 'role:carry_tote', priority: 'normal', repeat: 0, every: 10,
    steps: [{ to: 'A1-03', do: 'load' }, { to: 'packing', do: 'unload' }] },
  patrol: { name: 'Patrol and wait', robot: 'role:carry_tote', priority: 'low', repeat: 3, every: 0,
    steps: [{ to: '', do: 'go' }, { to: '', do: 'wait', wait_s: 30 }, { to: 'charging', do: 'go' }] },
  pallet: { name: 'Pallet to dock', robot: 'role:move_pallet', priority: 'high', repeat: 1, every: 0,
    steps: [{ to: 'pallets', do: 'lift' }, { to: 'dock', do: 'set' }] },
};
export async function showMissions() {
  clearInterval(msTimer);
  await buildMissionForm();
  refreshMissions(); msTimer = setInterval(() => { if (S.page === 'missions') refreshMissions(); else clearInterval(msTimer); }, 1500);
}
async function buildMissionForm() {
  msTargets = await api.get('/api/missions/targets');
  const opts = [...msTargets.waypoints, ...msTargets.zones.map(z => z.id), 'packing', 'dock', 'pallets', 'charging',
    ...msTargets.racks.flatMap(r => r.pickable.slice(0, 40).map(b => `${r.name}-${String(b).padStart(2, '0')}`))];
  $('msTargets').innerHTML = [...new Set(opts)].map(o => `<option value="${esc(o)}">`).join('');
  const T = S.MAP.types, have = r => Object.values(T).some(t => t.count && t.capabilities.includes(r));
  $('msRobot').innerHTML = '<optgroup label="Any robot that does the job">' + Object.entries(ROLE).filter(([r]) => have(r)).map(([r, l]) => `<option value="role:${r}">${l}</option>`).join('') + '</optgroup>'
    + '<optgroup label="Robot type">' + Object.entries(T).filter(([, t]) => t.count).map(([k, t]) => `<option value="type:${k}">${esc(t.label)}</option>`).join('') + '</optgroup>'
    + '<optgroup label="One robot">' + ((S.SNAP && S.SNAP.robots) || []).map(r => `<option value="robot:${r.id}">${r.id.replace(/_/g, ' ')}</option>`).join('') + '</optgroup>';
  $('msTpl').innerHTML = '<option value="">Start from…</option><option value="replenish">Replenish packing (every 10 min)</option><option value="patrol">Patrol and wait</option><option value="pallet">Pallet to dock</option>';
  if (!msSteps.length) useTemplate('replenish');
}
function useTemplate(k) {
  const t = TEMPLATES[k]; if (!t) return;
  $('msName').value = t.name; $('msPrio').value = t.priority; $('msRepeat').value = t.repeat; $('msEvery').value = t.every;
  if ([...$('msRobot').options].some(o => o.value === t.robot)) $('msRobot').value = t.robot;
  msSteps = t.steps.map(s => ({ ...s }));
  if (k === 'patrol' && msTargets) { msSteps[0].to = msTargets.waypoints[0] || msTargets.zones[0].id; msSteps[1].to = msSteps[0].to; }
  if (k === 'replenish' && msTargets && msTargets.racks.length) { const r = msTargets.racks[0]; msSteps[0].to = `${r.name}-${String(r.pickable[0] || 1).padStart(2, '0')}`; }
  renderSteps();
}
function renderSteps() {
  $('msSteps').innerHTML = msSteps.map((s, i) => `<div class="ms-step" data-i="${i}"><span class="n">${i + 1}</span>
    <select data-k="do">${Object.entries(DO).map(([k, l]) => `<option value="${k}" ${k === s.do ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <input data-k="to" list="msTargets" value="${esc(s.to)}" placeholder="where: A3-05, packing_1, Wait A, 12,7">
    ${s.do === 'wait' ? `<input data-k="wait_s" type="number" min="0" max="3600" value="${s.wait_s ?? 30}" title="seconds" class="sec">` : ''}
    <button class="ed" data-up="${i}" title="Move up">↑</button><button class="ed" data-del="${i}" title="Remove">✕</button></div>`).join('');
}
function bindMissions() {
  $('msTpl').onchange = e => { useTemplate(e.target.value); e.target.value = ''; };
  $('msAddStep').onclick = () => { msSteps.push({ to: '', do: 'go' }); renderSteps(); };
  $('msSteps').oninput = $('msSteps').onchange = e => {
    const row = e.target.closest('.ms-step'), k = e.target.dataset.k; if (!row || !k) return;
    msSteps[+row.dataset.i][k] = k === 'wait_s' ? +e.target.value : e.target.value;
    if (k === 'do' && e.type === 'change') renderSteps();
  };
  $('msSteps').onclick = e => {
    const d = e.target.closest('[data-del]'), u = e.target.closest('[data-up]');
    if (d) { msSteps.splice(+d.dataset.del, 1); renderSteps(); }
    if (u && +u.dataset.up > 0) { const i = +u.dataset.up; [msSteps[i - 1], msSteps[i]] = [msSteps[i], msSteps[i - 1]]; renderSteps(); }
  };
  $('msCreate').onclick = async () => {
    const [kind, val] = $('msRobot').value.split(':');
    const body = { name: $('msName').value.trim() || 'Mission', robot: { [kind]: val }, priority: $('msPrio').value,
      repeat: +$('msRepeat').value || 0, every_min: +$('msEvery').value || 0, steps: msSteps.filter(s => s.to || s.do === 'wait') };
    try { const m = await api.post('/api/missions', body); toast(`Mission '${m.name}' started: ${m.repeat_text}`, true); refreshMissions(); } catch (e) { /* toast shown */ }
  };
  $('msList').onclick = async e => {
    const b = e.target.closest('[data-ma]'); if (!b) return;
    const id = b.closest('[data-m]').dataset.m;
    if (b.dataset.ma === 'delete') { if (!confirm('Delete this mission? An open run is cancelled.')) return; await fetch('/api/missions/' + id, { method: 'DELETE' }); }
    else await api.post(`/api/missions/${id}/${b.dataset.ma}`).catch(() => {});
    refreshMissions();
  };
}
async function refreshMissions() {
  const L = await api.get('/api/missions').catch(() => []);
  const act = L.filter(m => m.status === 'active').length;
  $('msMeta').textContent = L.length ? `${act} running · ${L.length} total` : '';
  $('msList').innerHTML = L.map(m => {
    const rob = m.robot.robot ? m.robot.robot.replace(/_/g, ' ') : m.robot.type ? (S.MAP.types[m.robot.type] || { label: m.robot.type }).label : ROLE[m.robot.role] || m.robot.role;
    const o = m.open, btn = (a, l) => `<button class="btn sm" data-ma="${a}">${l}</button>`;
    return `<div class="mcard st-${m.status}" data-m="${m.id}">
      <div class="top"><b>${esc(m.name)}</b><span class="pri p${m.priority}">${m.priority_name}</span><span class="mst">${MSTATE[m.status]}</span><span class="spacer"></span>
        ${m.status === 'active' ? btn('pause', 'Pause') + btn('run_now', 'Run now') : ''}${['paused', 'invalid'].includes(m.status) ? btn('resume', 'Resume') : ''}
        ${['stopped', 'done'].includes(m.status) ? btn('run_now', 'Run again') : ''}${['active', 'paused'].includes(m.status) ? btn('stop', 'Stop') : ''}${btn('delete', 'Delete')}</div>
      <div class="sub">${esc(rob)} · ${m.repeat_text} · ${m.runs} done${m.failed ? `, ${m.failed} cancelled` : ''}${m.last_cycle ? ` · last run ${fmtDur(m.last_cycle)}` : ''}${m.created_by ? ` · by ${esc(m.created_by)}` : ''}</div>
      <div class="steps">${m.steps.map((s, i) => `<span class="${o && o.progress * m.steps.length > i ? 'done' : ''}">${DO[s.do]}${s.to ? ' ' + esc(s.to) : ''}${s.do === 'wait' ? ` ${s.wait_s ?? 30}s` : ''}</span>`).join('<i>›</i>')}</div>
      ${o ? `<div class="run"><span>Run #${m.runs + m.failed + 1} · ${o.status === 'queued' ? 'waiting for a robot' : 'robot ' + Object.values(o.robots).join(', ').replace(/_/g, ' ')}</span><div class="bar"><i style="width:${Math.round(o.progress * 100)}%"></i></div></div>`
        : m.next_in_s != null && m.status === 'active' ? `<div class="run"><span>Next run in ${fmtDur(m.next_in_s)}</span></div>` : ''}
      ${m.error ? `<div class="err">${esc(m.error)}</div>` : ''}</div>`;
  }).join('') || '<div class="hint" style="padding:10px">No missions yet. Build one on the right: a list of steps a robot does in order, once or repeated.</div>';
}

// ================================================================== WMS
let wmsTimer = null;
export function showWms() {
  clearInterval(wmsTimer); refreshWms(); wmsTimer = setInterval(() => { if (S.page === 'integration') refreshWms(); else clearInterval(wmsTimer); }, 2000);
}
async function refreshWms() {
  const [st, orders] = await Promise.all([api.get('/api/wms/status'), api.get('/api/wms/orders?limit=60')]);
  const by = st.by_status || {}, m = st.mock;
  $('wmsMock').checked = m.on;
  $('wmsMeta').textContent = m.on ? `Mock WMS connected · ${m.sent} lines sent${m.next_wave_in_s != null ? ` · next wave in ${fmtDur(m.next_wave_in_s)}` : ''}` : 'Waiting for a WMS (or switch on the mock WMS)';
  const sb = (n, l, c = '') => `<div class="${c}"><b>${n || 0}</b><span>${l}</span></div>`;
  $('wmsStats').innerHTML = sb(st.orders, 'orders received') + sb(by.queued, 'queued') + sb(by.in_progress, 'in progress') + sb(by.done, 'done', 'ok') + sb((by.rejected || 0) + (by.cancelled || 0), 'rejected / cancelled', (by.rejected ? 'bad' : ''));
  $('wmsHooks').innerHTML = st.hooks.length ? st.hooks.map(h => `<span class="mono">${esc(h.url)}</span> <span class="hint">${h.ok} ok · ${h.failed} failed</span> <button class="ed" data-hook="${esc(h.url)}">✕</button>`).join('<br>') : 'none';
  const P = ['low', 'normal', 'high', 'urgent'];
  $('wmsOrders').innerHTML = '<tr><th>Ref</th><th>Type</th><th>Location</th><th>Priority</th><th>Status</th><th>Cycle</th></tr>' + orders.map(o => `<tr>
    <td class="mono">${esc(o.ref)}</td><td>${esc((o.type || '').replace(/_/g, ' '))}</td><td>${esc(o.location || '')}${o.destination ? ' › ' + esc(o.destination) : ''}</td>
    <td>${o.priority != null ? `<span class="pri p${o.priority}">${P[o.priority]}</span>` : ''}</td><td><span class="ws ${o.status}" title="${esc(o.reason || '')}">${o.status.replace('_', ' ')}</span></td><td>${o.cycle != null ? fmtDur(o.cycle) : ''}</td></tr>`).join('');
}
function bindWms() {
  $('wmsMock').onchange = e => api.post('/api/wms/mock', { on: e.target.checked, wave_min: +$('wmsWave').value }).then(refreshWms).catch(() => { e.target.checked = !e.target.checked; });
  $('wmsWave').onchange = () => { if ($('wmsMock').checked) api.post('/api/wms/mock', { on: true, wave_min: +$('wmsWave').value }).then(refreshWms); };
  $('wmsShow').onclick = async () => { const k = await api.get('/api/wms/key'); $('wmsKey').textContent = k.key; };
  $('wmsRotate').onclick = async () => { if (!confirm('Make a new API key? The WMS must then use the new key.')) return; const k = await api.post('/api/wms/key/rotate'); $('wmsKey').textContent = k.key; toast('New WMS key made', true); };
  $('wmsHookAdd').onclick = () => api.post('/api/wms/webhooks', { url: $('wmsHook').value.trim() }).then(() => { $('wmsHook').value = ''; refreshWms(); }).catch(() => {});
  $('wmsHooks').onclick = e => { const b = e.target.closest('[data-hook]'); if (b) fetch('/api/wms/webhooks?url=' + encodeURIComponent(b.dataset.hook), { method: 'DELETE' }).then(refreshWms); };
}

// ================================================================== shift history + replay
const RP = { shift: null, plan: null, t: 0, playing: false, frames: [], from: 0, to: -1, loading: false, tab: 'events', raf: null, last: 0 };
export async function showReplay() {
  const L = await api.get('/api/shifts?limit=60');
  $('rpShifts').innerHTML = '<tr><th>Started</th><th>Site</th><th>Mode</th><th>Length</th><th>Done</th><th>Cycle</th><th>Coll.</th></tr>' + L.map(s => `<tr data-s="${s.id}" class="${RP.shift && RP.shift.id === s.id ? 'on' : ''}">
    <td>${when(s.started)}${s.ended ? '' : ' <span class="live-dot" title="running now">●</span>'}</td><td>${esc(s.site)}</td><td>${s.mode === 'tuscore' ? 'TusCore' : 'Separate'}</td>
    <td>${fmtDur(s.sim_end)}</td><td>${s.kpis.completed ?? 0}</td><td>${s.kpis.avg_cycle ? fmtDur(s.kpis.avg_cycle) : ''}</td><td>${s.kpis.collisions ?? 0}</td></tr>`).join('');
  if (!RP.shift && L.length) openShift(L.find(s => s.ended && s.sim_end > 30) ? L.find(s => s.ended && s.sim_end > 30).id : L[0].id);
  else if (RP.tab) renderSide();
}
async function openShift(id) {
  stopPlay();
  const sh = await api.get('/api/shifts/' + id);
  RP.shift = sh; RP.frames = []; RP.from = 0; RP.to = -1; RP.t = 0;
  document.querySelectorAll('#rpShifts tr[data-s]').forEach(r => r.classList.toggle('on', +r.dataset.s === id));
  $('rpTitle').textContent = `Replay · ${sh.site}`;
  const k = sh.kpis || {};
  $('rpMeta').textContent = `${when(sh.started)} · ${sh.mode === 'tuscore' ? 'TusCore' : 'separate managers'} · ${k.completed ?? 0} orders · avg cycle ${fmtDur(k.avg_cycle)} · ${k.collisions ?? 0} collisions`;
  $('rpTime').max = Math.max(1, Math.floor(sh.sim_end)); $('rpTime').value = 0;
  $('rpEmpty').hidden = true;
  if (RP.plan) RP.plan.dispose();
  const types = sh.map.types || {};
  for (const r of sh.robots) if (!types[r.type]) types[r.type] = { color: '#a78bfa', label: r.type, shape: 'square', flying: false };
  RP.plan = new Plan2D($('rpPlan'), $('rpCanvas'), { ...sh.map, types }, { onSelect: () => {}, onDispatch: () => {}, onHover: () => {}, getSelected: () => null });
  RP.plan.active = true; RP.plan.resize();
  await seek(0);
  renderSide();
}
async function loadAround(t) {
  if (RP.loading || (t >= RP.from && t <= RP.to - 2)) return;
  RP.loading = true;
  try {
    const t0 = Math.max(0, Math.floor(t) - 5), r = await api.get(`/api/shifts/${RP.shift.id}/frames?t0=${t0}&t1=${t0 + 300}`);
    RP.frames = r.frames; RP.from = t0; RP.to = r.frames.length ? r.frames[r.frames.length - 1][0] : t0;
  } finally { RP.loading = false; }
}
function frameAt(t) {
  const F = RP.frames; if (!F.length) return null;
  let lo = 0, hi = F.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (F[m][0] <= t) lo = m; else hi = m - 1; }
  const a = F[lo], b = F[Math.min(lo + 1, F.length - 1)], k = b[0] > a[0] ? Math.max(0, Math.min(1, (t - a[0]) / (b[0] - a[0]))) : 0;
  return RP.shift.robots.map((m, i) => {
    const p = a[1][i], q = b[1][i] || p;
    return { id: m.id, type: m.type, model: m.model, x: p[0] + (q[0] - p[0]) * k, y: p[1] + (q[1] - p[1]) * k, state: p[2], battery: p[3], load: p[4], blocked_for: p[5], task: p[6], path: [] };
  });
}
async function seek(t) {
  RP.t = Math.max(0, Math.min(t, RP.shift.sim_end));
  await loadAround(RP.t);
  const robots = frameAt(RP.t);
  if (robots) RP.plan.update({ robots, tasks: [], closed_rows: [] });
  $('rpClock').textContent = fmtT(RP.t); $('rpTime').value = Math.floor(RP.t);
  if (Date.now() - RP.last > 400) { RP.last = Date.now(); renderSide(); }
}
function renderSide() {
  const sh = RP.shift; if (!sh) { $('rpSide').innerHTML = ''; return; }
  document.querySelectorAll('#rpTabs button').forEach(b => b.classList.toggle('on', b.dataset.r === RP.tab));
  const t = RP.t;
  if (RP.tab === 'events') {
    const ev = sh.events.filter(e => e.t <= t).slice(-60).reverse();
    $('rpSide').innerHTML = ev.map(e => `<div class="ev ${e.sev}" data-t="${e.t}"><span class="mono">${fmtT(e.t)}</span><span>${esc(e.msg)}</span></div>`).join('') || '<div class="hint">No events yet at this time.</div>';
  } else if (RP.tab === 'alarms') {
    $('rpSide').innerHTML = sh.alarms.map(a => `<div class="ev ${a.level === 'critical' ? 'error' : a.level === 'info' ? 'info' : 'warn'} ${a.raised > t ? 'future' : ''}" data-t="${a.raised}">
      <span class="mono">${fmtT(a.raised)}</span><span><b>${esc(a.title)}</b> · ${esc(a.detail)}<br><small>${a.acked_by ? 'acknowledged by ' + esc(a.acked_by) + (a.acked_wall ? ' ' + ago(a.acked_wall) : '') : 'not acknowledged'}${a.cleared != null ? ' · cleared ' + fmtT(a.cleared) : ''}</small></span></div>`).join('') || '<div class="hint">No alarms in this shift.</div>';
  } else {
    const done = sh.orders.filter(o => o.finished <= t).slice(-80).reverse();
    $('rpSide').innerHTML = `<div class="hint" style="margin-bottom:6px">${sh.orders.filter(o => o.finished <= t && o.status === 'done').length} done by ${fmtT(t)}</div>` +
      done.map(o => `<div class="ev ${o.status === 'done' ? 'ok' : 'warn'}" data-t="${o.created}"><span class="mono">${fmtT(o.finished)}</span><span>${esc(o.title)}${o.ref ? ` <span class="mono hint">${esc(o.ref)}</span>` : ''} · ${o.status} · ${fmtDur(o.finished - o.created)}</span></div>`).join('');
  }
}
function play() {
  RP.playing = true; $('rpPlay').textContent = 'Pause';
  let prev = performance.now();
  const step = async now => {
    if (!RP.playing) return;
    const dt = (now - prev) / 1000; prev = now;
    if (RP.t >= RP.shift.sim_end) { stopPlay(); return; }
    await seek(RP.t + dt * +$('rpSpeed').value);
    RP.raf = requestAnimationFrame(step);
  };
  RP.raf = requestAnimationFrame(step);
}
function stopPlay() { RP.playing = false; cancelAnimationFrame(RP.raf); $('rpPlay').textContent = 'Play'; }
function bindReplay() {
  $('rpShifts').onclick = e => { const r = e.target.closest('tr[data-s]'); if (r) openShift(+r.dataset.s); };
  $('rpPlay').onclick = () => { if (!RP.shift) return; if (RP.playing) stopPlay(); else { if (RP.t >= RP.shift.sim_end - 1) seek(0); play(); } };
  $('rpTime').oninput = e => { if (RP.shift) seek(+e.target.value); };
  $('rpTabs').onclick = e => { const b = e.target.closest('[data-r]'); if (b) { RP.tab = b.dataset.r; renderSide(); } };
  $('rpSide').onclick = e => { const r = e.target.closest('[data-t]'); if (r && RP.shift) { stopPlay(); seek(+r.dataset.t); } };
}
export function leaveReplay() { stopPlay(); if (RP.plan) RP.plan.active = false; }
export function enterReplay() { if (RP.plan) { RP.plan.active = true; RP.plan.resize(); } }

// ================================================================== boot
export function initStep2() { bindAlarms(); bindMissions(); bindWms(); bindReplay(); }
export function initAuthUi() { bindLogin(); }
