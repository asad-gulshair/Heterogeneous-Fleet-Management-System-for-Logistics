// TusCore 3D digital twin (Three.js). 1 grid cell = 1 metre. World X = grid x, world Z = grid y, Y = up.
import * as THREE from 'three';
import { OrbitControls } from '../vendor/OrbitControls.js';

const RACK_H = 3.6, LEVELS = [0.12, 1.0, 1.9, 2.8], DRONE_ALT = 4.9, LIFT_TOP = 2.1, VIS = 1.35; // VIS: robots drawn 35% larger so they read at overview distance
const LETTER = { tote_amr: 'T', scissor_lift: 'L', pallet_stacker: 'P', drone: 'D' };
const STATE_COL = { idle: '#7f93b5', moving: '#6cc4ff', working: '#f5c76a', waiting: '#c9a7ff', to_charge: '#3ddc97', charging: '#3ddc97', fault: '#ff5d6c' };
const KIND_COL = { box_pick: '#2E9FE8', high_pick: '#f5a524', pallet_move: '#e5484d', inventory_scan: '#46a758' };

function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const std = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.25, ...o });
const glow = (color, i = 1.4) => new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: i, roughness: 0.4 });
function box(w, h, d, mat, x = 0, y = 0, z = 0, shadow = true) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z);
  m.castShadow = shadow; m.receiveShadow = shadow; return m;
}
function cyl(rt, rb, h, mat, seg = 20) { const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat); m.castShadow = true; return m; }
function textTexture(text, { size = 64, color = '#c7d8f5', bg = null, font = '700', pad = 16 } = {}) {
  const c = document.createElement('canvas'), g = c.getContext('2d');
  g.font = `${font} ${size}px Inter, Segoe UI, sans-serif`;
  const w = Math.ceil(g.measureText(text).width) + pad * 2, h = size + pad * 2;
  c.width = w; c.height = h;
  if (bg) { g.fillStyle = bg; g.fillRect(0, 0, w, h); }
  g.font = `${font} ${size}px Inter, Segoe UI, sans-serif`; g.fillStyle = color; g.textBaseline = 'middle'; g.fillText(text, pad, h / 2);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return { tex: t, aspect: w / h };
}
function floorLabel(text, width, color) {
  const { tex, aspect } = textTexture(text, { size: 72, color });
  const m = new THREE.Mesh(new THREE.PlaneGeometry(width, width / aspect), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; return m;
}

export class Twin3D {
  constructor(el, map, { onSelect, onHover } = {}) {
    this.el = el; this.map = map; this.types = map.types; this.onSelect = onSelect; this.onHover = onHover;
    this.robots = new Map(); this.markers = new Map(); this.selected = null; this.follow = false; this.active = true;
    this.flyers = []; this.restock = []; this.slots = new Map(); this.waitPallets = new Map(); this.lifted = new Set();
    this.dockPallets = []; this.transferBoxes = []; this.conveyor = []; this.seqs = []; this.tunnels = []; this.closedObjs = new Map(); this.scale = 1;
    this.dwell = map.dwell_s || { pick: 3, drop: 2, high_pick: 6, handoff: 2, pallet_pick: 4, pallet_drop: 4, scan: 1 };
    this.W = map.width; this.H = map.height; this.cx = this.W / 2; this.cz = this.H / 2;

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.shadowMap.autoUpdate = false;            // shadows are refreshed every few frames (see _loop) - big saving on laptops
    this.frame = 0; this.fpsT = 0; this.fpsN = 0; this.lowQ = false;
    r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.1;
    el.appendChild(r.domElement);

    const s = this.scene = new THREE.Scene();
    s.background = new THREE.Color('#070f24'); s.fog = new THREE.Fog('#070f24', 70, 190);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 600);
    this.controls = new OrbitControls(this.camera, r.domElement);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.08, maxPolarAngle: 1.47, minDistance: 5, maxDistance: 170, autoRotateSpeed: 0.55 });

    s.add(new THREE.HemisphereLight('#c9dcff', '#0b1630', 1.05));
    const sun = this.sun = new THREE.DirectionalLight('#ffffff', 1.6);
    sun.position.set(this.cx - 25, 55, this.cz - 35); sun.target.position.set(this.cx, 0, this.cz);
    sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
    const sc = sun.shadow.camera, ext = Math.max(this.W, this.H) * 0.75;
    Object.assign(sc, { left: -ext, right: ext, top: ext, bottom: -ext, near: 1, far: 160 }); sun.shadow.bias = -0.0005;
    s.add(sun, sun.target);
    const fill = new THREE.DirectionalLight('#7fb5ff', 0.35); fill.position.set(this.cx + 30, 25, this.cz + 40); s.add(fill);

    this._buildFloor(); this._buildZones(); this._buildRacks(); this._buildPalletDecor(); this._buildPacking();
    this._buildChargers(); this._buildWalls(); this._buildHeat();
    this.view('overview');

    this.ray = new THREE.Raycaster(); this.ptr = new THREE.Vector2();
    r.domElement.addEventListener('pointermove', e => this._hover(e));
    r.domElement.addEventListener('pointerleave', () => this.onHover && this.onHover(null));
    let down = null;
    r.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    r.domElement.addEventListener('pointerup', e => { if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) this._click(e); down = null; });

    this.tag = document.createElement('div'); this.tag.className = 'tag3d'; this.tag.style.display = 'none'; el.appendChild(this.tag);
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(el); this.resize();
    this.clock = new THREE.Clock(); this._loop = this._loop.bind(this); this.raf = requestAnimationFrame(this._loop);
  }

  // ------------------------------------------------------------------ static warehouse
  _buildFloor() {
    const { W, H } = this, px = 24, c = document.createElement('canvas'), g = c.getContext('2d');
    c.width = W * px; c.height = H * px;
    g.fillStyle = '#0d1d42'; g.fillRect(0, 0, c.width, c.height);
    for (let x = 0; x <= W; x++) { g.strokeStyle = x % 5 ? 'rgba(130,170,255,.07)' : 'rgba(130,170,255,.16)'; g.lineWidth = x % 5 ? 1 : 2; g.beginPath(); g.moveTo(x * px, 0); g.lineTo(x * px, c.height); g.stroke(); }
    for (let y = 0; y <= H; y++) { g.strokeStyle = y % 5 ? 'rgba(130,170,255,.07)' : 'rgba(130,170,255,.16)'; g.lineWidth = y % 5 ? 1 : 2; g.beginPath(); g.moveTo(0, y * px); g.lineTo(c.width, y * px); g.stroke(); }
    // yellow safety lines along the rack fronts
    g.strokeStyle = 'rgba(245,197,66,.55)'; g.lineWidth = 3; g.setLineDash([px * 0.6, px * 0.4]);
    for (const s of this.map.shelves) { g.strokeRect(s.x * px - 4, s.y * px - 4, s.w * px + 8, s.h * px + 8); }
    g.setLineDash([]);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.85, metalness: 0.08 }));
    floor.rotation.x = -Math.PI / 2; floor.position.set(this.cx, 0, this.cz); floor.receiveShadow = true; this.scene.add(floor);
    const apron = new THREE.Mesh(new THREE.PlaneGeometry(W + 60, H + 60), new THREE.MeshStandardMaterial({ color: '#081430', roughness: 1 }));
    apron.rotation.x = -Math.PI / 2; apron.position.set(this.cx, -0.02, this.cz); apron.receiveShadow = true; this.scene.add(apron);
  }

  _buildZones() {
    const names = { pallet_area: 'PALLET AREA', packing_1: 'PACKING 1', packing_2: 'PACKING 2', loading_dock: 'LOADING DOCK', charging_area: 'CHARGING' };
    for (const [k, z] of Object.entries(this.map.zones)) {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(z.w, z.h), new THREE.MeshStandardMaterial({ color: z.color, transparent: true, opacity: 0.2, roughness: 0.9 }));
      p.rotation.x = -Math.PI / 2; p.position.set(z.x + z.w / 2, 0.01, z.y + z.h / 2); p.receiveShadow = true; this.scene.add(p);
      const pts = [[z.x, z.y], [z.x + z.w, z.y], [z.x + z.w, z.y + z.h], [z.x, z.y + z.h], [z.x, z.y]].map(([x, y]) => new THREE.Vector3(x, 0.02, y));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: z.color, dashSize: 0.5, gapSize: 0.3 }));
      line.computeLineDistances(); this.scene.add(line);
      const lab = floorLabel(names[k] || k.toUpperCase(), Math.min(z.w - 0.6, 5.5), '#d4e3fb');
      const h = lab.geometry.parameters.height; lab.position.set(z.x + 0.4 + lab.geometry.parameters.width / 2, 0.03, z.y + z.h - 0.3 - h / 2); this.scene.add(lab);
    }
  }

  _buildRacks() {
    const shelves = [...this.map.shelves].sort((a, b) => a.y - b.y), rand = rng(7);
    const ups = [], beams = [], decks = [], boxes = [];
    shelves.forEach((s, i) => {
      for (let x = s.x; x <= s.x + s.w; x += 2) for (const z of [s.y + 0.05, s.y + s.h - 0.05]) ups.push([x, RACK_H / 2, z]);
      for (const lv of LEVELS) {
        decks.push([s.x + s.w / 2, lv, s.y + s.h / 2, s.w, s.h - 0.1]);
        for (const z of [s.y + 0.05, s.y + s.h - 0.05]) beams.push([s.x + s.w / 2, lv + 0.06, z, s.w]);
        for (let x = s.x; x < s.x + s.w; x++) for (let z = s.y; z < s.y + s.h; z++) if (rand() < 0.74) {
          const w = 0.55 + rand() * 0.3, h = 0.32 + rand() * (lv > 2.5 ? 0.45 : 0.55), d = 0.55 + rand() * 0.3;
          const r = rand(), col = r < 0.12 ? '#2f6fd6' : r < 0.2 ? '#d6dce6' : ['#b98c5a', '#a87b4b', '#c89b67', '#9c7246'][Math.floor(rand() * 4)];
          boxes.push([x + 0.5, lv + 0.03 + h / 2, z + 0.5, w, h, d, col, `${x}|${z}|${LEVELS.indexOf(lv)}`]);
        }
      }
      const lab = floorLabel('A' + (i + 1), 1.4, '#7fa2e0'); lab.position.set(s.x - 1.3, 0.03, s.y + s.h / 2); this.scene.add(lab);
    });
    const inst = (geo, mat, list, place, shadow = true) => {
      const m = new THREE.InstancedMesh(geo, mat, list.length), o = new THREE.Object3D(), c = new THREE.Color();
      list.forEach((it, i) => { place(o, it); o.updateMatrix(); m.setMatrixAt(i, o.matrix); if (it[6]) m.setColorAt(i, c.set(it[6])); });
      m.castShadow = shadow; m.receiveShadow = true; this.scene.add(m); return m;
    };
    inst(new THREE.BoxGeometry(0.09, RACK_H, 0.09), std('#4b6cc0', { metalness: 0.5 }), ups, (o, p) => { o.position.set(...p); o.scale.set(1, 1, 1); });
    inst(new THREE.BoxGeometry(1, 0.11, 0.06), std('#e8742a', { metalness: 0.4 }), beams, (o, p) => { o.position.set(p[0], p[1], p[2]); o.scale.set(p[3], 1, 1); });
    inst(new THREE.BoxGeometry(1, 0.03, 1), std('#6b7c9c', { metalness: 0.5 }), decks, (o, p) => { o.position.set(p[0], p[1], p[2]); o.scale.set(p[3], 1, p[4]); });
    this.boxMesh = inst(new THREE.BoxGeometry(1, 1, 1), std('#ffffff', { roughness: 0.85, metalness: 0 }), boxes,
      (o, p) => { o.position.set(p[0], p[1], p[2]); o.scale.set(p[3], p[4], p[5]); o.rotation.y = 0; }, false);
    boxes.forEach((b, i) => { const m = new THREE.Matrix4(); this.boxMesh.getMatrixAt(i, m); this.slots.set(b[7], { i, m, col: b[6], size: [b[3], b[4], b[5]], pos: new THREE.Vector3(b[0], b[1], b[2]), taken: false }); });
    this.shelvesSorted = shelves;
  }

  _buildPalletDecor() {
    // Pallets that are WAITING to be moved are drawn dynamically from the live order list (see _syncPallets).
    // Here only a static row of stored stock along the back edge of the pallet area.
    const z = this.map.zones.pallet_area; if (!z) return;
    const rand = rng(11);
    for (let x = z.x + 0.5; x < z.x + z.w - 0.5; x += 1.6) {
      this.scene.add(box(0.95, 0.14, 0.95, std('#9b6b3c', { metalness: 0, roughness: 0.9 }), x + 0.3, 0.07, z.y + 0.6));
      const h = 0.6 + rand() * 1.1;
      this.scene.add(box(0.86, h, 0.86, std(rand() < 0.5 ? '#cfd8e6' : '#b98c5a', { metalness: 0, roughness: 0.6 }), x + 0.3, 0.14 + h / 2, z.y + 0.6));
    }
  }

  _buildPacking() {
    // infeed conveyor with rollers -> carton rides into the pack & ship tunnel at the far end and leaves the twin there
    const roller = std('#9aa6bb', { metalness: 0.8, roughness: 0.3 }), frame = std('#33476f'), curtain = std('#0b1326', { roughness: 0.9, transparent: true, opacity: 0.85 });
    for (const k of Object.keys(this.map.zones).filter(k => k.startsWith('packing'))) {
      const z = this.map.zones[k], g = new THREE.Group(), x0 = z.x + 0.5, x1 = z.x + z.w - 0.5, bz = z.y + 0.6;
      g.add(box(z.w - 1, 0.85, 0.8, frame, z.x + z.w / 2, 0.425, bz));
      g.add(box(z.w - 1, 0.05, 0.06, std('#cdd6e6', { metalness: 0.6 }), z.x + z.w / 2, 0.9, bz - 0.38));
      g.add(box(z.w - 1, 0.05, 0.06, std('#cdd6e6', { metalness: 0.6 }), z.x + z.w / 2, 0.9, bz + 0.38));
      for (let x = x0 + 0.12; x < x1 - 0.05; x += 0.24) { const r = cyl(0.035, 0.035, 0.7, roller, 10); r.rotation.x = Math.PI / 2; r.position.set(x, 0.9, bz); r.castShadow = false; g.add(r); }
      // pack & ship tunnel (carton is taped, labelled and sent to shipping inside)
      const tx = x1 - 0.6, tw = 1.1, th = 0.8;
      g.add(box(tw, th, 0.06, std('#1f3460'), tx, 0.95 + th / 2, bz - 0.44));
      g.add(box(tw, th, 0.06, std('#1f3460'), tx, 0.95 + th / 2, bz + 0.44));
      g.add(box(tw, 0.08, 0.94, std('#26407a'), tx, 0.95 + th + 0.04, bz));
      g.add(box(tw - 0.1, 0.03, 0.05, glow('#2E9FE8', 1.4), tx, 0.95 + th + 0.09, bz + 0.47, false));
      for (let i = 0; i < 7; i++) g.add(box(0.015, th - 0.05, 0.11, curtain, tx - tw / 2 + 0.01, 0.95 + th / 2 + 0.02, bz - 0.33 + i * 0.11, false));
      const lab = textTexture('PACK & SHIP', { size: 54, color: '#e8f2ff' });
      const sign = new THREE.Mesh(new THREE.PlaneGeometry(0.95, 0.95 / lab.aspect), new THREE.MeshBasicMaterial({ map: lab.tex, transparent: true }));
      sign.position.set(tx, 0.95 + th * 0.55, bz + 0.475); g.add(sign);
      this.tunnels.push({ zone: z, entry: tx - tw / 2, bz });
      const mon = box(0.5, 0.32, 0.04, glow('#2E9FE8', 0.6), z.x + 1.1, 1.35, z.y + 0.18); g.add(mon);
      g.add(box(0.05, 0.45, 0.05, frame, z.x + 1.1, 1.0, z.y + 0.18));
      this.scene.add(g);
    }
  }

  _buildChargers() {
    const padMat = glow('#2E9FE8', 0.35), postMat = std('#1b2a4d'), lightMat = glow('#6cc4ff', 1.6);
    for (const [x, y] of this.map.chargers || []) {
      const pad = new THREE.Mesh(new THREE.PlaneGeometry(0.82, 0.82), padMat); pad.rotation.x = -Math.PI / 2; pad.position.set(x + 0.5, 0.015, y + 0.5); this.scene.add(pad);
      this.scene.add(box(0.26, 0.95, 0.2, postMat, x + 0.5, 0.475, y + 0.95));
      this.scene.add(box(0.2, 0.06, 0.02, lightMat, x + 0.5, 0.78, y + 0.84, false));
    }
  }

  _buildWalls() {
    const { W, H } = this, wall = std('#0e1f48', { roughness: 0.9, metalness: 0.1 }), strip = glow('#2E9FE8', 1.2);
    this.scene.add(box(W, 6, 0.3, wall, this.cx, 3, -0.15));          // north wall
    this.scene.add(box(0.3, 6, H, wall, -0.15, 3, this.cz));            // west wall
    this.scene.add(box(W, 0.08, 0.05, strip, this.cx, 5.7, 0.03, false));
    this.scene.add(box(0.05, 0.08, H, strip, 0.03, 5.7, this.cz, false));
    for (const [x, z, w, d] of [[this.cx, H, W, 0.15], [W, this.cz, 0.15, H]]) this.scene.add(box(w, 0.35, d, std('#f5c542', { roughness: 0.6 }), x, 0.175, z));
    // dock doors on the wall nearest to the loading dock
    const dz = this.map.zones.loading_dock;
    if (dz) {
      const east = dz.x + dz.w >= W - 3;
      if (east) {
        this.scene.add(box(0.3, 6, dz.h + 2, wall, W + 0.15, 3, dz.y + dz.h / 2));
        // a trailer backed up to the dock (pallets set down at the dock are 'loaded' into it)
        this.truck = new THREE.Group();
        this.truck.add(box(9, 3.2, 2.5, std('#d9dee8', { metalness: 0.3, roughness: 0.5 }), 4.7, 1.9, 0));
        this.truck.add(box(9.1, 0.25, 2.6, std('#2E9FE8'), 4.7, 0.5, 0));
        for (const x of [6.5, 7.6]) for (const zz of [-1.15, 1.15]) { const w = cyl(0.45, 0.45, 0.3, std('#111827')); w.rotation.x = Math.PI / 2; w.position.set(x, 0.45, zz); this.truck.add(w); }
        const { tex: tt, aspect: ta } = textTexture('TusCore Logistics', { size: 80, color: '#0A1A3C' });
        const side = new THREE.Mesh(new THREE.PlaneGeometry(5, 5 / ta), new THREE.MeshBasicMaterial({ map: tt, transparent: true })); side.position.set(4.7, 2.2, 1.27); this.truck.add(side);
        this.truckHome = new THREE.Vector3(W + 0.4, 0, dz.y + 1 + 1); this.truck.position.copy(this.truckHome); this.scene.add(this.truck);
        for (let y = dz.y + 1; y < dz.y + dz.h - 1; y += 3) {
          this.scene.add(box(0.06, 3.2, 2.2, std('#26324d', { metalness: 0.6 }), W - 0.02, 1.6, y + 1));
          this.scene.add(box(0.1, 0.35, 2.4, std('#f5c542'), W - 0.05, 0.2, y + 1));
        }
      }
    }
    // branding on the north wall
    const { tex, aspect } = textTexture('TusCore  ·  powered by Tusmec', { size: 96, color: '#6cc4ff' });
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(14, 14 / aspect), new THREE.MeshBasicMaterial({ map: tex, transparent: true }));
    sign.position.set(this.cx, 4.2, 0.02); this.scene.add(sign);
  }

  // ------------------------------------------------------------------ robots
  _makeRobot(r) {
    const t = this.types[r.type], col = t.color, g = new THREE.Group(), p = { kind: r.type };
    const bodyMat = std(col, { roughness: 0.42, metalness: 0.35 }), dark = std('#151c2c', { roughness: 0.7 });
    p.led = glow(STATE_COL.idle, 1.8);
    if (r.type === 'tote_amr') {
      g.add(box(0.84, 0.08, 0.66, dark, 0, 0.06, 0));
      g.add(box(0.78, 0.2, 0.6, bodyMat, 0, 0.18, 0));
      g.add(box(0.7, 0.03, 0.52, std('#2a3550'), 0, 0.295, 0));
      const lid = cyl(0.07, 0.07, 0.09, dark); lid.position.set(0.3, 0.35, 0); g.add(lid);
      for (const z of [-0.2, 0.2]) g.add(box(0.02, 0.05, 0.12, p.led, 0.395, 0.18, z, false));
      const binMat = std('#2f6fd6', { roughness: 0.5 });
      g.add(box(0.62, 0.04, 0.48, binMat, 0, 0.33, 0));                          // open bin: floor + 4 walls
      g.add(box(0.62, 0.24, 0.04, binMat, 0, 0.44, -0.22)); g.add(box(0.62, 0.24, 0.04, binMat, 0, 0.44, 0.22));
      g.add(box(0.04, 0.24, 0.48, binMat, -0.29, 0.44, 0)); g.add(box(0.04, 0.24, 0.48, binMat, 0.29, 0.44, 0));
      p.load = box(0.42, 0.26, 0.34, std('#b98c5a', { metalness: 0, roughness: 0.9 }), 0, 0.48, 0);
      p.binTop = 0.62;
      p.hd = this._makeHandler();
    } else if (r.type === 'scissor_lift') {
      g.add(box(0.9, 0.08, 0.74, dark, 0, 0.06, 0));
      g.add(box(0.86, 0.24, 0.7, bodyMat, 0, 0.2, 0));
      for (const z of [-0.2, 0.2]) g.add(box(0.02, 0.05, 0.12, p.led, 0.435, 0.2, z, false));
      p.arms = [];
      const armMat = std('#c9ced8', { metalness: 0.7, roughness: 0.3 });
      for (let i = 0; i < 3; i++) for (const z of [-0.3, 0.3]) for (const sgn of [1, -1]) {
        const a = box(1, 0.045, 0.045, armMat); a.userData = { stage: i, sgn }; p.arms.push(a); g.add(a);
      }
      p.platform = new THREE.Group();
      p.platform.add(box(0.84, 0.06, 0.7, std('#2b3245', { metalness: 0.6 }), 0, 0, 0));
      const rail = std('#f5c542', { roughness: 0.5 });
      for (const [x, z] of [[-0.4, -0.33], [0.4, -0.33], [-0.4, 0.33], [0.4, 0.33]]) p.platform.add(box(0.035, 0.5, 0.035, rail, x, 0.27, z));
      p.platform.add(box(0.84, 0.035, 0.035, rail, 0, 0.5, -0.33)); p.platform.add(box(0.84, 0.035, 0.035, rail, 0, 0.5, 0.33));
      p.platform.add(box(0.035, 0.035, 0.7, rail, -0.4, 0.5, 0));
      p.load = box(0.5, 0.32, 0.4, std('#b98c5a', { metalness: 0, roughness: 0.9 }), 0, 0.19, 0); p.platform.add(p.load);
      g.add(p.platform); p.lift = 0.34;
    } else if (r.type === 'pallet_stacker') {
      g.add(box(0.66, 0.85, 0.74, bodyMat, -0.28, 0.5, 0));
      g.add(box(0.68, 0.12, 0.76, std('#f5c542'), -0.28, 0.82, 0));
      g.add(box(0.3, 0.35, 0.5, std('#1b2438'), -0.35, 1.1, 0));
      for (const z of [-0.24, 0.24]) g.add(box(0.06, 2.1, 0.06, std('#4a5266', { metalness: 0.7 }), 0.1, 1.05, z));
      g.add(box(0.06, 0.06, 0.54, std('#4a5266'), 0.1, 2.08, 0));
      for (const [x, z] of [[-0.45, -0.32], [-0.45, 0.32], [0.45, -0.18], [0.45, 0.18]]) { const w = cyl(0.09, 0.09, 0.08, dark); w.rotation.x = Math.PI / 2; w.position.set(x, 0.09, z); g.add(w); }
      for (const z of [-0.2, 0.2]) g.add(box(0.02, 0.05, 0.12, p.led, 0.06, 0.62, z, false));
      p.forks = new THREE.Group();
      p.forks.add(box(0.06, 0.45, 0.6, std('#3a4256', { metalness: 0.6 }), 0.16, 0.22, 0));
      for (const z of [-0.18, 0.18]) p.forks.add(box(0.8, 0.045, 0.1, std('#8a93a8', { metalness: 0.7 }), 0.58, 0.02, z));
      p.load = new THREE.Group();
      p.load.add(box(0.95, 0.14, 0.95, std('#9b6b3c', { metalness: 0 }), 0.6, 0.12, 0));
      p.load.add(box(0.85, 0.75, 0.85, std('#cfd8e6', { metalness: 0, roughness: 0.6 }), 0.6, 0.58, 0));
      p.forks.add(p.load); g.add(p.forks); p.fork = 0.06;
    } else {   // drone
      p.body = new THREE.Group();
      p.body.add(box(0.36, 0.1, 0.36, std('#2a3a52', { metalness: 0.5 }), 0, 0, 0));
      p.body.add(box(0.2, 0.05, 0.2, bodyMat, 0, 0.07, 0));
      for (const a of [Math.PI / 4, -Math.PI / 4]) { const arm = box(1.0, 0.035, 0.06, std('#1b2438')); arm.rotation.y = a; p.body.add(arm); }
      p.rotors = [];
      for (const [x, z] of [[0.36, 0.36], [-0.36, 0.36], [0.36, -0.36], [-0.36, -0.36]]) {
        const m = cyl(0.04, 0.04, 0.06, dark); m.position.set(x, 0.04, z); p.body.add(m);
        const rot = new THREE.Mesh(new THREE.CylinderGeometry(0.19, 0.19, 0.01, 24), new THREE.MeshStandardMaterial({ color: '#cfe6ff', transparent: true, opacity: 0.32 }));
        rot.position.set(x, 0.08, z); p.rotors.push(rot); p.body.add(rot);
      }
      for (const z of [-0.13, 0.13]) p.body.add(box(0.36, 0.025, 0.025, dark, 0, -0.12, z));
      p.body.add(box(0.06, 0.03, 0.06, p.led, 0.17, 0.03, 0, false));
      const cam = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12), dark); cam.position.y = -0.08; p.body.add(cam);
      p.beam = new THREE.Mesh(new THREE.ConeGeometry(1.15, 1, 28, 1, true), new THREE.MeshBasicMaterial({ color: '#46e07a', transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
      p.beam.visible = false; this.scene.add(p.beam);
      g.add(p.body); p.alt = 0.12;
    }
    if (p.load) p.load.visible = false;
    // beacon (fault / blocked), selection ring
    p.beaconMat = glow('#ff5d6c', 2);
    p.beacon = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), p.beaconMat);
    p.beacon.position.y = r.type === 'pallet_stacker' ? 2.25 : r.type === 'drone' ? 0.25 : 0.45; p.beacon.visible = false;
    (p.body || g).add(p.beacon);
    p.ring = new THREE.Mesh(new THREE.RingGeometry(0.62, 0.74, 40), new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.9, depthWrite: false }));
    p.ring.rotation.x = -Math.PI / 2; p.ring.position.y = 0.025; p.ring.visible = false; this.scene.add(p.ring);
    p.fault = new THREE.Mesh(new THREE.RingGeometry(0.8, 1.0, 40), new THREE.MeshBasicMaterial({ color: '#ff5d6c', transparent: true, opacity: 0.8, depthWrite: false }));
    p.fault.rotation.x = -Math.PI / 2; p.fault.position.y = 0.03; p.fault.visible = false; this.scene.add(p.fault);
    // planned path line
    p.pathGeo = new THREE.BufferGeometry(); p.pathGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 512), 3));
    p.path = new THREE.Line(p.pathGeo, new THREE.LineDashedMaterial({ color: col, dashSize: 0.35, gapSize: 0.25, transparent: true, opacity: 0.3 }));
    p.path.frustumCulled = false; this.scene.add(p.path); p.pathKey = '';
    g.traverse(o => { o.userData.rid = r.id; });
    g.scale.setScalar(VIS);
    g.position.set(r.x + 0.5, 0, r.y + 0.5);
    this.scene.add(g);
    // floating vendor marker - readable above the racks from any angle
    p.pinTex = { normal: this._pinTexture(col, LETTER[r.type] || '?'), fault: this._pinTexture('#ff5d6c', '!') };
    p.pin = new THREE.Sprite(new THREE.SpriteMaterial({ map: p.pinTex.normal, depthTest: false, transparent: true }));
    p.pin.scale.set(1.05, 1.05, 1); p.pin.renderOrder = 10; p.pin.userData.rid = r.id; this.scene.add(p.pin);
    p.stem = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 1, 0)]),
      new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.35 }));
    this.scene.add(p.stem);
    p.halo = new THREE.Mesh(new THREE.CircleGeometry(0.85, 32), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.28, depthWrite: false }));
    p.halo.rotation.x = -Math.PI / 2; p.halo.position.y = 0.02; this.scene.add(p.halo);
    return { g, p, pos: new THREE.Vector3(r.x + 0.5, 0, r.y + 0.5), yaw: 0, data: r };
  }

  _pinTexture(color, letter) {
    const c = document.createElement('canvas'); c.width = c.height = 96; const g = c.getContext('2d');
    g.fillStyle = 'rgba(6,16,40,.85)'; g.beginPath(); g.arc(48, 48, 44, 0, 7); g.fill();
    g.lineWidth = 7; g.strokeStyle = color; g.stroke();
    g.fillStyle = color; g.font = '800 46px Inter, Segoe UI, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(letter, 48, 51);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
  _disposeRobot(rec) { for (const o of [rec.g, rec.p.ring, rec.p.fault, rec.p.path, rec.p.beam, rec.p.pin, rec.p.stem, rec.p.halo, rec.p.hd && rec.p.hd.g]) if (o) this.scene.remove(o); }

  // ------------------------------------------------------------------ live data
  update(snap) {
    this.snap = snap;
    const seen = new Set();
    for (const r of snap.robots) {
      seen.add(r.id);
      let rec = this.robots.get(r.id);
      if (!rec) { rec = this._makeRobot(r); this.robots.set(r.id, rec); }
      if (rec.data && rec.data !== r) this._transitions(rec, rec.data, r);
      rec.data = r;
      const key = r.path.length + ':' + (r.path[0] || '') + ':' + (r.path[r.path.length - 1] || '');
      if (key !== rec.p.pathKey) {
        rec.p.pathKey = key;
        const a = rec.p.pathGeo.attributes.position, n = Math.min(r.path.length + 1, 512), y = 0.04;
        a.setXYZ(0, r.x + 0.5, y, r.y + 0.5);
        for (let i = 1; i < n; i++) a.setXYZ(i, r.path[i - 1][0] + 0.5, y, r.path[i - 1][1] + 0.5);
        a.needsUpdate = true; rec.p.pathGeo.setDrawRange(0, n); rec.p.path.computeLineDistances();
      }
    }
    for (const [id, rec] of this.robots) if (!seen.has(id)) { this._disposeRobot(rec); this.robots.delete(id); }
    this.scale = snap.scale || 1;
    this._syncPallets(snap);
    this._setClosed(snap.closed_rows || []);
    // queued order markers
    const want = new Set();
    for (const t of snap.tasks) {
      if (t.status !== 'queued') continue;
      want.add(t.id);
      if (!this.markers.has(t.id)) {
        const m = new THREE.Mesh(new THREE.CylinderGeometry(0.26, 0.26, 0.07, 6), glow(KIND_COL[t.kind], 0.9));
        m.position.set(t.pickup[0] + 0.5, 0.6, t.pickup[1] + 0.5); m.userData.phase = Math.random() * 6;
        this.scene.add(m); this.markers.set(t.id, m);
      }
    }
    for (const [id, m] of this.markers) if (!want.has(id)) { this.scene.remove(m); m.geometry.dispose(); this.markers.delete(id); }
  }

  select(id) { this.selected = id; if (!id) this.follow = false; }

  view(name) {
    const { W, H, cx, cz } = this, c = this.controls, m = Math.max(W, H);
    this.follow = false; c.autoRotate = false;
    if (name === 'top') { this.camera.position.set(cx, m * 1.08, cz + 0.01); c.target.set(cx, 0, cz); }
    else if (name === 'follow') { this.follow = !!this.selected; if (!this.follow) return false; }
    else if (name === 'orbit') { c.autoRotate = true; }
    else { this.camera.position.set(cx + W * 0.18, m * 0.5, cz + H * 0.98); c.target.set(cx, 0, cz * 1.05); }
    c.update(); return true;
  }

  // ------------------------------------------------------------------ pick / drop realism
  _dur(kind) { return Math.min(2.6, Math.max(0.25, (this.dwell[kind] || 2) / Math.max(0.1, this.scale))); }
  _carton(col = '#b98c5a', size = [0.42, 0.26, 0.34]) {
    const m = box(size[0], size[1], size[2], std(col, { metalness: 0, roughness: 0.9 }), 0, 0, 0); m.castShadow = true; this.scene.add(m); return m;
  }
  _fly(mesh, from, to, dur, { arc = 0.6, delay = 0, then = null } = {}) {
    mesh.position.copy(from); this.flyers.push({ mesh, from: from.clone(), to, dur, arc, t: -delay, then });
  }
  _shelfRowAt(cellY) { return (this.shelvesSorted || []).find(s => s.y + s.h === Math.round(cellY)); }
  _takeBox(x, s, levels) {
    // take the real carton out of the rack slot (front row first); it is restocked a while later
    for (const li of levels) for (const z of [s.y + s.h - 1, s.y]) {
      const sl = this.slots.get(`${x}|${z}|${li}`);
      if (sl && !sl.taken) {
        sl.taken = true; const o = new THREE.Matrix4().makeScale(0, 0, 0); this.boxMesh.setMatrixAt(sl.i, o); this.boxMesh.instanceMatrix.needsUpdate = true;
        this.restock.push({ sl, at: performance.now() + 90000 }); return { pos: sl.pos.clone(), col: sl.col, size: [sl.size[0] * 0.7, Math.min(sl.size[1], 0.5), sl.size[2] * 0.6] };
      }
    }
    return { pos: new THREE.Vector3(x + 0.5, LEVELS[levels[0]] + 0.3, s.y + s.h - 0.5), col: '#b98c5a', size: [0.42, 0.26, 0.34] };
  }
  _binPos(rec) { return new THREE.Vector3(rec.pos.x, (rec.p.binTop || 0.62) * VIS, rec.pos.z); }
  _platPos(rec) { return new THREE.Vector3(rec.pos.x, (rec.p.lift + 0.25) * VIS, rec.pos.z); }
  _forkTip(rec) { return new THREE.Vector3(rec.pos.x + Math.cos(rec.yaw) * 0.6 * VIS, 0.07, rec.pos.z - Math.sin(rec.yaw) * 0.6 * VIS); }

  // ---- handling unit of the tote AMR: a telescopic mast with a tray and two side arms that reach into the rack
  _makeHandler() {
    const g = new THREE.Group(), metal = std('#58627a', { metalness: 0.75, roughness: 0.3 }), arm = std('#c9ced8', { metalness: 0.8, roughness: 0.25 });
    const h = { g, posts: [], arms: [], fingers: [] };
    for (const x of [-0.36, 0.36]) { const m = box(0.06, 1, 0.06, metal); h.posts.push(m); g.add(m); m.userData.x = x; }
    h.tray = box(0.66, 0.035, 0.52, std('#2b3245', { metalness: 0.6 })); g.add(h.tray);
    for (const x of [-0.31, 0.31]) {
      const a = box(0.035, 0.05, 1, arm); a.userData.x = x; h.arms.push(a); g.add(a);
      const f = box(0.08, 0.12, 0.03, glow('#2E9FE8', 0.9)); f.userData.x = x; h.fingers.push(f); g.add(f);
    }
    g.visible = false; this.scene.add(g); return h;
  }
  // tray height y (top surface), arms extended by ext metres in z-direction dir; finger closed 0..1
  _poseHandler(rec, y, ext, dir, grip = 0) {
    const h = rec.p.hd, x = rec.pos.x, z = rec.pos.z;
    h.g.visible = true;
    for (const m of h.posts) { const top = Math.max(y + 0.12, 0.9); m.scale.y = top - 0.3; m.position.set(x + m.userData.x, 0.3 + (top - 0.3) / 2, z); }
    h.tray.position.set(x, y - 0.018, z);
    const len = 0.5 + ext;
    h.arms.forEach(a => { a.scale.z = len; a.position.set(x + a.userData.x, y + 0.1, z + dir * (len / 2 - 0.25)); });   // from behind the mast to the finger tips
    h.fingers.forEach(f => { f.position.set(x + f.userData.x * (1 - 0.18 * grip), y + 0.1, z + dir * (ext + 0.22)); });
  }
  _seq(dur, step, then) { this.seqs.push({ t: 0, dur, step, then }); }
  _cargoLoad(rec, cargo) {     // show the very same carton (colour + size) in the robot's bin / on its platform
    const p = rec.p; if (!p.load || !cargo) return;
    p.cargo = cargo; p.load.material.color.set(cargo.col);
    const base = p.kind === 'tote_amr' ? [0.42, 0.26, 0.34] : [0.5, 0.32, 0.4];
    p.load.scale.set(cargo.size[0] / VIS / base[0], cargo.size[1] / VIS / base[1], cargo.size[2] / VIS / base[2]);
    if (p.kind === 'tote_amr') p.load.position.y = 0.35 + cargo.size[1] / VIS / 2;
    else p.load.position.y = 0.03 + cargo.size[1] / VIS / 2;
  }
  // straight-line moves through way-points (no flying): each leg eased, way-points may be live functions
  _slide(mesh, pts, dur, then) {
    const P = () => pts.map(q => (typeof q === 'function' ? q() : q));
    const L = P().slice(1).map((q, i, a) => q.distanceTo(i ? a[i - 1] : P()[0]) + 1e-3), tot = L.reduce((x, y) => x + y, 0);
    this._seq(dur, k => {
      const q = P(); let d = k * tot, i = 0;
      while (i < L.length - 1 && d > L[i]) { d -= L[i]; i++; }
      const u = Math.min(1, d / L[i]), e = u * u * (3 - 2 * u);
      mesh.position.lerpVectors(q[i], q[i + 1], e);
    }, then);
  }
  _bandDur(kind, lo, hi) { return Math.min(hi, Math.max(lo, (this.dwell[kind] || 2) / Math.max(0.1, this.scale))); }

  _pickTote(rec, b) {
    const p = rec.p, m = this._carton(b.col, b.size), s1 = b.size[1];
    m.position.copy(b.pos); p.pending = true;
    const cargo = { col: b.col, size: b.size };
    if (this.scale > 20 || !p.hd) {                 // very fast replay: short straight transfer
      this._slide(m, [b.pos, () => new THREE.Vector3(rec.pos.x, b.pos.y, rec.pos.z), () => this._binPos(rec)], 0.4, () => { this.scene.remove(m); this._cargoLoad(rec, cargo); p.pending = false; });
      return;
    }
    p.hold = true;
    const base = rec.pos.clone(), dir = Math.sign(b.pos.z - base.z) || 1, L = Math.max(0.05, Math.abs(b.pos.z - base.z) - 0.05);
    const rest = 0.62 * VIS, yS = b.pos.y - s1 / 2, inBin = (0.35 * VIS) + s1 / 2;
    const ph = (k, a, z) => Math.min(1, Math.max(0, (k - a) / (z - a))), ez = u => u * u * (3 - 2 * u);
    this._seq(this._bandDur('pick', 1.6, 3.0), k => {
      let y, ext, grip = 0;
      if (k < 0.2) { y = rest + (yS - rest) * ez(ph(k, 0, 0.2)); ext = 0; }                          // mast rises to the slot
      else if (k < 0.42) { y = yS; ext = L * ez(ph(k, 0.2, 0.42)); }                                    // arms reach into the rack
      else if (k < 0.5) { y = yS; ext = L; grip = ph(k, 0.42, 0.5); }                                   // fingers close
      else if (k < 0.72) { y = yS; ext = L * (1 - ez(ph(k, 0.5, 0.72))); grip = 1; }                     // carton pulled onto the tray
      else { y = yS + (rest - yS) * ez(ph(k, 0.72, 0.92)); ext = 0; grip = 1 - ph(k, 0.9, 1); }          // tray lowers, carton into the bin
      this._poseHandler(rec, y, ext, dir, grip);
      if (k >= 0.5) {
        const cy = k < 0.92 ? y + s1 / 2 : rest + s1 / 2 + (inBin - rest - s1 / 2) * ez(ph(k, 0.92, 1));
        m.position.set(rec.pos.x, cy, rec.pos.z + dir * (k < 0.72 ? ext : 0));
      }
    }, () => { p.hd.g.visible = false; p.hold = false; this.scene.remove(m); this._cargoLoad(rec, cargo); p.pending = false; });
  }

  _dropTote(rec) {
    const p = rec.p, t = this._nearestTunnel(rec.pos); if (!t) return;
    const cargo = p.cargo || { col: '#b98c5a', size: [0.42, 0.26, 0.34] }, s1 = cargo.size[1];
    const m = this._carton(cargo.col, cargo.size); p.hideLoad = true; p.cargo = null;
    const belt = 0.95, bx = Math.min(t.entry - 0.8, Math.max(t.zone.x + 0.9, rec.pos.x));
    m.position.copy(this._binPos(rec)); m.position.y = 0.35 * VIS + s1 / 2;
    const onBelt = () => this.conveyor.push({ m, t });
    if (this.scale > 20 || !p.hd) { this._slide(m, [m.position.clone(), new THREE.Vector3(rec.pos.x, belt + s1 / 2 + 0.05, rec.pos.z), new THREE.Vector3(bx, belt + s1 / 2, t.bz)], 0.4, onBelt); return; }
    // the robot docks at the conveyor (side-on, one robot per dock position), then transfers the carton
    const dx = Math.round(rec.pos.x - 0.5) + 0.5, dz = t.bz + 0.95;
    const taken = [...this.robots.values()].some(o => o !== rec && o.p.dock && Math.abs(o.p.dock.x - dx) < 0.6 && Math.abs(o.p.dock.z - dz) < 0.6);
    if (!taken) p.dock = { x: dx, z: dz };
    const approach = taken ? 0 : Math.min(1.2, 0.35 + Math.abs(rec.pos.z - dz) * 0.3);
    this._seq(approach, () => { m.position.set(rec.pos.x, 0.35 * VIS + s1 / 2, rec.pos.z); }, () => { this._transferToBelt(rec, m, t, bx, s1, onBelt); });
  }
  _transferToBelt(rec, m, t, bx, s1, onBelt) {
    const p = rec.p, belt = 0.95;
    p.hold = true;
    const base = rec.pos.clone(), dir = Math.sign(t.bz - base.z) || -1, L = Math.abs(t.bz - base.z), rest = 0.62 * VIS;
    const ph = (k, a, z) => Math.min(1, Math.max(0, (k - a) / (z - a))), ez = u => u * u * (3 - 2 * u);
    let released = false;
    this._seq(this._bandDur('drop', 1.4, 2.6), k => {
      let y, ext, grip = 1;
      if (k < 0.22) { y = rest + (belt - rest) * ez(ph(k, 0, 0.22)); ext = 0; }                          // lift carton to belt height
      else if (k < 0.5) { y = belt; ext = L * ez(ph(k, 0.22, 0.5)); }                                   // push it sideways onto the belt
      else if (k < 0.58) { y = belt; ext = L; grip = 1 - ph(k, 0.5, 0.58); }                            // release
      else if (k < 0.82) { y = belt; ext = L * (1 - ez(ph(k, 0.58, 0.82))); grip = 0; }                  // arms retract
      else { y = belt + (rest - belt) * ez(ph(k, 0.82, 1)); ext = 0; grip = 0; }                        // mast down
      this._poseHandler(rec, y, ext, dir, grip);
      if (k < 0.22) m.position.set(rec.pos.x, Math.max(m.position.y, y + s1 / 2), rec.pos.z);
      else if (k < 0.5) m.position.set(rec.pos.x + (bx - rec.pos.x) * ez(ph(k, 0.22, 0.5)), belt + s1 / 2, rec.pos.z + dir * ext);
      else if (!released) { released = true; m.position.set(bx, belt + s1 / 2, t.bz); onBelt(); }
    }, () => { p.hd.g.visible = false; p.hold = false; p.dock = null; if (!released) { m.position.set(bx, belt + s1 / 2, t.bz); onBelt(); } });
  }
  _nearestTunnel(pos) {
    let best = null, bd = 1e9;
    for (const t of this.tunnels) { const z = t.zone, d = Math.hypot(z.x + z.w / 2 - pos.x, z.y + z.h / 2 - pos.z); if (d < bd) { bd = d; best = t; } }
    return best;
  }

  _transitions(rec, prev, r) {
    const startWork = r.state === 'working' && (prev.state !== 'working' || prev.action !== r.action);
    if (startWork && r.action === 'pick box') {                       // tote AMR: mast up, arms into the rack, carton pulled onto the tray, into the bin
      const s = this._shelfRowAt(r.y); if (!s) return;
      this._pickTote(rec, this._takeBox(Math.round(r.x), s, Math.random() < 0.5 ? [0, 1] : [1, 0]));
    } else if (startWork && r.action === 'pick high shelf') {         // scissor lift: platform rises, carton slides straight out of the TOP level
      const s = this._shelfRowAt(r.y); if (!s) return;
      const d = this._dur('high_pick'), b = this._takeBox(Math.round(r.x), s, [3, 2]);
      const m = this._carton(b.col, b.size); m.position.copy(b.pos); rec.p.pending = true;
      const cargo = { col: b.col, size: b.size }, wait = Math.max(0.6, d * 0.5);
      this._seq(wait, () => {}, () => this._slide(m, [b.pos, () => new THREE.Vector3(rec.pos.x, Math.max(b.pos.y, this._platPos(rec).y + b.size[1] / 2), rec.pos.z), () => this._platPos(rec).setY(this._platPos(rec).y - 0.25 * VIS + 0.03 + b.size[1] / 2 + 0.05)],
        Math.max(0.6, d * 0.45), () => { this.scene.remove(m); this._cargoLoad(rec, cargo); rec.p.pending = false; }));
    } else if (startWork && r.action === 'hand over') {               // lift -> tote AMR of the same order: slide across, then down into the bin
      const mate = [...this.robots.values()].find(o => o !== rec && o.data.task === r.task && o.data.type === 'tote_amr');
      if (!mate) return;
      const cargo = rec.p.cargo || { col: '#b98c5a', size: [0.42, 0.26, 0.34] };
      rec.p.hideLoad = true; mate.p.pending = true; rec.p.cargo = null;
      const m = this._carton(cargo.col, cargo.size), plat = () => this._platPos(rec).setY(this._platPos(rec).y - 0.25 * VIS + 0.05 + cargo.size[1] / 2);
      this._slide(m, [plat(), () => new THREE.Vector3(mate.pos.x, Math.max(plat().y, 0.9), mate.pos.z), () => this._binPos(mate).setY(0.35 * VIS + cargo.size[1] / 2)],
        Math.max(0.8, this._dur('handoff')), () => { this.scene.remove(m); this._cargoLoad(mate, cargo); mate.p.pending = false; });
    } else if (startWork && r.action === 'place at transfer point') { // separate managers: lift leaves the carton on the floor
      const cargo = rec.p.cargo || { col: '#b98c5a', size: [0.42, 0.26, 0.34] };
      rec.p.hideLoad = true; rec.p.cargo = null; const m = this._carton(cargo.col, cargo.size); m.userData.cargo = cargo;
      const spot = new THREE.Vector3(rec.pos.x + 0.55, cargo.size[1] / 2 + 0.01, rec.pos.z + 0.55), from = this._platPos(rec);
      this._slide(m, [from, new THREE.Vector3(spot.x, from.y, spot.z), spot], Math.max(0.8, this._dur('handoff')), () => { this.transferBoxes.push(m); });
    } else if (startWork && r.action === 'collect tote') {
      let best = null, bd = 9;
      for (const m of this.transferBoxes) { const dd = Math.hypot(m.position.x - rec.pos.x, m.position.z - rec.pos.z); if (dd < bd) { bd = dd; best = m; } }
      if (best) {
        this.transferBoxes.splice(this.transferBoxes.indexOf(best), 1);
        const cargo = best.userData.cargo || { col: '#b98c5a', size: [0.42, 0.26, 0.34] };
        this.scene.remove(best); this._pickTote(rec, { pos: best.position.clone(), col: cargo.col, size: cargo.size });
      }
    } else if (startWork && r.action === 'drop at packing') {         // carton pushed onto the conveyor, rides into the pack & ship tunnel
      this._dropTote(rec);
    }
    // pallets: picked up -> gone from the floor; set down at the dock -> stays there until the truck takes it
    if (r.type === 'pallet_stacker') {
      if (!prev.load && r.load === 'pallet' && prev.task) this.lifted.add(prev.task);
      if (prev.load === 'pallet' && !r.load && prev.action === 'set down at dock') this._dockPallet(this._forkTip(rec));
    }
  }
  _nearestZone(pos, prefix) {
    let best = null, bd = 1e9;
    for (const [k, z] of Object.entries(this.map.zones)) if (k.startsWith(prefix)) { const d = Math.hypot(z.x + z.w / 2 - pos.x, z.y + z.h / 2 - pos.z); if (d < bd) { bd = d; best = z; } }
    return best;
  }
  _palletMesh(h = 0.8) {
    const g = new THREE.Group();
    g.add(box(0.95, 0.14, 0.95, std('#9b6b3c', { metalness: 0, roughness: 0.9 }), 0, 0.07, 0));
    g.add(box(0.86, h, 0.86, std('#cfd8e6', { metalness: 0, roughness: 0.6 }), 0, 0.14 + h / 2, 0));
    this.scene.add(g); return g;
  }
  _syncPallets(snap) {
    // pallets waiting to be moved stand where the pallet stacker will pick them up
    const want = new Set();
    for (const t of snap.tasks) if (t.kind === 'pallet_move' && !this.lifted.has(t.id)) {
      want.add(t.id);
      if (!this.waitPallets.has(t.id)) { const g = this._palletMesh(0.55 + (t.id % 5) * 0.12); g.position.set(t.pickup[0] + 0.5, 0, t.pickup[1] + 0.5); this.waitPallets.set(t.id, g); }
    }
    for (const [id, g] of this.waitPallets) if (!want.has(id)) { this.scene.remove(g); this.waitPallets.delete(id); }
    if (this.lifted.size > 500) this.lifted.clear();
  }
  _dockPallet(pos) {
    const g = this._palletMesh(0.75); g.position.set(pos.x, 0, pos.z); this.dockPallets.push(g);
    if (this.dockPallets.length >= 6 && !this.truckGo) this.truckGo = { t: 0, load: this.dockPallets.splice(0) };
  }
  _animateItems(dt, t) {
    for (let i = this.flyers.length - 1; i >= 0; i--) {
      const f = this.flyers[i]; f.t += dt;
      if (f.t < 0) continue;
      if (f.show) { f.show.visible = true; f.show = null; }
      const k = Math.min(1, f.t / f.dur), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const to = typeof f.to === 'function' ? f.to() : f.to;
      f.mesh.position.lerpVectors(f.from, to, e); f.mesh.position.y += Math.sin(Math.PI * e) * f.arc;
      if (k >= 1) { this.flyers.splice(i, 1); if (f.then) f.then(); }
    }
    for (let i = this.seqs.length - 1; i >= 0; i--) {                // multi-step handling sequences
      const q = this.seqs[i]; q.t += dt; const k = Math.min(1, q.t / q.dur);
      q.step(k); if (k >= 1) { this.seqs.splice(i, 1); if (q.then) q.then(); }
    }
    const belt = 0.7 * Math.min(4, Math.max(1, this.scale));         // cartons ride the rollers into the pack & ship tunnel and leave the twin there
    for (let i = this.conveyor.length - 1; i >= 0; i--) {
      const c = this.conveyor[i], half = (c.m.geometry.parameters.width || 0.4) / 2;
      c.m.position.x += dt * belt; c.m.position.z += (c.t.bz - c.m.position.z) * Math.min(1, dt * 6);
      if (c.m.position.x - half > c.t.entry + 0.05) { this.scene.remove(c.m); this.conveyor.splice(i, 1); }
    }
    const now = performance.now();
    for (let i = this.restock.length - 1; i >= 0; i--) if (now >= this.restock[i].at) {
      const { sl } = this.restock[i]; sl.taken = false; this.boxMesh.setMatrixAt(sl.i, sl.m); this.boxMesh.instanceMatrix.needsUpdate = true; this.restock.splice(i, 1);
    }
    if (this.truckGo && this.truck) {                                 // truck loads the dock pallets, drives off, comes back
      const g = this.truckGo; g.t += dt;
      const fade = Math.max(0, 1 - g.t / 1.2); g.load.forEach(p => p.scale.setScalar(Math.max(0.01, fade)));
      if (g.t > 1.2 && g.load.length) { g.load.forEach(p => this.scene.remove(p)); g.load = []; }
      this.truck.position.x = this.truckHome.x + (g.t < 1.5 ? 0 : g.t < 5 ? (g.t - 1.5) ** 2 * 3 : Math.max(0, 36 - (g.t - 5) * 12));
      if (g.t > 8) { this.truck.position.copy(this.truckHome); this.truckGo = null; }
    }
  }

  // ------------------------------------------------------------------ overlays: congestion heatmap, closed aisles
  _buildHeat() {
    const n = this.W * this.H;
    this.heat = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.96, 0.96), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.62, depthWrite: false }), n);
    this.heat.rotation.x = 0; this.heat.count = 0; this.heat.visible = false; this.heat.renderOrder = 2; this.scene.add(this.heat);
  }
  setHeat(data) {
    if (!data) { this.heat.visible = false; return; }
    const o = new THREE.Object3D(), c = new THREE.Color(), max = Math.max(1, data.max), cells = data.cells.slice(0, this.W * this.H);
    cells.forEach(([x, y, v], i) => {
      o.position.set(x + 0.5, 0.05, y + 0.5); o.rotation.set(-Math.PI / 2, 0, 0); o.updateMatrix(); this.heat.setMatrixAt(i, o.matrix);
      const k = Math.log1p(v) / Math.log1p(max); c.setHSL(0.16 - 0.16 * k, 1, 0.5); this.heat.setColorAt(i, c);
    });
    this.heat.count = cells.length; this.heat.instanceMatrix.needsUpdate = true; if (this.heat.instanceColor) this.heat.instanceColor.needsUpdate = true;
    this.heat.visible = true;
  }
  _setClosed(rows) {
    const key = rows.join(','); if (key === this._closedKey) return; this._closedKey = key;
    for (const [r, g] of this.closedObjs) if (!rows.includes(r)) { this.scene.remove(g); this.closedObjs.delete(r); }
    for (const r of rows) if (!this.closedObjs.has(r) && this.map.aisles && this.map.aisles[r]) {
      const [x, y, w, h] = this.map.aisles[r], g = new THREE.Group();
      const c = document.createElement('canvas'); c.width = 64; c.height = 64; const q = c.getContext('2d');
      q.fillStyle = '#1b1b1b'; q.fillRect(0, 0, 64, 64); q.strokeStyle = '#f5c542'; q.lineWidth = 14;
      for (let i = -64; i < 128; i += 32) { q.beginPath(); q.moveTo(i, 64); q.lineTo(i + 64, 0); q.stroke(); }
      const tex = new THREE.CanvasTexture(c); tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.repeat.set(w, h); tex.colorSpace = THREE.SRGBColorSpace;
      const pl = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.75, depthWrite: false }));
      pl.rotation.x = -Math.PI / 2; pl.position.set(x + w / 2, 0.06, y + h / 2); g.add(pl);
      for (const xx of [x, x + w]) { g.add(box(0.12, 1.0, 0.12, std('#f5c542'), xx, 0.5, y + h / 2)); g.add(box(0.06, 0.12, h, std('#ff5d6c'), xx, 0.9, y + h / 2)); }
      const { tex: lt, aspect } = textTexture(`A${r + 1} · MAINTENANCE`, { size: 72, color: '#1b1b1b', bg: '#f5c542' });
      const ls = Math.max(5, Math.max(this.W, this.H) / 9), lab = new THREE.Sprite(new THREE.SpriteMaterial({ map: lt, depthTest: false })); lab.scale.set(ls, ls / aspect, 1); lab.position.set(x + w / 2, 5.2, y + h / 2); lab.renderOrder = 11; g.add(lab);
      g.add(box(w, 0.06, 0.08, std('#f5c542'), x + w / 2, 4.0, y + h / 2, false));   // overhead barrier tape, visible above the racks
      this.scene.add(g); this.closedObjs.set(r, g);
    }
  }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
    this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }

  _pick(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.ptr.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.ray.setFromCamera(this.ptr, this.camera);
    const hits = this.ray.intersectObjects([...this.robots.values()].flatMap(r => [r.g, r.p.pin]), true);
    return hits.length ? hits[0].object.userData.rid : null;
  }
  _hover(e) {
    if (!this.onHover) return;
    const id = this._pick(e), rect = this.el.getBoundingClientRect();
    this.renderer.domElement.style.cursor = id ? 'pointer' : 'grab';
    this.onHover(id ? this.robots.get(id).data : null, e.clientX - rect.left, e.clientY - rect.top);
  }
  _click(e) { const id = this._pick(e); if (this.onSelect) this.onSelect(id); }

  // ------------------------------------------------------------------ animation
  _loop() {
    this.raf = requestAnimationFrame(this._loop);
    const dt = Math.min(this.clock.getDelta(), 0.1), t = this.clock.elapsedTime;
    if (!this.active) return;
    const k = 1 - Math.exp(-dt * 9);
    for (const rec of this.robots.values()) this._animate(rec, dt, t, k);
    this._animateItems(dt, t);
    for (const m of this.markers.values()) { m.position.y = 0.6 + Math.sin(t * 2.4 + m.userData.phase) * 0.12; m.rotation.y += dt * 1.2; }
    if (this.follow && this.selected && this.robots.has(this.selected)) {
      const p = this.robots.get(this.selected).pos, c = this.controls, d = p.clone().sub(c.target).multiplyScalar(k);
      c.target.add(d); this.camera.position.add(d);
    }
    this.controls.update();
    this._tag();
    if (this.frame++ % 5 === 0) this.renderer.shadowMap.needsUpdate = true;
    this.renderer.render(this.scene, this.camera);
    // adaptive quality: if this machine renders < 22 fps for 3 s, drop shadows and pixel density once
    const nowMs = performance.now(); this.fpsN++;
    if (!this.fpsStart) this.fpsStart = nowMs;
    if (nowMs - this.fpsStart >= 3000) { const fps = this.fpsN * 1000 / (nowMs - this.fpsStart); this.fps = Math.round(fps); this.fpsStart = nowMs; this.fpsN = 0;
      if (fps < 22 && !this.lowQ) { this.lowQ = true; this.renderer.shadowMap.enabled = false; this.renderer.setPixelRatio(1);
        this.scene.traverse(o => { if (o.material) o.material.needsUpdate = true; }); this.resize(); } }
  }

  _animate(rec, dt, t, k) {
    const r = rec.data, p = rec.p, g = rec.g;
    const tx = p.dock ? p.dock.x : r.x + 0.5, tz = p.dock ? p.dock.z : r.y + 0.5, dx = tx - rec.pos.x, dz = tz - rec.pos.z;
    if (dx * dx + dz * dz > 0.0004) {
      const want = p.hold ? rec.yaw : Math.atan2(-dz, dx);
      let diff = want - rec.yaw; diff = Math.atan2(Math.sin(diff), Math.cos(diff)); rec.yaw += diff * Math.min(1, dt * 7);
    }
    if (!p.hold) { rec.pos.x += dx * k; rec.pos.z += dz * k; }
    if (Math.abs(dx) > 6 || Math.abs(dz) > 6) { rec.pos.x = tx; rec.pos.z = tz; }        // teleport on reset
    g.position.set(rec.pos.x, 0, rec.pos.z); g.rotation.y = rec.yaw;
    p.led.color.set(STATE_COL[r.state] || '#7f93b5'); p.led.emissive.set(STATE_COL[r.state] || '#7f93b5');
    p.led.emissiveIntensity = r.state === 'charging' ? 1.2 + Math.sin(t * 4) * 0.8 : 1.8;
    if (p.load) p.load.visible = !!r.load && !p.pending && !p.hideLoad;
    if (!r.load) p.hideLoad = false;

    if (p.kind === 'scissor_lift') {
      const up = (r.state === 'working' && r.action === 'pick high shelf') || (r.load && r.state !== 'moving' && r.action !== 'hand over' && r.action !== 'place at transfer point');
      const target = up ? LIFT_TOP : 0.34;
      p.lift += (target - p.lift) * Math.min(1, dt * 1.6);
      p.platform.position.y = p.lift + 0.03;
      const base = 0.32, hs = Math.max(0.02, (p.lift - base) / 3), half = 0.36, len = Math.hypot(2 * half, hs), th = Math.atan2(hs, 2 * half);
      for (const a of p.arms) {
        a.scale.x = len; a.rotation.z = th * a.userData.sgn;
        a.position.set(0, base + hs * (a.userData.stage + 0.5), a.position.z || 0);
      }
      p.arms.forEach((a, i) => { a.position.z = (Math.floor(i / 2) % 2 ? 0.3 : -0.3); });
    } else if (p.kind === 'pallet_stacker') {
      const target = r.load ? 0.32 : (r.state === 'working' ? 0.2 : 0.06);
      p.fork += (target - p.fork) * Math.min(1, dt * 2.5); p.forks.position.y = p.fork;
    } else if (p.kind === 'drone') {
      const flying = !['idle', 'charging', 'fault'].includes(r.state);
      const target = flying ? DRONE_ALT / VIS : 0.12;
      p.alt += (target - p.alt) * Math.min(1, dt * 1.1);
      const bob = flying ? Math.sin(t * 2.2) * 0.06 : 0;
      p.body.position.y = p.alt + bob;
      const spin = p.alt > 0.2 || flying ? 38 : 0;
      for (const ro of p.rotors) ro.rotation.y += spin * dt;
      p.body.rotation.z = flying ? Math.sin(t * 1.3) * 0.03 : 0;
      const scanning = (r.action || '').includes('scan') && (r.state === 'working' || (r.state === 'moving' && r.action === 'finish scan'));
      p.beam.visible = scanning && p.alt > 2;
      if (p.beam.visible) {
        const h = p.alt * VIS - 0.1; p.beam.scale.set(1, h, 1); p.beam.position.set(rec.pos.x, h / 2, rec.pos.z);
        p.beam.material.opacity = 0.1 + 0.06 * Math.sin(t * 6);
      }
    }
    // indicators
    const fault = r.state === 'fault', blocked = !fault && r.blocked_for >= 1 && r.state === 'moving';
    p.beacon.visible = fault || blocked;
    if (p.beacon.visible) { const c = fault ? '#ff5d6c' : '#f5a524'; p.beaconMat.color.set(c); p.beaconMat.emissive.set(c); p.beaconMat.emissiveIntensity = 1 + Math.abs(Math.sin(t * 6)) * 2.5; }
    p.fault.visible = fault;
    if (fault) { p.fault.position.x = rec.pos.x; p.fault.position.z = rec.pos.z; const s = 1 + 0.25 * Math.sin(t * 5); p.fault.scale.set(s, s, s); }
    const sel = r.id === this.selected;
    p.ring.visible = sel; if (sel) { p.ring.position.x = rec.pos.x; p.ring.position.z = rec.pos.z; }
    const topY = p.kind === 'drone' ? p.alt * VIS + 1.4 : 5.4;
    p.pin.position.set(rec.pos.x, topY, rec.pos.z);
    p.pin.material.map = fault ? p.pinTex.fault : p.pinTex.normal;
    const busy = !['idle', 'charging'].includes(r.state);           // parked robots get a small, faint marker
    const ps = sel ? 1.45 : busy ? 1.0 : 0.62; p.pin.scale.set(ps, ps, 1); p.pin.material.opacity = sel || busy ? 1 : 0.55;
    p.stem.visible = busy || sel;
    p.stem.position.set(rec.pos.x, 0, rec.pos.z); p.stem.scale.y = topY - 0.5;
    p.halo.position.x = rec.pos.x; p.halo.position.z = rec.pos.z;
    p.halo.material.opacity = sel ? 0.5 : 0.28;
    p.path.material.opacity = sel ? 0.95 : 0.28;
    p.path.visible = r.path.length > 0;
  }

  _tag() {
    const rec = this.selected && this.robots.get(this.selected);
    if (!rec) { this.tag.style.display = 'none'; return; }
    const r = rec.data, h = r.type === 'drone' ? rec.p.alt * VIS + 2.2 : 6.3;
    const v = new THREE.Vector3(rec.pos.x, h, rec.pos.z).project(this.camera);
    if (v.z > 1) { this.tag.style.display = 'none'; return; }
    const w = this.el.clientWidth, hh = this.el.clientHeight;
    this.tag.style.display = 'block'; this.tag.style.left = ((v.x + 1) / 2 * w) + 'px'; this.tag.style.top = ((1 - v.y) / 2 * hh) + 'px';
    this.tag.innerHTML = `<b>${r.id.replace(/_/g, ' ')}</b> · ${r.state}${r.action ? ' · ' + r.action : ''} · ${Math.round(r.battery)}%`;
  }

  dispose() {
    cancelAnimationFrame(this.raf); this.ro.disconnect(); this.controls.dispose();
    this.renderer.dispose(); this.renderer.domElement.remove(); this.tag.remove();
  }
}
