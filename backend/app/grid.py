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
        }
