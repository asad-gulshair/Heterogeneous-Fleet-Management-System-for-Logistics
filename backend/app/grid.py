"""Grid map: which cells are blocked, which zones exist, where racks can be picked from.

Loaded from a layout config (YAML written by hand, or compiled from the Layout Designer).
1 cell = 1 m. Everything is axis-aligned rectangles {x, y, w, h} in cells.

Blocking rules
  racks (shelves)   block ground robots, the drone flies over them
  walls, no-go      block every robot, the drone included
Zones carry a `type` (packing, pallet_area, dock, charging); hand-written configs may leave it out and
the type is taken from the zone name (packing_1 -> packing, loading_dock -> dock ...).
"""
from __future__ import annotations
from collections import deque

ZONE_TYPES = ("packing", "pallet_area", "dock", "charging")
FACES = {"h": ("S", "N"), "v": ("E", "W")}          # pick faces per rack orientation, preferred first
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def zone_type(name: str, z: dict) -> str:
    t = z.get("type")
    if t:
        return t
    n = name.lower()
    if n.startswith("pack"):
        return "packing"
    if n.startswith("pallet"):
        return "pallet_area"
    if "dock" in n:
        return "dock"
    if n.startswith("charg"):
        return "charging"
    return "other"


def cells_of(r: dict):
    return ((x, y) for x in range(r["x"], r["x"] + r["w"]) for y in range(r["y"], r["y"] + r["h"]))


class Grid:
    def __init__(self, cfg: dict):
        self.width = cfg["width"]
        self.height = cfg["height"]
        self.shelves = cfg.get("shelves", [])
        for s in self.shelves:
            s.setdefault("orient", "h" if s["w"] >= s["h"] else "v")
        self.walls = cfg.get("walls", [])
        self.nogo = cfg.get("nogo", [])
        self.zones = cfg.get("zones", {})
        self.ztype = {k: zone_type(k, z) for k, z in self.zones.items()}

        # cells no robot can enter (drone included) and cells ground robots cannot enter
        self.hard: set[tuple[int, int]] = {c for r in self.walls + self.nogo for c in cells_of(r) if self.in_bounds(*c)}
        self.blocked: set[tuple[int, int]] = set(self.hard)
        for s in self.shelves:
            self.blocked.update(cells_of(s))

        # Charger bays: in each charging zone one row along its bottom edge, every second cell (then further rows).
        self.chargers: list[tuple[int, int]] = []
        cand = []
        for name in self.zones_of("charging"):
            z = self.zones[name]
            rows = [[(x, y) for x in range(z["x"] + 1, z["x"] + z["w"] - 1, 2) if (x, y) not in self.blocked]
                    for y in range(z["y"] + z["h"] - 1, z["y"] - 1, -2)]
            cand.append(rows)
        if cand:
            default = sum(len(r[0]) for r in cand if r)
            n = cfg.get("charging_bays", default)
            flat = [c for zr in cand for row in zr for c in row]
            # first rows of every zone first, so bays spread over all charging areas
            first = [c for zr in cand for c in (zr[0] if zr else [])]
            order = first + [c for c in flat if c not in first]
            self.chargers = order[:max(1, min(n, len(order)))] if order else []

        # Ground cells reachable from the charging area (robots start there). Used to drop unreachable pick faces.
        self.reach = self._reachable()

        # Rack faces: for every rack the list of (bay, face) robots can stand at.
        self.faces: list[list[tuple[int, str]]] = []
        self.rows = sorted(self.shelves, key=lambda s: (s["y"], s["x"]))
        for s in self.rows:
            ok = []
            for bay in range(self.rack_len(s)):
                for f in FACES[s["orient"]]:
                    a = self._face_cells(s, bay, f)[0]
                    if a in self.reach:
                        ok.append((bay, f))
                        break
            self.faces.append(ok)

        # Aisles: the two-cell lane in front of each rack's main face (index = rack, 0 = A1). Can be closed.
        self.aisles = []
        for i, s in enumerate(self.rows):
            f = self.faces[i][0][1] if self.faces[i] else FACES[s["orient"]][0]
            cells = set()
            for bay in range(self.rack_len(s)):
                for c in self._face_cells(s, bay, f):
                    if self.in_bounds(*c) and c not in self.blocked:
                        cells.add(c)
            self.aisles.append(cells)
        self.closed: set = set()        # cells closed for maintenance (very expensive for A*, never a work site)

    # ------------------------------------------------------------ geometry helpers
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def passable(self, x: int, y: int, flying: bool = False) -> bool:
        """Drones fly over racks; walls and no-go zones stop everything."""
        if not self.in_bounds(x, y) or (x, y) in self.hard:
            return False
        return flying or (x, y) not in self.blocked

    def zones_of(self, t: str) -> list[str]:
        return sorted(k for k, v in self.ztype.items() if v == t)

    @staticmethod
    def rack_len(s: dict) -> int:
        return s["w"] if s["orient"] == "h" else s["h"]

    def _face_cells(self, s: dict, bay: int, face: str):
        """(a, b): a = cell right in front of the bay, b = one further out (second robot for a hand-over)."""
        x, y, w, h = s["x"], s["y"], s["w"], s["h"]
        if face == "S":
            return (x + bay, y + h), (x + bay, y + h + 1)
        if face == "N":
            return (x + bay, y - 1), (x + bay, y - 2)
        if face == "E":
            return (x + w, y + bay), (x + w + 1, y + bay)
        return (x - 1, y + bay), (x - 2, y + bay)

    def face(self, row: int, bay: int, face: str | None = None):
        """Work cells in front of a rack bay. b falls back to a free neighbour when the aisle is one cell wide."""
        s = self.rows[row]
        if face is None:
            face = next((f for b, f in self.faces[row] if b == bay), FACES[s["orient"]][0])
        a, b = self._face_cells(s, bay, face)
        if b not in self.reach:
            alt = [n for n in ((a[0] + dx, a[1] + dy) for dx, dy in N4) if n in self.reach and n != a]
            b = alt[0] if alt else a
        return a, b

    def scan_line(self, row: int):
        s = self.rows[row]
        if s["orient"] == "h":
            return (s["x"], s["y"]), (s["x"] + s["w"] - 1, s["y"])
        return (s["x"], s["y"]), (s["x"], s["y"] + s["h"] - 1)

    def rack_name(self, row: int) -> str:
        return self.rows[row].get("name") or f"A{row + 1}"

    def _reachable(self) -> set:
        starts = [c for n in self.zones_of("charging") for c in cells_of(self.zones[n]) if self.passable(*c)]
        if not starts:
            starts = [(x, y) for x in range(self.width) for y in range(self.height) if self.passable(x, y)][:1]
        seen = set(starts)
        q = deque(starts)
        while q:
            cx, cy = q.popleft()
            for dx, dy in N4:
                n = (cx + dx, cy + dy)
                if n not in seen and self.passable(*n):
                    seen.add(n)
                    q.append(n)
        return seen

    def nearest_free(self, x: int, y: int, flying: bool = False):
        """BFS outward from (x,y) to the closest passable cell (used when a click lands on a shelf)."""
        if self.passable(x, y, flying):
            return (x, y)
        seen = {(x, y)}
        q = deque([(x, y)])
        while q:
            cx, cy = q.popleft()
            for dx, dy in N4:
                n = (cx + dx, cy + dy)
                if n in seen or not self.in_bounds(*n):
                    continue
                if self.passable(*n, flying):
                    return n
                seen.add(n)
                q.append(n)
        return None

    def free_cell_in(self, name: str, rng) -> tuple:
        """Random free cell inside a zone (same random draws as before for clean zones)."""
        z = self.zones[name]
        c = (rng.randrange(z["x"], z["x"] + z["w"]), rng.randrange(z["y"], z["y"] + z["h"]))
        if c in self.reach:
            return c
        free = [p for p in cells_of(z) if p in self.reach]
        return rng.choice(free) if free else self.nearest_free(*c)

    def zone_center(self, name: str):
        z = self.zones[name]
        return (z["x"] + z["w"] // 2, z["y"] + z["h"] // 2)

    def to_dict(self) -> dict:
        """Static map data sent once to the browser."""
        def box(a):
            return ([min(c[0] for c in a), min(c[1] for c in a), max(c[0] for c in a) - min(c[0] for c in a) + 1,
                     max(c[1] for c in a) - min(c[1] for c in a) + 1] if a else None)
        return {
            "width": self.width,
            "height": self.height,
            "shelves": self.rows,
            "walls": self.walls,
            "nogo": self.nogo,
            "zones": {k: dict(z, type=self.ztype[k]) for k, z in self.zones.items()},
            "chargers": [list(c) for c in self.chargers],
            "aisles": [box(a) for a in self.aisles],
        }
