"""Grid map: which cells are blocked, which zones exist. Loaded from YAML."""
from __future__ import annotations
from collections import deque


class Grid:
    def __init__(self, cfg: dict):
        self.width = cfg["width"]
        self.height = cfg["height"]
        self.shelves = cfg.get("shelves", [])
        self.zones = cfg.get("zones", {})
        # Set of (x, y) cells that ground robots cannot enter.
        self.blocked: set[tuple[int, int]] = set()
        for s in self.shelves:
            for x in range(s["x"], s["x"] + s["w"]):
                for y in range(s["y"], s["y"] + s["h"]):
                    self.blocked.add((x, y))

        # Charger bays: one row along the bottom of the charging area, every second cell.
        self.chargers: list[tuple[int, int]] = []
        z = self.zones.get("charging_area")
        if z:
            cand = [(x, y) for y in range(z["y"] + z["h"] - 1, z["y"] - 1, -2)          # bottom row first
                    for x in range(z["x"] + 1, z["x"] + z["w"] - 1, 2)]
            n = cfg.get("charging_bays", len([c for c in cand if c[1] == z["y"] + z["h"] - 1]))
            self.chargers = cand[:max(1, min(n, len(cand)))]

        # Aisles: the two-cell lane in front of each shelf row (index = row, 0 = A1). Can be closed for maintenance.
        self.aisles = []
        for s in sorted(self.shelves, key=lambda s: s["y"]):
            cells = {(x, y) for x in range(s["x"], s["x"] + s["w"]) for y in (s["y"] + s["h"], s["y"] + s["h"] + 1)
                     if 0 <= y < self.height and (x, y) not in self.blocked}
            self.aisles.append(cells)
        self.closed: set = set()        # cells closed for maintenance (very expensive for A*, never a work site)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def passable(self, x: int, y: int, flying: bool = False) -> bool:
        """Drones ignore shelves; everything else treats them as walls."""
        if not self.in_bounds(x, y):
            return False
        return flying or (x, y) not in self.blocked

    def nearest_free(self, x: int, y: int, flying: bool = False):
        """BFS outward from (x,y) to the closest passable cell (used when a click lands on a shelf)."""
        if self.passable(x, y, flying):
            return (x, y)
        seen = {(x, y)}
        q = deque([(x, y)])
        while q:
            cx, cy = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cx + dx, cy + dy)
                if n in seen or not self.in_bounds(*n):
                    continue
                if self.passable(*n, flying):
                    return n
                seen.add(n)
                q.append(n)
        return None

    def zone_center(self, name: str):
        z = self.zones[name]
        return (z["x"] + z["w"] // 2, z["y"] + z["h"] // 2)

    def to_dict(self) -> dict:
        """Static map data sent once to the browser."""
        return {
            "width": self.width,
            "height": self.height,
            "shelves": self.shelves,
            "zones": self.zones,
            "chargers": [list(c) for c in self.chargers],
            "aisles": [[min(c[0] for c in a), min(c[1] for c in a), max(c[0] for c in a) - min(c[0] for c in a) + 1,
                        max(c[1] for c in a) - min(c[1] for c in a) + 1] if a else None for a in self.aisles],
        }
