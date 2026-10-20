"""Simulation core: owns the grid + robots and advances time."""
import random
import yaml
from .grid import Grid
from .planner import astar
from .robots import Robot


class Simulation:
    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.grid = Grid(self.cfg)
        self.drain = self.cfg.get("battery", {}).get("drain_per_cell", 0.05)
        self.time_scale = 1.0   # >1 = faster than real time
        self.sim_time = 0.0     # simulated seconds
        self.robots: dict[str, Robot] = {}
        self._spawn_robots()

    def _spawn_robots(self):
        # Robots start in the charging area, one free cell each.
        z = self.cfg["zones"]["charging_area"]
        spots = [(x, y) for y in range(z["y"], z["y"] + z["h"]) for x in range(z["x"], z["x"] + z["w"])]
        random.shuffle(spots)
        for tname, t in self.cfg["robot_types"].items():
            for i in range(1, t["count"] + 1):
                rid = f"{tname}_{i}"
                self.robots[rid] = Robot(
                    id=rid, rtype=tname, label=t["label"], vendor=t["vendor"],
                    interface=t["interface"], speed=t["speed"], flying=t["flying"],
                    cell=spots.pop(),
                )

    def tick(self, real_dt: float):
        dt = real_dt * self.time_scale
        self.sim_time += dt
        for r in self.robots.values():
            r.step(dt, self.drain)

    def goto(self, robot_id: str, x: int, y: int):
        """Send a robot to a cell. Returns (ok, message)."""
        r = self.robots.get(robot_id)
        if r is None:
            return False, "unknown robot"
        goal = self.grid.nearest_free(x, y, r.flying)   # snap clicks on shelves to the aisle
        if goal is None:
            return False, "no reachable cell"
        path = astar(self.grid, r.cell, goal, r.flying)
        if path is None:
            return False, "no path"
        r.set_path(path, goal)
        return True, "ok"

    def random_targets(self):
        """Demo helper: send every robot to a random free cell."""
        for r in self.robots.values():
            for _ in range(20):
                x = random.randrange(self.grid.width)
                y = random.randrange(self.grid.height)
                if self.grid.passable(x, y, r.flying):
                    self.goto(r.id, x, y)
                    break

    def snapshot(self) -> dict:
        return {
            "t": round(self.sim_time, 1),
            "scale": self.time_scale,
            "robots": [r.to_dict() for r in self.robots.values()],
        }

    def types_meta(self) -> dict:
        return self.cfg["robot_types"]
