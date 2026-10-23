// 2D plan view of the twin (canvas). Also the fallback when WebGL is not available.
const KC = { box_pick: '#2E9FE8', high_pick: '#f5a524', pallet_move: '#e5484d', inventory_scan: '#46a758' };
const KL = { box_pick: 'B', high_pick: 'H', pallet_move: 'P', inventory_scan: 'S' };
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
  _rr(g, x, y, w, h, r) { g.beginPath(); g.roundRect ? g.roundRect(x, y, w, h, r) : g.rect(x, y, w, h); }
  _drawFloor() {
    const g = this.floor.getContext('2d'), c = this.cell, M = this.map, W = M.width * c, H = M.height * c;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const bg = g.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, '#0a1a3c'); bg.addColorStop(1, '#08132e'); g.fillStyle = bg; g.fillRect(0, 0, W, H);
    for (let x = 0; x <= M.width; x++) { g.strokeStyle = x % 5 ? 'rgba(120,160,255,.045)' : 'rgba(120,160,255,.11)'; g.beginPath(); g.moveTo(x * c, 0); g.lineTo(x * c, H); g.stroke(); }
    for (let y = 0; y <= M.height; y++) { g.strokeStyle = y % 5 ? 'rgba(120,160,255,.045)' : 'rgba(120,160,255,.11)'; g.beginPath(); g.moveTo(0, y * c); g.lineTo(W, y * c); g.stroke(); }
    const names = { pallet_area: 'PALLET AREA', packing_1: 'PACKING 1', packing_2: 'PACKING 2', loading_dock: 'LOADING DOCK', charging_area: 'CHARGING' };
    for (const [k, z] of Object.entries(M.zones)) {
      g.fillStyle = z.color + '33'; this._rr(g, z.x * c, z.y * c, z.w * c, z.h * c, 6); g.fill();
      g.setLineDash([5, 4]); g.strokeStyle = z.color + 'cc'; g.lineWidth = 1.2; g.stroke(); g.setLineDash([]);
      g.font = `700 ${Math.max(9, c * .5)}px Inter,sans-serif`; g.fillStyle = '#c7d8f5'; g.textBaseline = 'top'; g.fillText(names[k] || k.toUpperCase(), z.x * c + 7, z.y * c + 6);
    }
    for (const [x, y] of M.chargers || []) { g.fillStyle = '#2E9FE8'; this._rr(g, x * c + c * .15, y * c + c * .72, c * .7, c * .2, 2); g.fill(); }
    [...M.shelves].sort((a, b) => a.y - b.y).forEach((s, i) => {
      const x = s.x * c, y = s.y * c, w = s.w * c, h = s.h * c;
      g.fillStyle = '#16295a'; this._rr(g, x, y, w, h, 4); g.fill(); g.strokeStyle = '#2a4a93'; g.lineWidth = 1; g.stroke();
      g.strokeStyle = 'rgba(120,170,255,.28)'; for (let bx = s.x + 2; bx < s.x + s.w; bx += 2) { g.beginPath(); g.moveTo(bx * c, y + 2); g.lineTo(bx * c, y + h - 2); g.stroke(); }
      g.font = `700 ${Math.max(9, c * .5)}px Inter,sans-serif`; g.fillStyle = '#6f8fc9'; g.textBaseline = 'middle'; g.textAlign = 'right'; g.fillText('A' + (i + 1), x - 6, y + h / 2); g.textAlign = 'left';
    });
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
      g.fillStyle = '#f5c542'; g.font = `700 ${Math.max(9, c * .55)}px Inter,sans-serif`; g.textBaseline = 'middle'; g.fillText(`A${row + 1} · MAINTENANCE`, a[0] * c + 6, (a[1] + a[3] / 2) * c); g.restore();
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
