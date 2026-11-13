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
        self.rows = grid.rows                                    # racks A1..An (sorted top to bottom)
        self.packing = grid.zones_of("packing")
        self.pallets = grid.zones_of("pallet_area")
        self.docks = grid.zones_of("dock")
        self._next_id = 1

    # --- helpers -----------------------------------------------------
    def _zone_cell(self, name: str) -> tuple:
        return self.grid.free_cell_in(name, self.rng)

    def _pick(self, names: list[str]) -> str:
        # one candidate: no random draw (keeps seeded runs of hand-written layouts identical)
        return names[0] if len(names) == 1 else self.rng.choice(names)

    def _pickable_rows(self) -> list[int]:
        return [i for i, f in enumerate(self.grid.faces) if f]

    def _shelf_face(self, row: int | None, bay: int | None, lift: bool = False):
        """Aisle cells in front of a rack bay: the picker stands on `a`, a second robot can stand on `b`.
        Only bays with a reachable face are used (racks against a wall are picked from the open side)."""
        rows = self._pickable_rows()
        if lift:                                  # TL-65 needs a wide enough aisle (set by the layout compiler)
            wide = [i for i in rows if self.rows[i].get("lift_bays", True) != []]
            if row is None and len(wide) < len(rows):
                row = self.rng.choice(wide) if wide else None
            if row is not None and self.rows[row].get("lift_bays") and bay is None:
                bay, face = self.rng.choice(self.rows[row]["lift_bays"])
                a, b = self.grid.face(row, bay, face)
                return row, bay, a, b
        if row is None:
            row = self.rng.randrange(len(self.rows)) if len(rows) == len(self.rows) else self.rng.choice(rows)
        else:
            row = max(0, min(row, len(self.rows) - 1))
        s, faces = self.rows[row], self.grid.faces[row]
        n = self.grid.rack_len(s)
        if bay is None:
            bay = self.rng.randrange(n) if len(faces) == n else self.rng.choice(faces)[0]
        bay = max(0, min(bay, n - 1))
        a, b = self.grid.face(row, bay)
        return row, bay, a, b

    def new_id(self) -> int:
        tid = self._next_id
        self._next_id += 1
        return tid

    # --- order builders ----------------------------------------------
    def make(self, kind: str, now: float, row: int | None = None, bay: int | None = None,
             coordinated: bool = True) -> Task:
        """coordinated=False models separate vendor managers: no synchronized hand-over; the lift
        drops the tote at a transfer point and the tote job is created afterwards by the WMS."""
        d, tid = self.dwell, self._next_id
        self._next_id += 1
        if kind == "box_pick":
            row, bay, a, _ = self._shelf_face(row, bay)
            pack = self._zone_cell(self._pick(self.packing))
            plans = {"carry_tote": [Action(a, d["pick"], "pick box"), Action(pack, d["drop"], "drop at packing")]}
            title = f"Box {self.grid.rack_name(row)}-{bay + 1:02d}"
        elif kind == "high_pick":
            row, bay, a, b = self._shelf_face(row, bay, lift=True)
            pack = self._zone_cell(self._pick(self.packing))
            title = f"High pick {self.grid.rack_name(row)}-{bay + 1:02d}"
            if coordinated:
                plans = {  # scissor lift picks high, tote AMR meets it for the hand-over, then delivers
                    "high_pick": [Action(a, d["high_pick"], "pick high shelf"), Action(a, d["handoff"], "hand over", sync="handoff")],
                    "carry_tote": [Action(b, d["handoff"], "receive tote", sync="handoff"), Action(pack, d["drop"], "drop at packing")],
                }
            else:
                plans = {"high_pick": [Action(a, d["high_pick"], "pick high shelf"), Action(a, d["handoff"], "place at transfer point")]}
                then = ("carry_tote", [Action(b, d["handoff"], "collect tote"), Action(pack, d["drop"], "drop at packing")])
        elif kind == "pallet_move":
            src, dst = self._zone_cell(self._pick(self.pallets)), self._zone_cell(self._pick(self.docks))
            plans = {"move_pallet": [Action(src, d["pallet_pick"], "lift pallet"), Action(dst, d["pallet_drop"], "set down at dock")]}
            title = "Pallet to dock"
        elif kind == "inventory_scan":
            row = self.rng.randrange(len(self.rows)) if row is None else max(0, min(row, len(self.rows) - 1))
            p0, p1 = self.grid.scan_line(row)
            plans = {"scan": [Action(p0, d["scan"], "start scan"), Action(p1, d["scan"], "finish scan")]}
            title = f"Scan row {self.grid.rack_name(row)}"
        else:
            raise ValueError(f"unknown order kind: {kind}")
        t = Task(id=tid, kind=kind, title=title, created=now, plans=plans)
        if kind == "high_pick" and not coordinated:
            t.then = then
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
