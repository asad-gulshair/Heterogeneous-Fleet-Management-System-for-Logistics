// 2D plan view of the twin (canvas). Also the fallback when WebGL is not available.
const KC = { box_pick: '#2E9FE8', high_pick: '#f5a524', pallet_move: '#e5484d', inventory_scan: '#46a758' };
const KL = { box_pick: 'B', high_pick: 'H', pallet_move: 'P', inventory_scan: 'S', mission: 'M', transport: 'T' };
const batColor = b => b > 40 ? '#3ddc97' : b > 20 ? '#f5a524' : '#ff5d6c';

export class Plan2D {
  constructor(wrap, canvas, map, { onSelect, onDispatch, onHover, getSelected }) {
    Object.assign(this, { wrap, cv: canvas, map, onSelect, onDispatch, onHover, getSelected });
    this.ctx = canvas.getContext('2d'); this.floor = document.createElement('canvas'); this.disp = {}; this.active = false; this.snap = null;
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(wrap);
    canvas.addEventListener('mousemove', e => this._move(e));
    canvas.addEventListener('mouseleave', () => this.onHover && this.onHover(null));
    canvas.addEventListener('click', e => this._click(e));
    this._frame = this._frame.bind(this); this.raf = requestAnimationFrame(this._frame);
  }
  resize() {
    const W = this.map.width, H = this.map.height, aw = this.wrap.clientWidth - 20, ah = this.wrap.clientHeight - 20;
    if (aw <= 0 || ah <= 0) return;
    this.cell = Math.max(6, Math.min(aw / W, ah / H)); this.dpr = window.devicePixelRatio || 1;
    const w = this.cell * W, h = this.cell * H;
    for (const c of [this.cv, this.floor]) { c.width = Math.round(w * this.dpr); c.height = Math.round(h * this.dpr); }
    this.cv.style.width = w + 'px'; this.cv.style.height = h + 'px'; this._drawFloor();
  }
  _drawTraffic(g, c, top) {                              // traffic rules drawn in the Layout Designer (top = waypoints, over racks)
    const T = this.map.traffic || {};
    if (top) return this._drawWaypoints(g, c, T);
    for (const z of T.speed || []) {
      g.fillStyle = 'rgba(245,165,36,.08)'; g.fillRect(z.x * c, z.y * c, z.w * c, z.h * c);
      g.setLineDash([3, 3]); g.strokeStyle = 'rgba(245,165,36,.8)'; g.lineWidth = 1.2; g.strokeRect(z.x * c, z.y * c, z.w * c, z.h * c); g.setLineDash([]);
      g.font = `700 ${Math.max(9, c * .42)}px Inter,sans-serif`; g.fillStyle = '#f5c46b'; g.textBaseline = 'top'; g.fillText(`≤ ${z.max} m/s`, z.x * c + 4, z.y * c + 4);
    }
    for (const l of T.lanes || []) {
      const [dx, dy] = { E: [1, 0], W: [-1, 0], S: [0, 1], N: [0, -1] }[l.dir || 'E'];
      g.fillStyle = 'rgba(34,197,94,.14)'; g.fillRect(l.x * c, l.y * c, l.w * c, l.h * c);
      g.strokeStyle = 'rgba(34,197,94,.85)'; g.lineWidth = Math.max(1.2, c * .07); g.lineCap = 'round'; g.lineJoin = 'round';
      const along = dx ? l.w : l.h, across = dx ? l.h : l.w, k = c * .28;
      for (let i = 1; i < along; i += 2) for (let j = 0; j < across; j++) {
        const ax = (dx ? l.x + i : l.x + j + .5) * c, ay = (dx ? l.y + j + .5 : l.y + i) * c;
        g.beginPath(); g.moveTo(ax - dx * k + dy * k, ay - dy * k + dx * k); g.lineTo(ax + dx * k, ay + dy * k); g.lineTo(ax - dx * k - dy * k, ay - dy * k - dx * k); g.stroke();
      }
    }
    for (const z of T.single || []) {
      g.fillStyle = 'rgba(167,139,250,.13)'; g.fillRect(z.x * c, z.y * c, z.w * c, z.h * c);
      g.strokeStyle = '#a78bfa'; g.lineWidth = 2; g.strokeRect(z.x * c + 1, z.y * c + 1, z.w * c - 2, z.h * c - 2);
      if (z.w * c > 26) { g.font = `700 ${Math.max(8, c * .38)}px Inter,sans-serif`; g.fillStyle = '#cdbcff'; g.textBaseline = 'top'; g.fillText((z.name || '1 robot').toUpperCase(), z.x * c + 3, z.y * c + 3); }
    }
  }
  _drawWaypoints(g, c, T) {
    for (const [name, [x, y]] of Object.entries(T.waypoints || {})) {
      const cx = (x + .5) * c, cy = (y + .5) * c, r = c * .36;
      g.fillStyle = '#38bdf8'; g.beginPath(); g.moveTo(cx, cy - r); g.lineTo(cx + r, cy); g.lineTo(cx, cy + r); g.lineTo(cx - r, cy); g.closePath(); g.fill();
      g.font = `700 ${Math.max(8, c * .4)}px Inter,sans-serif`; g.fillStyle = '#9fdcfb'; g.textBaseline = 'bottom'; g.textAlign = 'center'; g.fillText(name, cx, cy - r - 1); g.textAlign = 'left';
    }
  }
  _rr(g, x, y, w, h, r) { g.beginPath(); g.roundRect ? g.roundRect(x, y, w, h, r) : g.rect(x, y, w, h); }
  _drawFloor() {
    const g = this.floor.getContext('2d'), c = this.cell, M = this.map, W = M.width * c, H = M.height * c;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const bg = g.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, '#0a1a3c'); bg.addColorStop(1, '#08132e'); g.fillStyle = bg; g.fillRect(0, 0, W, H);
    for (let x = 0; x <= M.width; x++) { g.strokeStyle = x % 5 ? 'rgba(120,160,255,.045)' : 'rgba(120,160,255,.11)'; g.beginPath(); g.moveTo(x * c, 0); g.lineTo(x * c, H); g.stroke(); }
    for (let y = 0; y <= M.height; y++) { g.strokeStyle = y % 5 ? 'rgba(120,160,255,.045)' : 'rgba(120,160,255,.11)'; g.beginPath(); g.moveTo(0, y * c); g.lineTo(W, y * c); g.stroke(); }
    const TN = { pallet_area: 'PALLET AREA', packing: 'PACKING', dock: 'LOADING DOCK', charging: 'CHARGING' }, cnt = {}, tot = {}, names = {};
    for (const z of Object.values(M.zones)) tot[z.type] = (tot[z.type] || 0) + 1;
    for (const [k, z] of Object.entries(M.zones).sort((a, b) => a[1].y - b[1].y || a[1].x - b[1].x)) {
      cnt[z.type] = (cnt[z.type] || 0) + 1; names[k] = (TN[z.type] || k.toUpperCase()) + (z.type === 'packing' || tot[z.type] > 1 ? ' ' + cnt[z.type] : '');
    }
    for (const n of M.nogo || []) {                       // no-go zones: hatched red
      g.save(); g.beginPath(); g.rect(n.x * c, n.y * c, n.w * c, n.h * c); g.clip();
      g.fillStyle = 'rgba(229,72,77,.14)'; g.fillRect(n.x * c, n.y * c, n.w * c, n.h * c);
      g.strokeStyle = 'rgba(229,72,77,.45)'; g.lineWidth = 2;
      for (let i = -n.h * c; i < n.w * c; i += 10) { g.beginPath(); g.moveTo(n.x * c + i, (n.y + n.h) * c); g.lineTo(n.x * c + i + n.h * c, n.y * c); g.stroke(); }
      g.restore(); g.strokeStyle = '#e5484d'; g.setLineDash([5, 4]); g.strokeRect(n.x * c, n.y * c, n.w * c, n.h * c); g.setLineDash([]);
      if (n.name && n.w * c > 40) { g.font = `700 ${Math.max(9, c * .45)}px Inter,sans-serif`; g.fillStyle = '#ffb3ba'; g.textBaseline = 'top'; g.fillText(n.name.toUpperCase(), n.x * c + 5, n.y * c + 5); }
    }
    for (const w of M.walls || []) { g.fillStyle = '#5b6f99'; g.fillRect(w.x * c, w.y * c, w.w * c, w.h * c); }
    this._drawTraffic(g, c, false);
    for (const [k, z] of Object.entries(M.zones)) {
      g.fillStyle = z.color + '33'; this._rr(g, z.x * c, z.y * c, z.w * c, z.h * c, 6); g.fill();
      g.setLineDash([5, 4]); g.strokeStyle = z.color + 'cc'; g.lineWidth = 1.2; g.stroke(); g.setLineDash([]);
      g.font = `700 ${Math.max(9, c * .5)}px Inter,sans-serif`; g.fillStyle = '#c7d8f5'; g.textBaseline = 'top'; g.fillText(names[k] || k.toUpperCase(), z.x * c + 7, z.y * c + 6);
    }
    for (const [x, y] of M.chargers || []) { g.fillStyle = '#2E9FE8'; this._rr(g, x * c + c * .15, y * c + c * .72, c * .7, c * .2, 2); g.fill(); }
    [...M.shelves].sort((a, b) => a.y - b.y).forEach((s, i) => {
      const x = s.x * c, y = s.y * c, w = s.w * c, h = s.h * c;
      g.fillStyle = '#16295a'; this._rr(g, x, y, w, h, 4); g.fill(); g.strokeStyle = '#2a4a93'; g.lineWidth = 1; g.stroke();
      const hz = (s.orient || (s.w >= s.h ? 'h' : 'v')) === 'h';
      g.strokeStyle = 'rgba(120,170,255,.28)';
      if (hz) for (let bx = s.x + 2; bx < s.x + s.w; bx += 2) { g.beginPath(); g.moveTo(bx * c, y + 2); g.lineTo(bx * c, y + h - 2); g.stroke(); }
      else for (let by = s.y + 2; by < s.y + s.h; by += 2) { g.beginPath(); g.moveTo(x + 2, by * c); g.lineTo(x + w - 2, by * c); g.stroke(); }
      g.font = `700 ${Math.max(9, c * .5)}px Inter,sans-serif`; g.fillStyle = '#6f8fc9'; g.textBaseline = 'middle';
      if (hz) { g.textAlign = 'right'; g.fillText(s.name || 'A' + (i + 1), x - 6, y + h / 2); } else { g.textAlign = 'center'; g.fillText(s.name || 'A' + (i + 1), x + w / 2, y - c * .5); }
      g.textAlign = 'left';
    });
    this._drawTraffic(g, c, true);
  }
  update(snap) { this.snap = snap; }
  setHeat(data) { this.heatData = data; }
  _frame() {
    this.raf = requestAnimationFrame(this._frame);
    if (!this.active || !this.snap || !this.cell) return;
    const g = this.ctx, c = this.cell, S = this.snap, types = this.map.types, sel = this.getSelected();
    g.setTransform(1, 0, 0, 1, 0, 0); g.drawImage(this.floor, 0, 0); g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.heatData) {                                   // congestion heatmap: where robots wait for each other
      const mx = Math.max(1, this.heatData.max);
      for (const [x, y, v] of this.heatData.cells) { const k = Math.log1p(v) / Math.log1p(mx); g.fillStyle = `hsla(${58 - 58 * k},100%,50%,.55)`; g.fillRect(x * c, y * c, c, c); }
    }
    for (const row of S.closed_rows || []) {               // aisle closed for maintenance
      const a = (this.map.aisles || [])[row]; if (!a) continue;
      g.save(); g.fillStyle = 'rgba(245,197,66,.22)'; g.fillRect(a[0] * c, a[1] * c, a[2] * c, a[3] * c);
      g.strokeStyle = '#f5c542'; g.setLineDash([6, 4]); g.lineWidth = 2; g.strokeRect(a[0] * c, a[1] * c, a[2] * c, a[3] * c);
      g.fillStyle = '#f5c542'; g.font = `700 ${Math.max(9, c * .55)}px Inter,sans-serif`; g.textBaseline = 'middle'; g.fillText(`${(this.map.shelves[row] && this.map.shelves[row].name) || 'A' + (row + 1)} · MAINTENANCE`, a[0] * c + 6, (a[1] + a[3] / 2) * c); g.restore();
    }
    for (const t of S.tasks) if (t.status === 'queued') {
      const x = (t.pickup[0] + .5) * c, y = (t.pickup[1] + .5) * c, R = Math.max(5, c * .32);
      g.fillStyle = KC[t.kind] + '33'; g.strokeStyle = KC[t.kind]; g.lineWidth = 1.3; this._rr(g, x - R, y - R, R * 2, R * 2, 3); g.fill(); g.stroke();
      g.fillStyle = KC[t.kind]; g.font = `700 ${Math.max(8, c * .42)}px Inter,sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(KL[t.kind], x, y + .5); g.textAlign = 'left';
    }
    for (const r of S.robots) { const d = this.disp[r.id] || (this.disp[r.id] = { x: r.x, y: r.y }); d.x += (r.x - d.x) * .35; d.y += (r.y - d.y) * .35; }
    for (const r of S.robots) if (r.path.length) {
      const d = this.disp[r.id], strong = r.id === sel; g.save(); g.strokeStyle = types[r.type].color; g.globalAlpha = strong ? .9 : .28; g.lineWidth = strong ? 2.2 : 1.2; g.setLineDash(strong ? [6, 5] : [3, 5]);
      g.beginPath(); g.moveTo((d.x + .5) * c, (d.y + .5) * c); for (const p of r.path) g.lineTo((p[0] + .5) * c, (p[1] + .5) * c); g.stroke(); g.restore();
    }
    const order = [...S.robots].sort((a, b) => types[a.type].flying - types[b.type].flying);
    for (const r of order) this._robot(r, this.disp[r.id], sel);
  }
  _robot(r, d, sel) {
    const g = this.ctx, c = this.cell, t = this.map.types[r.type], cx = (d.x + .5) * c, cy = (d.y + .5) * c, R = Math.max(6, c * .46);
    g.save();
    const gl = g.createRadialGradient(cx, cy, R * .4, cx, cy, R * 2.2); gl.addColorStop(0, t.color + '55'); gl.addColorStop(1, t.color + '00'); g.fillStyle = gl; g.beginPath(); g.arc(cx, cy, R * 2.2, 0, 7); g.fill();
    g.fillStyle = t.color; g.strokeStyle = '#04101f'; g.lineWidth = 1.5;
    if (t.shape === 'circle') { g.beginPath(); g.arc(cx, cy, R, 0, 7); g.fill(); g.stroke(); }
    else if (t.shape === 'square') { this._rr(g, cx - R, cy - R, R * 2, R * 2, R * .35); g.fill(); g.stroke(); }
    else if (t.shape === 'triangle') { g.beginPath(); g.moveTo(cx, cy - R * 1.1); g.lineTo(cx + R * 1.05, cy + R * .9); g.lineTo(cx - R * 1.05, cy + R * .9); g.closePath(); g.fill(); g.stroke(); }
    else { g.beginPath(); g.arc(cx, cy, R * .55, 0, 7); g.fill(); g.stroke(); for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) { g.beginPath(); g.arc(cx + dx * R * .95, cy + dy * R * .95, R * .38, 0, 7); g.fillStyle = t.color + 'aa'; g.fill(); g.stroke(); } }
    g.lineWidth = 2.2; g.strokeStyle = 'rgba(255,255,255,.14)'; g.beginPath(); g.arc(cx, cy, R + 4, 0, 7); g.stroke();
    g.strokeStyle = batColor(r.battery); g.beginPath(); g.arc(cx, cy, R + 4, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * r.battery / 100); g.stroke();
    if (r.state === 'fault') { const p = .6 + .4 * Math.sin(Date.now() / 180); g.strokeStyle = `rgba(255,93,108,${p})`; g.lineWidth = 2.4; g.beginPath(); g.arc(cx, cy, R + 7, 0, 7); g.stroke(); }
    else if (r.blocked_for >= 1 && r.state === 'moving') { g.fillStyle = '#f5a524'; g.beginPath(); g.arc(cx + R, cy - R, Math.max(2.5, c * .18), 0, 7); g.fill(); }
    if (r.id === sel) { g.strokeStyle = '#fff'; g.lineWidth = 1.6; g.beginPath(); g.arc(cx, cy, R + 9, 0, 7); g.stroke(); }
    g.restore();
  }
  _pt(e) { const r = this.cv.getBoundingClientRect(); return { fx: (e.clientX - r.left) / this.cell, fy: (e.clientY - r.top) / this.cell, px: e.clientX - this.wrap.getBoundingClientRect().left, py: e.clientY - this.wrap.getBoundingClientRect().top }; }
  _pick(fx, fy) { return this.snap && this.snap.robots.find(r => { const d = this.disp[r.id] || r; return Math.hypot(d.x + .5 - fx, d.y + .5 - fy) < .9; }); }
  _move(e) { const { fx, fy, px, py } = this._pt(e); const h = this._pick(fx, fy); this.onHover && this.onHover(h || null, px, py); }
  _click(e) {
    const { fx, fy } = this._pt(e), h = this._pick(fx, fy);
    if (h) { this.onSelect(h.id); return; }
    this.onDispatch(Math.floor(fx), Math.floor(fy));
  }
  dispose() { cancelAnimationFrame(this.raf); this.ro.disconnect(); }
}
