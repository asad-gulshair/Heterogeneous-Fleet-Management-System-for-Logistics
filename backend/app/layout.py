"""Layout Designer back end: layout model -> simulation config, plus the problem checker.

A layout is vendor-neutral JSON in metres (see docs/TASK2_PLAN.md):
    {"schema": "tuscore.layout/1", "name", "width_m", "height_m", "grid_m": 1,
     "elements": [{"id", "type", "x", "y", "w", "h", ...}], "robots": {type: count}, "orders": {kind: per hour}}

compile_layout() rasterises it onto the 1 m simulation grid and writes the same config schema as the hand-written
Task 1 YAML files, so allocation, traffic, comparison and what-if run on drawn layouts with no special cases.
check_layout() returns the problems list shown in the designer (errors block a run, warnings explain risk).
"""
from __future__ import annotations
import copy
import math
import re
from collections import deque
from pathlib import Path

import yaml

from .grid import Grid, zone_type, cells_of, FACES

SCHEMA = "tuscore.layout/1"
BASE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "warehouse.yaml"

SOLIDS = ("wall", "rack", "nogo")
ZONES = ("packing", "dock", "pallet_area", "charging")
ELEMENTS = {
    "rack":        {"label": "Rack",             "color": "#4b6cc0", "default": [10, 2], "hint": "Shelving. Picked from its long sides; the drone flies over it."},
    "wall":        {"label": "Wall",             "color": "#8b97ad", "default": [10, 1], "hint": "Blocks every robot, the drone included."},
    "nogo":        {"label": "No-go zone",       "color": "#e5484d", "default": [3, 3],  "hint": "People-only or unsafe area. No robot enters."},
    "packing":     {"label": "Packing station",  "color": "#1f6f5c", "default": [6, 4],  "hint": "Tote AMRs deliver picked cartons here."},
    "dock":        {"label": "Loading dock",     "color": "#7a5a1f", "default": [8, 8],  "hint": "Pallet stackers bring pallets here for the truck."},
    "pallet_area": {"label": "Pallet area",      "color": "#3b4a6b", "default": [12, 10], "hint": "Pallets wait here to be moved to a dock."},
    "charging":    {"label": "Charging area",    "color": "#2E9FE8", "default": [12, 4], "hint": "Robots park and charge here. Every robot starts here."},
}
SAFETY_M = 0.15          # clearance each side of a robot in an aisle

# Robot catalog: what the designer offers. Behaviour values come from config/warehouse.yaml.
# Every robot type does exactly one job (role). A custom type is "a robot that does job X with this size,
# speed, battery, price and protocol"; the fleet manager plans it exactly like the built-in robot of that job.
ROLES = {
    "carry_tote":  {"label": "Tote carrier",        "model": "tote_amr",       "does": "Carries totes from racks to packing"},
    "high_pick":   {"label": "High-shelf picker",   "model": "scissor_lift",   "does": "Picks from the top levels, hands totes to a tote carrier"},
    "move_pallet": {"label": "Pallet mover",        "model": "pallet_stacker", "does": "Moves pallets from the pallet area to a dock"},
    "scan":        {"label": "Inventory scanner (flying)", "model": "drone",   "does": "Flies over racks and counts stock"},
}
ADAPTERS = {"vda5050": "VDA 5050 / MQTT", "rest": "REST API", "tcp_json": "TCP / JSON", "mavlink": "MAVLink-style"}
LIMITS = {"width_m": (0.2, 3.0), "length_m": (0.2, 4.0), "speed_mps": (0.1, 3.0), "battery_kwh": (0.05, 30.0), "cost_eur": (0, 2_000_000)}
CUSTOM_KEY = re.compile(r"^c_[a-z0-9_]{1,30}$")
CATALOG_EXTRA = {
    "tote_amr":       {"width_m": 0.6, "length_m": 0.8, "cost_eur": 35000, "does": "Carries totes from racks to packing"},
    "scissor_lift":   {"width_m": 0.9, "length_m": 1.3, "cost_eur": 65000, "does": "Picks from the top levels, hands totes to a tote AMR"},
    "pallet_stacker": {"width_m": 1.0, "length_m": 1.8, "cost_eur": 80000, "does": "Moves pallets from the pallet area to a dock"},
    "drone":          {"width_m": 0.5, "length_m": 0.5, "cost_eur": 20000, "does": "Flies over racks and counts stock"},
}
# which order kind needs which places and which jobs (robot roles)
ORDER_NEEDS = {
    "box_pick":       {"zones": ["packing"], "racks": True,  "roles": ["carry_tote"]},
    "high_pick":      {"zones": ["packing"], "racks": True,  "roles": ["high_pick", "carry_tote"]},
    "pallet_move":    {"zones": ["pallet_area", "dock"], "racks": False, "roles": ["move_pallet"]},
    "inventory_scan": {"zones": [], "racks": True, "roles": ["scan"]},
}
CAPS = {"box_pick": "carry_tote", "high_pick": "high_pick", "pallet_move": "move_pallet", "inventory_scan": "scan"}


def base_config() -> dict:
    return yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))


def catalog(library: dict | None = None) -> dict:
    base = base_config()
    robots = {}
    for k, t in base["robot_types"].items():
        robots[k] = {"label": t["label"], "vendor": t["vendor"], "interface": t["interface"], "adapter": t["adapter"],
                     "speed_mps": t["speed"], "flying": t["flying"], "battery_kwh": t["battery_kwh"], "color": t.get("color"),
                     "default_count": t["count"], "role": t["capabilities"][0], "model": k, "builtin": True,
                     **CATALOG_EXTRA.get(k, {})}
    return {"elements": ELEMENTS, "robots": robots, "orders": base["orders"]["rate_per_hour"],
            "roles": ROLES, "adapters": ADAPTERS, "limits": LIMITS, "library": library or {},
            "safety_m": SAFETY_M, "grid_m": 1}


def clean_robot(d: dict) -> dict | None:
    """A custom robot type from the user, clamped to sane values. None if it is not usable."""
    if not isinstance(d, dict) or d.get("role") not in ROLES:
        return None
    num = lambda k, dv: float(max(LIMITS[k][0], min(LIMITS[k][1], float(d.get(k) if d.get(k) not in (None, "") else dv))))
    role = d["role"]
    base = CATALOG_EXTRA[ROLES[role]["model"]]
    color = str(d.get("color") or "")
    out = {"label": (str(d.get("label") or "").strip() or ROLES[role]["label"])[:40], "role": role,
           "vendor": (str(d.get("vendor") or "").strip() or "Custom")[:40],
           "adapter": d.get("adapter") if d.get("adapter") in ADAPTERS else "vda5050",
           "width_m": round(num("width_m", base["width_m"]), 2), "length_m": round(num("length_m", base["length_m"]), 2),
           "speed_mps": round(num("speed_mps", 1.0), 2), "battery_kwh": round(num("battery_kwh", 1.5), 2),
           "cost_eur": int(num("cost_eur", base["cost_eur"])),
           "color": color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#a78bfa"}
    out["interface"] = ADAPTERS[out["adapter"]]
    out["flying"] = role == "scan"
    out["model"] = ROLES[role]["model"]
    out["does"] = ROLES[role]["does"]
    return out


def robot_key(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (label or "robot").lower()).strip("_")[:24] or "robot"
    return "c_" + s


def robot_types(L: dict) -> dict:
    """Every robot type a (normalised) layout can use: the built-in catalog plus its own custom types."""
    out = dict(catalog()["robots"])
    for k, d in (L.get("custom_robots") or {}).items():
        out[k] = dict(d, default_count=0, builtin=False)
    return out


def need_cells(width_m: float) -> int:
    """Aisle width (whole cells) a robot of this width needs, with the safety margin on both sides."""
    return max(1, math.ceil(width_m + 2 * SAFETY_M - 1e-9))


# ---------------------------------------------------------------- normalise
def _rect(e: dict, W: int, H: int) -> dict:
    x, y = int(round(float(e.get("x", 0)))), int(round(float(e.get("y", 0))))
    w, h = max(1, int(round(float(e.get("w", 1))))), max(1, int(round(float(e.get("h", 1)))))
    return {"x": x, "y": y, "w": w, "h": h}


def normalise(layout: dict) -> dict:
    """Fill defaults, snap to the grid, drop unknown element types. Never raises on user input."""
    L = copy.deepcopy(layout or {})
    L["schema"] = SCHEMA
    L["name"] = str(L.get("name") or "Untitled layout")[:80]
    L["width_m"] = int(max(1, min(500, round(float(L.get("width_m") or 60)))))
    L["height_m"] = int(max(1, min(500, round(float(L.get("height_m") or 40)))))
    L["grid_m"] = 1
    els = []
    for i, e in enumerate(L.get("elements") or []):
        if e.get("type") not in ELEMENTS:
            continue
        r = _rect(e, L["width_m"], L["height_m"])
        ne = {"id": str(e.get("id") or f"e{i + 1}"), "type": e["type"], **r}
        if e.get("name"):
            ne["name"] = str(e["name"])[:24]
        if e["type"] == "rack":
            ne["levels"] = int(max(1, min(8, int(e.get("levels") or 4))))
        if e["type"] == "charging" and e.get("bays"):
            ne["bays"] = int(max(1, min(60, int(e["bays"]))))
        els.append(ne)
    L["elements"] = els
    cat = catalog()
    custom = {}
    for k, d in list((L.get("custom_robots") or {}).items())[:12]:
        c = clean_robot(d)
        if c and CUSTOM_KEY.match(str(k)) and k not in cat["robots"]:
            custom[k] = c
    L["custom_robots"] = custom
    given = L.get("robots") or {}
    L["robots"] = {k: int(max(0, min(200, int(given.get(k, v["default_count"]) or 0)))) for k, v in cat["robots"].items()}
    L["robots"].update({k: int(max(0, min(200, int(given.get(k, 0) or 0)))) for k in custom})
    L["orders"] = {k: float(max(0, min(5000, float((L.get("orders") or {}).get(k, v) or 0))))
                   for k, v in cat["orders"].items()}
    L["background"] = clean_background(L.get("background"))
    return L


BG_ID = re.compile(r"^[0-9a-f]{16,64}\.(png|jpg)$")


def clean_background(b) -> dict | None:
    """Floor plan under the drawing: an uploaded image placed in metres. Only the designer uses it."""
    if not isinstance(b, dict) or not BG_ID.match(str(b.get("id") or "")):
        return None
    f = lambda k, dv, lo, hi: float(max(lo, min(hi, float(b.get(k) if b.get(k) is not None else dv))))
    return {"id": b["id"], "name": str(b.get("name") or "")[:120],
            "w_px": int(f("w_px", 1, 1, 20000)), "h_px": int(f("h_px", 1, 1, 20000)),
            "x": round(f("x", 0, -5000, 5000), 3), "y": round(f("y", 0, -5000, 5000), 3),
            "m_per_px": f("m_per_px", 0.05, 1e-5, 10), "rot": round(f("rot", 0, -360, 360), 2),
            "opacity": round(f("opacity", 0.55, 0.05, 1), 2), "locked": bool(b.get("locked")), "invert": bool(b.get("invert")),
            "show": b.get("show", True) is not False, "scaled": bool(b.get("scaled")),
            "page": int(f("page", 1, 1, 9999)), "pages": int(f("pages", 1, 1, 9999)),
            "source": str(b.get("source") or "")[:80]}


# ---------------------------------------------------------------- compile
def _names(L: dict) -> dict:
    """Stable names per element id: racks A1.. (top to bottom), zones packing_1, dock_1 ..."""
    out, count = {}, {}
    racks = sorted([e for e in L["elements"] if e["type"] == "rack"], key=lambda e: (e["y"], e["x"]))
    for i, e in enumerate(racks):
        out[e["id"]] = e.get("name") or f"A{i + 1}"
    for e in sorted([e for e in L["elements"] if e["type"] in ZONES], key=lambda e: (e["y"], e["x"])):
        count[e["type"]] = count.get(e["type"], 0) + 1
        out[e["id"]] = f"{e['type']}_{count[e['type']]}"
    return out


def _clip(r: dict, W: int, H: int):
    x0, y0 = max(0, r["x"]), max(0, r["y"])
    x1, y1 = min(W, r["x"] + r["w"]), min(H, r["y"] + r["h"])
    return None if x1 <= x0 or y1 <= y0 else {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def to_config(layout: dict, effective_orders: dict | None = None) -> dict:
    """Layout -> simulation config (same schema as config/warehouse.yaml)."""
    L = normalise(layout)
    W, H = L["width_m"], L["height_m"]
    cfg = base_config()
    names = _names(L)
    cfg["name"] = L["name"]
    cfg["width"], cfg["height"] = W, H
    cfg["shelves"], cfg["walls"], cfg["nogo"], cfg["zones"] = [], [], [], {}
    bays = 0
    for e in L["elements"]:
        r = _clip(e, W, H)
        if not r:
            continue
        if e["type"] == "rack":
            cfg["shelves"].append(dict(r, name=names[e["id"]], levels=e.get("levels", 4), orient="h" if r["w"] >= r["h"] else "v",
                                       id=e["id"]))
        elif e["type"] == "wall":
            cfg["walls"].append(dict(r, id=e["id"]))
        elif e["type"] == "nogo":
            cfg["nogo"].append(dict(r, id=e["id"], **({"name": e["name"]} if e.get("name") else {})))
        else:
            cfg["zones"][names[e["id"]]] = dict(r, type=e["type"], color=ELEMENTS[e["type"]]["color"], id=e["id"])
            bays += e.get("bays", 0)
    if bays:
        cfg["charging_bays"] = bays
    for k, t in cfg["robot_types"].items():
        t["count"] = L["robots"].get(k, 0)
        t["model"] = k
        t.update({f: CATALOG_EXTRA[k][f] for f in ("width_m", "length_m", "cost_eur") if k in CATALOG_EXTRA})
    for k, d in L["custom_robots"].items():           # custom type = the built-in robot of that job with new numbers
        t = copy.deepcopy(cfg["robot_types"][d["model"]])
        t.update({"label": d["label"], "vendor": d["vendor"], "interface": d["interface"], "adapter": d["adapter"],
                  "speed": d["speed_mps"], "battery_kwh": d["battery_kwh"], "color": d["color"], "flying": d["flying"],
                  "capabilities": [d["role"]], "count": L["robots"].get(k, 0), "model": d["model"],
                  "width_m": d["width_m"], "length_m": d["length_m"], "cost_eur": d["cost_eur"], "custom": True})
        cfg["robot_types"][k] = t
    cfg["orders"]["rate_per_hour"] = dict(effective_orders if effective_orders is not None else L["orders"])
    cfg["layout_source"] = {"schema": SCHEMA, "name": L["name"]}
    # separate-manager chargers must not exceed the bays
    return cfg


def from_config(cfg: dict, name: str | None = None) -> dict:
    """Hand-written Task 1 config -> layout (used to turn the example YAML files into designer templates)."""
    els = []
    for i, s in enumerate(sorted(cfg.get("shelves", []), key=lambda s: (s["y"], s["x"]))):
        els.append({"id": f"r{i + 1}", "type": "rack", "x": s["x"], "y": s["y"], "w": s["w"], "h": s["h"], "levels": 4})
    for i, w in enumerate(cfg.get("walls", [])):
        els.append({"id": f"w{i + 1}", "type": "wall", **{k: w[k] for k in "xywh"}})
    for i, w in enumerate(cfg.get("nogo", [])):
        els.append({"id": f"n{i + 1}", "type": "nogo", **{k: w[k] for k in "xywh"}})
    for k, z in cfg.get("zones", {}).items():
        t = zone_type(k, z)
        if t in ZONES:
            e = {"id": f"z_{k}", "type": t, **{c: z[c] for c in "xywh"}}
            if t == "charging" and cfg.get("charging_bays"):
                e["bays"] = cfg["charging_bays"]
            els.append(e)
    return normalise({"name": name or cfg.get("name"), "width_m": cfg["width"], "height_m": cfg["height"], "elements": els,
                      "robots": {k: t["count"] for k, t in cfg["robot_types"].items()},
                      "orders": cfg["orders"]["rate_per_hour"]})


# ---------------------------------------------------------------- checks
def _runs(grid: Grid):
    """Length of the free horizontal and vertical run through every free ground cell (aisle width in cells)."""
    W, H = grid.width, grid.height
    free = lambda x, y: grid.in_bounds(x, y) and (x, y) not in grid.blocked
    rx, ry = {}, {}
    for y in range(H):
        x = 0
        while x < W:
            if free(x, y):
                x0 = x
                while x < W and free(x, y):
                    x += 1
                for xx in range(x0, x):
                    rx[(xx, y)] = x - x0
            x += 1
    for x in range(W):
        y = 0
        while y < H:
            if free(x, y):
                y0 = y
                while y < H and free(x, y):
                    y += 1
                for yy in range(y0, y):
                    ry[(x, yy)] = y - y0
            y += 1
    return rx, ry


def _bfs(starts, ok):
    seen = set(s for s in starts if ok(s))
    q = deque(seen)
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cx + dx, cy + dy)
            if n not in seen and ok(n):
                seen.add(n)
                q.append(n)
    return seen


def _overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def check_layout(layout: dict) -> dict:
    """Problems + stats for the designer. level: error (blocks run) | warning | info."""
    L = normalise(layout)
    W, H = L["width_m"], L["height_m"]
    P = []

    def add(level, code, msg, ids=(), cells=None, fix=None):
        P.append({"level": level, "code": code, "msg": msg, "ids": list(ids), "cells": cells or [], "fix": fix})

    els = L["elements"]
    by = lambda t: [e for e in els if e["type"] == t]
    names = _names(L)
    label = lambda e: f"{ELEMENTS[e['type']]['label']} {names.get(e['id'], '')}".strip()
    cat = robot_types(L)
    robots = L["robots"]
    role_n = lambda role: sum(n for k, n in robots.items() if n and cat[k]["role"] == role)

    def widest(role):
        """The widest robot of this job in the fleet (every robot sent to an aisle must fit), else the catalog one."""
        have = [cat[k] for k, n in robots.items() if n and cat[k]["role"] == role]
        return max(have, key=lambda r: r["width_m"]) if have else cat[ROLES[role]["model"]]

    if W < 10 or H < 10:
        add("error", "floor_small", f"Floor is {W} x {H} m. Use at least 10 x 10 m.")
    if W * H > 120000:
        add("warning", "floor_large", f"Floor is {W} x {H} m ({W * H:,} m²). Check the floor plan scale.")

    for e in els:
        if e["x"] < 0 or e["y"] < 0 or e["x"] + e["w"] > W or e["y"] + e["h"] > H:
            add("error", "outside", f"{label(e)} is partly outside the floor.", [e["id"]], fix="Move it inside the floor.")
    for i, a in enumerate(els):
        for b in els[i + 1:]:
            if not _overlap(a, b):
                continue
            sa, sb = a["type"] in SOLIDS, b["type"] in SOLIDS
            if sa and sb:
                if a["type"] != b["type"]:
                    add("warning", "overlap", f"{label(a)} overlaps {label(b)}.", [a["id"], b["id"]])
            else:
                add("error", "overlap", f"{label(a)} overlaps {label(b)}. Work areas must be clear floor.",
                    [a["id"], b["id"]], fix="Move one of them.")

    if not by("charging"):
        add("error", "no_charging", "Add a charging area. Robots park and charge there, and every robot starts there.")
    if sum(robots.values()) == 0:
        add("error", "no_robots", "Add at least one robot.")

    # which order kinds this site can actually produce
    have = {t: bool(by(t)) for t in ZONES}
    have_racks = bool(by("rack"))
    eff = dict(L["orders"])
    for kind, need in ORDER_NEEDS.items():
        if L["orders"].get(kind, 0) <= 0:
            continue
        missing = [ELEMENTS[z]["label"].lower() for z in need["zones"] if not have[z]] + (["racks"] if need["racks"] and not have_racks else [])
        no_robot = [ROLES[r]["label"].lower() for r in need["roles"] if role_n(r) == 0]
        if missing:
            eff[kind] = 0
            add("info", "kind_off", f"{kind.replace('_', ' ').capitalize()} orders are switched off: no {' or '.join(missing)}.")
        elif no_robot:
            add("error", "kind_no_robot", f"{L['orders'][kind]:.0f} {kind.replace('_', ' ')} orders/h but no {' and no '.join(no_robot)}. "
                "Add a robot that does this job or set that order rate to 0.")
    for r, n in robots.items():
        if n and all(eff.get(k, 0) == 0 for k, v in ORDER_NEEDS.items() if cat[r]["role"] in v["roles"]):
            add("warning", "robot_idle", f"{n} x {cat[r]['label']} will have nothing to do in this layout.")

    cfg = to_config(L, eff)
    grid = Grid(copy.deepcopy(cfg))
    reach = grid.reach

    # stations reachable from the charging area
    for e in els:
        if e["type"] in ("packing", "dock", "pallet_area") and _clip(e, W, H):
            if not any(c in reach for c in cells_of(_clip(e, W, H))):
                add("error", "unreachable", f"{label(e)} cannot be reached from the charging area.", [e["id"]],
                    fix="Open a path: move a wall, rack or no-go zone.")
    zs = [n for n in grid.zones_of("charging")]
    if zs and not any(c in reach for n in zs for c in cells_of(grid.zones[n])):
        add("error", "charging_blocked", "The charging area has no free floor.", [e["id"] for e in by("charging")])

    # racks: reachable faces, aisle width for the scissor lift
    rx, ry = _runs(grid)
    lift_r, tote_r = widest("high_pick"), widest("carry_tote")
    lift_need, tote_need = need_cells(lift_r["width_m"]), need_cells(tote_r["width_m"])
    rack_ids = {s["id"]: i for i, s in enumerate(grid.rows)}
    lift_bays_total = 0
    for e in by("rack"):
        if e["id"] not in rack_ids:
            continue
        i = rack_ids[e["id"]]
        s, faces = grid.rows[i], grid.faces[i]
        n = grid.rack_len(s)
        if not faces:
            add("error", "rack_no_face", f"Rack {s['name']} cannot be picked from: both long sides are blocked or unreachable.",
                [e["id"]], fix="Leave an aisle along one long side.")
            continue
        if len(faces) < n:
            add("warning", "rack_partial", f"Rack {s['name']}: {n - len(faces)} of {n} bays cannot be reached.", [e["id"]])
        widths, lift_bays = [], []
        for bay, f0 in faces:                      # widest reachable face of every bay
            best, best_f = 0, f0
            for f in FACES[s["orient"]]:
                a = grid._face_cells(s, bay, f)[0]
                if a in reach:
                    w = ry.get(a, 0) if s["orient"] == "h" else rx.get(a, 0)
                    if w > best:
                        best, best_f = w, f
            widths.append(best)
            if best >= lift_need:
                lift_bays.append([bay, best_f])
        narrow = [w for w in widths if w < tote_need]
        if narrow:
            add("error", "aisle_tote", f"Aisle at rack {s['name']} is {min(widths)} m wide; the {tote_r['label']} needs {tote_need} m "
                f"({tote_r['width_m']} m + {SAFETY_M} m each side).", [e["id"]], fix=f"Widen the aisle to {tote_need} m.")
        lift_ok = sum(1 for w in widths if w >= lift_need)
        lift_bays_total += lift_ok
        s_cfg = next(x for x in cfg["shelves"] if x["id"] == e["id"])
        if lift_ok < len(widths) and role_n("high_pick"):
            m = min(widths)
            add("warning", "aisle_lift", f"Aisle at rack {s['name']} is {m} m wide; the {lift_r['label']} needs {lift_need} m "
                f"({lift_r['width_m']} m + {SAFETY_M} m each side). "
                + ("No high picks from this rack." if lift_ok == 0 else f"High picks only at {lift_ok} of {len(widths)} bays."),
                [e["id"]], fix=f"Widen the aisle to {lift_need} m.")
        if lift_ok < len(widths):
            s_cfg["lift_bays"] = lift_bays          # high picks only where the TL-65 fits
    if role_n("high_pick") and eff.get("high_pick", 0) > 0 and have_racks and lift_bays_total == 0:
        add("error", "no_lift_aisle", f"No rack has an aisle wide enough for the {lift_r['label']}, so high picks are impossible.")

    # pallet mover: wide enough path charging -> pallet area -> dock
    if role_n("move_pallet") and eff.get("pallet_move", 0) > 0:
        pm = widest("move_pallet")
        need = need_cells(pm["width_m"])
        wide = lambda c: c in reach and max(rx.get(c, 0), ry.get(c, 0)) >= need
        starts = [c for n in zs for c in cells_of(grid.zones[n])]
        ok = _bfs(starts, wide)
        for e in by("pallet_area") + by("dock"):
            r = _clip(e, W, H)
            if r and any(c in reach for c in cells_of(r)) and not any(c in ok for c in cells_of(r)):
                add("warning", "aisle_stacker", f"The {pm['label']} ({pm['width_m']} m wide) has no path "
                    f"at least {need} m wide to {label(e)}.", [e["id"]], fix=f"Make a {need} m wide passage.")

    # chargers and parking
    total = sum(robots.values())
    if grid.chargers:
        if total > 4 * len(grid.chargers):
            add("warning", "few_chargers", f"{total} robots share {len(grid.chargers)} charger bays. Expect queues at the chargers.",
                [e["id"] for e in by("charging")], fix="Make the charging area bigger or add one.")
    park = sum(1 for n in zs for c in cells_of(grid.zones[n]) if c in reach)
    if zs and total > park:
        add("warning", "parking", f"{total} robots but only {park} m² of charging area. Extra robots start next to it.",
            [e["id"] for e in by("charging")])

    errors = sum(1 for p in P if p["level"] == "error")
    stats = {"floor_m2": W * H, "racks": len(by("rack")), "rack_bays": sum(grid.rack_len(s) for s in grid.rows),
             "pickable_bays": sum(len(f) for f in grid.faces), "chargers": len(grid.chargers),
             "robots": total, "free_m2": sum(1 for x in range(W) for y in range(H) if (x, y) not in grid.blocked),
             "reachable_m2": len(reach), "orders_per_hour": round(sum(eff.values()), 1)}
    return {"ok": errors == 0, "errors": errors, "warnings": sum(1 for p in P if p["level"] == "warning"),
            "problems": P, "stats": stats, "effective_orders": eff, "config": cfg}


def compile_layout(layout: dict) -> tuple[dict, dict]:
    """(config, report). The config only runs if report['ok']."""
    rep = check_layout(layout)
    return rep.pop("config"), rep
