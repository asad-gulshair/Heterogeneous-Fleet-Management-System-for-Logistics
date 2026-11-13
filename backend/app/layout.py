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
CATALOG_EXTRA = {
    "tote_amr":       {"width_m": 0.6, "length_m": 0.8, "cost_eur": 35000, "does": "Carries totes from racks to packing"},
    "scissor_lift":   {"width_m": 0.9, "length_m": 1.3, "cost_eur": 65000, "does": "Picks from the top levels, hands totes to a tote AMR"},
    "pallet_stacker": {"width_m": 1.0, "length_m": 1.8, "cost_eur": 80000, "does": "Moves pallets from the pallet area to a dock"},
    "drone":          {"width_m": 0.5, "length_m": 0.5, "cost_eur": 20000, "does": "Flies over racks and counts stock"},
}
# which order kind needs which places and which robots
ORDER_NEEDS = {
    "box_pick":       {"zones": ["packing"], "racks": True,  "robots": ["tote_amr"]},
    "high_pick":      {"zones": ["packing"], "racks": True,  "robots": ["scissor_lift", "tote_amr"]},
    "pallet_move":    {"zones": ["pallet_area", "dock"], "racks": False, "robots": ["pallet_stacker"]},
    "inventory_scan": {"zones": [], "racks": True, "robots": ["drone"]},
}
CAPS = {"box_pick": "carry_tote", "high_pick": "high_pick", "pallet_move": "move_pallet", "inventory_scan": "scan"}


def base_config() -> dict:
    return yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))


def catalog() -> dict:
    base = base_config()
    robots = {}
    for k, t in base["robot_types"].items():
        robots[k] = {"label": t["label"], "vendor": t["vendor"], "interface": t["interface"], "speed_mps": t["speed"],
                     "flying": t["flying"], "battery_kwh": t["battery_kwh"], "color": t.get("color"),
                     "default_count": t["count"], **CATALOG_EXTRA.get(k, {})}
    return {"elements": ELEMENTS, "robots": robots, "orders": base["orders"]["rate_per_hour"],
            "safety_m": SAFETY_M, "grid_m": 1}


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
    L["robots"] = {k: int(max(0, min(200, int((L.get("robots") or {}).get(k, v["default_count"]) or 0))))
                   for k, v in cat["robots"].items()}
    L["orders"] = {k: float(max(0, min(5000, float((L.get("orders") or {}).get(k, v) or 0))))
                   for k, v in cat["orders"].items()}
    L.setdefault("background", None)
    return L


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
    cat = catalog()["robots"]
    robots = L["robots"]

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
        no_robot = [cat[r]["label"] for r in need["robots"] if robots.get(r, 0) == 0]
        if missing:
            eff[kind] = 0
            add("info", "kind_off", f"{kind.replace('_', ' ').capitalize()} orders are switched off: no {' or '.join(missing)}.")
        elif no_robot:
            add("error", "kind_no_robot", f"{L['orders'][kind]:.0f} {kind.replace('_', ' ')} orders/h but no {' and no '.join(no_robot)}. "
                "Add the robot or set that order rate to 0.")
    for r, n in robots.items():
        if n and all(eff.get(k, 0) == 0 for k, v in ORDER_NEEDS.items() if r in v["robots"]):
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
    lift_need = need_cells(cat["scissor_lift"]["width_m"])
    tote_need = need_cells(cat["tote_amr"]["width_m"])
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
            add("error", "aisle_tote", f"Aisle at rack {s['name']} is too narrow for the tote AMR.", [e["id"]])
        lift_ok = sum(1 for w in widths if w >= lift_need)
        lift_bays_total += lift_ok
        s_cfg = next(x for x in cfg["shelves"] if x["id"] == e["id"])
        if lift_ok < len(widths) and robots.get("scissor_lift"):
            m = min(widths)
            add("warning", "aisle_lift", f"Aisle at rack {s['name']} is {m} m wide; the TL-65 needs {lift_need} m "
                f"({cat['scissor_lift']['width_m']} m + {SAFETY_M} m each side). "
                + ("No high picks from this rack." if lift_ok == 0 else f"High picks only at {lift_ok} of {len(widths)} bays."),
                [e["id"]], fix=f"Widen the aisle to {lift_need} m.")
        if lift_ok < len(widths):
            s_cfg["lift_bays"] = lift_bays          # high picks only where the TL-65 fits
    if robots.get("scissor_lift") and eff.get("high_pick", 0) > 0 and have_racks and lift_bays_total == 0:
        add("error", "no_lift_aisle", "No rack has an aisle wide enough for the TL-65, so high picks are impossible.")

    # pallet stacker: wide enough path charging -> pallet area -> dock
    if robots.get("pallet_stacker") and eff.get("pallet_move", 0) > 0:
        need = need_cells(cat["pallet_stacker"]["width_m"])
        wide = lambda c: c in reach and max(rx.get(c, 0), ry.get(c, 0)) >= need
        starts = [c for n in zs for c in cells_of(grid.zones[n])]
        ok = _bfs(starts, wide)
        for e in by("pallet_area") + by("dock"):
            r = _clip(e, W, H)
            if r and any(c in reach for c in cells_of(r)) and not any(c in ok for c in cells_of(r)):
                add("warning", "aisle_stacker", f"The pallet stacker ({cat['pallet_stacker']['width_m']} m wide) has no path "
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
