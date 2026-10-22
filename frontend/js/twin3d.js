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
    this.W = map.width; this.H = map.height; this.cx = this.W / 2; this.cz = this.H / 2;

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
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

    this._buildFloor(); this._buildZones(); this._buildRacks(); this._buildPallets(); this._buildPacking();
    this._buildChargers(); this._buildWalls();
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
          boxes.push([x + 0.5, lv + 0.03 + h / 2, z + 0.5, w, h, d, col]);
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
    inst(new THREE.BoxGeometry(1, 1, 1), std('#ffffff', { roughness: 0.85, metalness: 0 }), boxes,
      (o, p) => { o.position.set(p[0], p[1], p[2]); o.scale.set(p[3], p[4], p[5]); o.rotation.y = 0; }, false);
  }

  _buildPallets() {
    const z = this.map.zones.pallet_area; if (!z) return;
    const rand = rng(11), pal = [], loads = [];
    for (let x = z.x + 1; x < z.x + z.w - 1; x += 2) for (let y = z.y + 1.5; y < z.y + z.h - 1; y += 2.5) if (rand() < 0.55) {
      pal.push([x + 0.5, 0.07, y]);
      const h = 0.5 + rand() * 0.9; loads.push([x + 0.5, 0.14 + h / 2, y, h, rand() < 0.5 ? '#cfd8e6' : '#b98c5a']);
    }
    const inst = (geo, mat, list, f) => { const m = new THREE.InstancedMesh(geo, mat, list.length), o = new THREE.Object3D(), c = new THREE.Color();
      list.forEach((p, i) => { f(o, p); o.updateMatrix(); m.setMatrixAt(i, o.matrix); if (p[4]) m.setColorAt(i, c.set(p[4])); }); m.castShadow = true; m.receiveShadow = true; this.scene.add(m); };
    inst(new THREE.BoxGeometry(1, 0.14, 1), std('#9b6b3c', { roughness: 0.9, metalness: 0 }), pal, (o, p) => { o.position.set(...p); o.scale.set(0.95, 1, 0.95); });
    inst(new THREE.BoxGeometry(1, 1, 1), std('#ffffff', { roughness: 0.6, metalness: 0 }), loads, (o, p) => { o.position.set(p[0], p[1], p[2]); o.scale.set(0.88, p[3], 0.88); });
  }

  _buildPacking() {
    for (const k of Object.keys(this.map.zones).filter(k => k.startsWith('packing'))) {
      const z = this.map.zones[k], g = new THREE.Group();
      g.add(box(z.w - 1, 0.85, 0.8, std('#33476f'), z.x + z.w / 2, 0.425, z.y + 0.6));
      g.add(box(z.w - 1, 0.04, 0.82, std('#cdd6e6', { metalness: 0.6 }), z.x + z.w / 2, 0.87, z.y + 0.6));
      g.add(box(z.w - 1.2, 0.06, 0.4, std('#1b2438', { roughness: 0.4 }), z.x + z.w / 2, 0.92, z.y + 0.6));
      for (let i = 0; i < 3; i++) g.add(box(0.4, 0.3, 0.35, std('#b98c5a', { roughness: 0.9, metalness: 0 }), z.x + 1 + i * 1.6, 1.1, z.y + 0.6));
      const mon = box(0.5, 0.32, 0.04, glow('#2E9FE8', 0.6), z.x + z.w - 1.2, 1.35, z.y + 0.35); g.add(mon);
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
      p.load = new THREE.Group();
      p.load.add(box(0.62, 0.3, 0.48, std('#2f6fd6', { roughness: 0.5 }), 0, 0.46, 0));
      p.load.add(box(0.5, 0.06, 0.36, std('#b98c5a', { metalness: 0 }), 0, 0.62, 0));
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
      p.load = box(0.56, 0.3, 0.44, std('#2f6fd6'), 0, 0.18, 0); p.platform.add(p.load);
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
  _disposeRobot(rec) { for (const o of [rec.g, rec.p.ring, rec.p.fault, rec.p.path, rec.p.beam, rec.p.pin, rec.p.stem, rec.p.halo]) if (o) this.scene.remove(o); }

  // ------------------------------------------------------------------ live data
  update(snap) {
    this.snap = snap;
    const seen = new Set();
    for (const r of snap.robots) {
      seen.add(r.id);
      let rec = this.robots.get(r.id);
      if (!rec) { rec = this._makeRobot(r); this.robots.set(r.id, rec); }
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
    for (const m of this.markers.values()) { m.position.y = 0.6 + Math.sin(t * 2.4 + m.userData.phase) * 0.12; m.rotation.y += dt * 1.2; }
    if (this.follow && this.selected && this.robots.has(this.selected)) {
      const p = this.robots.get(this.selected).pos, c = this.controls, d = p.clone().sub(c.target).multiplyScalar(k);
      c.target.add(d); this.camera.position.add(d);
    }
    this.controls.update();
    this._tag();
    this.renderer.render(this.scene, this.camera);
  }

  _animate(rec, dt, t, k) {
    const r = rec.data, p = rec.p, g = rec.g;
    const tx = r.x + 0.5, tz = r.y + 0.5, dx = tx - rec.pos.x, dz = tz - rec.pos.z;
    if (dx * dx + dz * dz > 0.0004) {
      const want = Math.atan2(-dz, dx);
      let diff = want - rec.yaw; diff = Math.atan2(Math.sin(diff), Math.cos(diff)); rec.yaw += diff * Math.min(1, dt * 7);
    }
    rec.pos.x += dx * k; rec.pos.z += dz * k;
    if (Math.abs(dx) > 6 || Math.abs(dz) > 6) { rec.pos.x = tx; rec.pos.z = tz; }        // teleport on reset
    g.position.set(rec.pos.x, 0, rec.pos.z); g.rotation.y = rec.yaw;
    p.led.color.set(STATE_COL[r.state] || '#7f93b5'); p.led.emissive.set(STATE_COL[r.state] || '#7f93b5');
    p.led.emissiveIntensity = r.state === 'charging' ? 1.2 + Math.sin(t * 4) * 0.8 : 1.8;
    if (p.load) p.load.visible = !!r.load;

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
    const ps = sel ? 1.45 : 1.05; p.pin.scale.set(ps, ps, 1);
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
