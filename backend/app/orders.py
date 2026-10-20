"""Order generation: builds tasks (with robot plans) and schedules Poisson arrivals."""
from __future__ import annotations
import random
from .grid import Grid
from .tasks import Action, Task

KINDS = ["box_pick", "high_pick", "pallet_move", "inventory_scan"]
NAMES = {"box_pick": "Box pick", "high_pick": "High-shelf pick", "pallet_move": "Pallet move", "inventory_scan": "Inventory scan"}


class OrderFactory:
    def __init__(self, grid: Grid, cfg: dict, rng: random.Random):
        self.grid, self.rng = grid, rng
        self.dwell = cfg["orders"]["dwell_s"]
        self.p_mismatch = cfg["orders"].get("scan_mismatch_prob", 0.0)
        self.rows = sorted(grid.shelves, key=lambda s: s["y"])   # shelf rows A1..An
        self._next_id = 1

    # --- helpers -----------------------------------------------------
    def _zone_cell(self, name: str) -> tuple:
        z = self.grid.zones[name]
        return (self.rng.randrange(z["x"], z["x"] + z["w"]), self.rng.randrange(z["y"], z["y"] + z["h"]))

    def _shelf_face(self, row: int | None, bay: int | None):
        """Aisle cells in front of a shelf bay: the picker stands on `a`, a second robot can stand on `b`."""
        row = self.rng.randrange(len(self.rows)) if row is None else max(0, min(row, len(self.rows) - 1))
        s = self.rows[row]
        bay = self.rng.randrange(s["w"]) if bay is None else max(0, min(bay, s["w"] - 1))
        x = s["x"] + bay
        a = (x, s["y"] + s["h"])
        b = (x, s["y"] + s["h"] + 1)
        return row, bay, a, b

    # --- order builders ----------------------------------------------
    def make(self, kind: str, now: float, row: int | None = None, bay: int | None = None) -> Task:
        d, tid = self.dwell, self._next_id
        self._next_id += 1
        if kind == "box_pick":
            row, bay, a, _ = self._shelf_face(row, bay)
            pack = self._zone_cell(self.rng.choice(["packing_1", "packing_2"]))
            plans = {"carry_tote": [Action(a, d["pick"], "pick box"), Action(pack, d["drop"], "drop at packing")]}
            title = f"Box A{row + 1}-{bay + 1:02d}"
        elif kind == "high_pick":
            row, bay, a, b = self._shelf_face(row, bay)
            pack = self._zone_cell(self.rng.choice(["packing_1", "packing_2"]))
            plans = {  # scissor lift picks high, tote AMR meets it for the hand-over, then delivers
                "high_pick": [Action(a, d["high_pick"], "pick high shelf"), Action(a, d["handoff"], "hand over", sync="handoff")],
                "carry_tote": [Action(b, d["handoff"], "receive tote", sync="handoff"), Action(pack, d["drop"], "drop at packing")],
            }
            title = f"High pick A{row + 1}-{bay + 1:02d}"
        elif kind == "pallet_move":
            src, dst = self._zone_cell("pallet_area"), self._zone_cell("loading_dock")
            plans = {"move_pallet": [Action(src, d["pallet_pick"], "lift pallet"), Action(dst, d["pallet_drop"], "set down at dock")]}
            title = "Pallet to dock"
        elif kind == "inventory_scan":
            row = self.rng.randrange(len(self.rows)) if row is None else max(0, min(row, len(self.rows) - 1))
            s = self.rows[row]
            plans = {"scan": [Action((s["x"], s["y"]), d["scan"], "start scan"),
                              Action((s["x"] + s["w"] - 1, s["y"]), d["scan"], "finish scan")]}
            title = f"Scan row A{row + 1}"
        else:
            raise ValueError(f"unknown order kind: {kind}")
        t = Task(id=tid, kind=kind, title=title, created=now, plans=plans)
        if kind == "inventory_scan":
            t.mismatch = self.rng.random() < self.p_mismatch
        return t


class ArrivalProcess:
    """Independent Poisson process per order kind (exponential inter-arrival times)."""
    def __init__(self, cfg: dict, rng: random.Random):
        self.rng = rng
        self.rates = cfg["orders"]["rate_per_hour"]
        self.shift = cfg["shift"]["length_s"]
        self.next = {k: self._gap(k, 0.0) for k in self.rates if self.rates[k] > 0}

    def _gap(self, kind: str, t: float) -> float:
        return t + self.rng.expovariate(self.rates[kind] / 3600.0)

    def due(self, now: float):
        """Yield (kind, arrival_time) for every order that arrived up to `now`."""
        for kind in list(self.next):
            while self.next[kind] <= now and self.next[kind] < self.shift:
                yield kind, self.next[kind]
                self.next[kind] = self._gap(kind, self.next[kind])
