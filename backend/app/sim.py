"""Simulation core: owns the grid, robots, orders and the fleet-manager logic."""
from __future__ import annotations
import math
import random
from collections import deque
import yaml
from .allocator import ALLOCATORS
from .grid import Grid
from .orders import ArrivalProcess, OrderFactory, KINDS
from .planner import astar
from .robots import Robot
from .tasks import Task

SUBSTEP = 0.5   # max simulated seconds per physics step (keeps fast-forward accurate)


class Simulation:
    def __init__(self, config_path: str, seed: int | None = None, allocator: str = "hungarian"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.grid = Grid(self.cfg)
        b = self.cfg["battery"]
        self.drain, self.charge_rate = b["drain_per_cell"], b["charge_rate_per_s"]
        self.low, self.resume = b["low_threshold"], b["resume_threshold"]
        self.shift_len = self.cfg["shift"]["length_s"]
        self.time_scale = 1.0
        self.auto_orders = True
        self.reset(seed, allocator)

    # ------------------------------------------------------------ setup
    def reset(self, seed: int | None = None, allocator: str | None = None):
        self.seed = self.cfg["shift"]["seed"] if seed is None else seed
        if allocator:
            self.allocator = ALLOCATORS[allocator]()
        self.rng = random.Random(self.seed)
        self.sim_time = 0.0
        self.robots: dict[str, Robot] = {}
        self.tasks: dict[int, Task] = {}      # queued + in progress
        self.queue: list[int] = []            # queued task ids, oldest first
        self.recent: deque = deque(maxlen=40)  # finished tasks (for the UI)
        self.bays: dict[tuple, str | None] = {c: None for c in self.grid.chargers}
        self.factory = OrderFactory(self.grid, self.cfg, self.rng)
        self.arrivals = ArrivalProcess(self.cfg, self.rng)
        self.stats = {"created": 0, "completed": 0, "started": 0, "wait_sum": 0.0, "cycle_sum": 0.0,
                      "mismatches": 0, "by_kind": {k: 0 for k in KINDS}}
        self._spawn_robots()

    def _spawn_robots(self):
        z = self.cfg["zones"]["charging_area"]   # robots start parked in the charging area
        spots = [(x, y) for y in range(z["y"], z["y"] + z["h"]) for x in range(z["x"], z["x"] + z["w"])]
        self.rng.shuffle(spots)
        for tname, t in self.cfg["robot_types"].items():
            for i in range(1, t["count"] + 1):
                self.robots[f"{tname}_{i}"] = Robot(
                    id=f"{tname}_{i}", rtype=tname, label=t["label"], vendor=t["vendor"],
                    interface=t["interface"], speed=t["speed"], flying=t["flying"],
                    capabilities=list(t["capabilities"]), kwh=t["battery_kwh"], cell=spots.pop())

    # ------------------------------------------------------------ time
    def tick(self, real_dt: float):
        dt = real_dt * self.time_scale
        n = max(1, math.ceil(dt / SUBSTEP))
        for _ in range(n):
            self._step(dt / n)

    def _step(self, dt: float):
        self.sim_time += dt
        if self.auto_orders:
            for kind, t in self.arrivals.due(self.sim_time):
                self.add_order(kind, at=t)
        self._allocate()
        for r in self.robots.values():
            self._tick_robot(r, dt)

    def shift_over(self) -> bool:
        return self.sim_time >= self.shift_len and not self.tasks

    def run_headless(self, max_time: float | None = None) -> dict:
        """Run the whole shift as fast as possible (used by tests and the Phase 4 comparison)."""
        limit = max_time or self.shift_len * 1.5
        while self.sim_time < limit and not self.shift_over():
            self._step(SUBSTEP)
        return self.kpis()

    # ------------------------------------------------------------ orders
    def add_order(self, kind: str, row=None, bay=None, at: float | None = None, manual=False):
        t = self.factory.make(kind, self.sim_time if at is None else at, row, bay)
        t.manual = manual
        self.tasks[t.id] = t
        self.queue.append(t.id)
        self.stats["created"] += 1
        return t

    def _allocate(self):
        if not self.queue:
            return
        free = [r for r in self.robots.values()
                if r.state == "idle" and r.task_id is None and r.battery >= self.low]
        if not free:
            return
        queued = [self.tasks[i] for i in self.queue]
        for tid, mapping in self.allocator.allocate(queued, free, self.sim_time).items():
            self._dispatch(self.tasks[tid], mapping)

    def _dispatch(self, t, mapping: dict):
        t.status, t.started, t.robots = "in_progress", self.sim_time, dict(mapping)
        self.queue.remove(t.id)
        self.stats["started"] += 1
        self.stats["wait_sum"] += t.started - t.created
        for role, rid in mapping.items():
            r = self.robots[rid]
            r.task_id, r.role, r.plan, r.idx = t.id, role, t.plans[role], 0
        for rid in mapping.values():
            self._begin_action(self.robots[rid])

    # ------------------------------------------------------------ robot state machine
    def _tick_robot(self, r: Robot, dt: float):
        s = r.state
        if s in ("moving", "to_charge"):
            if s == "moving":
                r.busy_time += dt
            r.step(dt, self.drain)
            if not r.path:
                self._arrived(r)
        elif s == "working":
            r.busy_time += dt
            r.dwell_left -= dt
            if r.dwell_left <= 0:
                self._action_done(r)
        elif s == "waiting":
            r.busy_time += dt
        elif s == "charging":
            r.battery = min(100.0, r.battery + self.charge_rate * dt)
            if r.battery >= self.resume:
                self._release_bay(r)
                r.state = "idle"
        elif s == "idle" and r.task_id is None and r.battery < self.low:
            self._send_to_charge(r)

    def _begin_action(self, r: Robot):
        a = r.plan[r.idx]
        r.action = a.label
        if r.cell == a.cell:
            r.set_path([], None)
            self._arrived(r)
            return
        path = astar(self.grid, r.cell, a.cell, r.flying)
        if path is None:                      # should not happen on a valid layout
            raise RuntimeError(f"no path for {r.id} {r.cell}->{a.cell}")
        r.set_path(path, a.cell)

    def _arrived(self, r: Robot):
        if r.state == "to_charge":
            r.state = "charging"
            return
        if r.task_id is None:                 # manual goto finished
            r.state = "idle"
            return
        t, a = self.tasks[r.task_id], r.plan[r.idx]
        if a.sync:
            waiting = t.sync.setdefault(a.sync, set())
            waiting.add(r.id)
            need = {rid for role, rid in t.robots.items() if any(x.sync == a.sync for x in t.plans[role])}
            if waiting >= need:               # everybody is here -> start the shared action
                for rid in need:
                    rr = self.robots[rid]
                    rr.state, rr.dwell_left = "working", rr.plan[rr.idx].dwell
            else:
                r.state = "waiting"
        else:
            r.state, r.dwell_left = "working", a.dwell

    def _action_done(self, r: Robot):
        t = self.tasks[r.task_id]
        t.done_actions += 1
        r.idx += 1
        if r.idx < len(r.plan):
            self._begin_action(r)
            return
        t.done_roles.add(r.role)
        r.clear_task()
        r.state = "idle"
        if len(t.done_roles) == len(t.plans):
            self._complete(t)

    def _complete(self, t):
        t.status, t.finished = "done", self.sim_time
        del self.tasks[t.id]
        self.recent.appendleft(t)
        s = self.stats
        s["completed"] += 1
        s["cycle_sum"] += t.finished - t.created
        s["by_kind"][t.kind] += 1
        s["mismatches"] += int(t.mismatch)

    # ------------------------------------------------------------ charging
    def _send_to_charge(self, r: Robot):
        free = [c for c, who in self.bays.items() if who is None]
        if not free:
            return                            # wait for a bay; robot stays idle but not allocatable
        bay = min(free, key=lambda c: abs(c[0] - r.cell[0]) + abs(c[1] - r.cell[1]))
        self.bays[bay], r.bay = r.id, bay
        path = astar(self.grid, r.cell, bay, r.flying)
        r.set_path(path or [], bay, state="to_charge")
        if not path:
            r.state = "charging"

    def _release_bay(self, r: Robot):
        if r.bay is not None:
            self.bays[r.bay] = None
            r.bay = None

    # ------------------------------------------------------------ manual control (Phase 1)
    def goto(self, robot_id: str, x: int, y: int):
        r = self.robots.get(robot_id)
        if r is None:
            return False, "unknown robot"
        if r.task_id is not None or r.state in ("charging", "to_charge"):
            return False, "robot is busy"
        goal = self.grid.nearest_free(x, y, r.flying)
        if goal is None:
            return False, "no reachable cell"
        path = astar(self.grid, r.cell, goal, r.flying)
        if path is None:
            return False, "no path"
        r.set_path(path, goal)
        return True, "ok"

    # ------------------------------------------------------------ reporting
    def kpis(self) -> dict:
        s, n = self.stats, len(self.robots)
        hours = max(self.sim_time, 1.0) / 3600.0
        return {
            "created": s["created"], "completed": s["completed"],
            "queued": len(self.queue), "active": len(self.tasks) - len(self.queue),
            "orders_per_hour": round(s["completed"] / hours, 1),
            "avg_wait": round(s["wait_sum"] / s["started"], 1) if s["started"] else 0.0,
            "avg_cycle": round(s["cycle_sum"] / s["completed"], 1) if s["completed"] else 0.0,
            "utilization": round(100 * sum(r.busy_time for r in self.robots.values()) / (n * max(self.sim_time, 1.0)), 1),
            "energy_kwh": round(sum(r.energy_kwh for r in self.robots.values()), 2),
            "mismatches": s["mismatches"], "by_kind": dict(s["by_kind"]),
        }

    def snapshot(self) -> dict:
        now = self.sim_time
        live = [t.to_dict(now) for t in sorted(self.tasks.values(), key=lambda t: t.id)]
        return {
            "t": round(now, 1), "scale": self.time_scale, "shift": self.shift_len,
            "auto_orders": self.auto_orders, "allocator": self.allocator.name, "seed": self.seed,
            "robots": [r.to_dict() for r in self.robots.values()],
            "tasks": live[:80], "recent": [t.to_dict(now) for t in list(self.recent)[:15]],
            "kpis": self.kpis(),
        }

    def types_meta(self) -> dict:
        return self.cfg["robot_types"]
