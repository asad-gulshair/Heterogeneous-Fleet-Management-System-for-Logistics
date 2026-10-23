"""Simulation core: owns the grid, robots, orders and the fleet-manager logic.

Layers (top to bottom):
  allocator   which free robot takes which order
  sim (here)  task execution state machine, traffic control, charging, faults
  adapters    translate our common commands to each vendor's protocol and back
  robots      the simulated machines (the "plant")
"""
from __future__ import annotations
import bisect
import copy
import math
import random
from collections import deque
import yaml
from .adapters import Bus, build_adapters
from .allocator import ALLOCATORS
from .events import EventLog
from .grid import Grid
from .orders import ArrivalProcess, OrderFactory, KINDS
from .planner import astar
from .robots import Robot
from .tasks import Task
from .traffic import TrafficManager, find_cycles

SUBSTEP = 0.5   # max simulated seconds per physics step (keeps fast-forward accurate)
STATIONARY = ("idle", "working", "waiting", "charging", "fault")
MODES = ("tuscore", "separate")
# what a robot carries after finishing an action (drives the 3D view and the report)
LOAD_AFTER = {"pick box": "tote", "drop at packing": None, "pick high shelf": "tote", "hand over": None,
              "receive tote": "tote", "place at transfer point": None, "collect tote": "tote",
              "lift pallet": "pallet", "set down at dock": None}


def apply_overrides(cfg: dict, ov: dict | None) -> dict:
    """What-if overrides on top of a layout config (robot counts, order rates, chargers ...)."""
    if not ov:
        return cfg
    cfg = copy.deepcopy(cfg)
    for k, v in (ov.get("robot_counts") or {}).items():
        if k in cfg["robot_types"]:
            cfg["robot_types"][k]["count"] = max(0, int(v))
    for k, v in (ov.get("order_rates") or {}).items():
        cfg["orders"]["rate_per_hour"][k] = max(0.0, float(v))
    if ov.get("load_factor"):
        cfg["orders"]["rate_per_hour"] = {k: v * float(ov["load_factor"]) for k, v in cfg["orders"]["rate_per_hour"].items()}
    if ov.get("charging_bays"):
        cfg["charging_bays"] = int(ov["charging_bays"])
    if ov.get("mtbf_hours") is not None:
        cfg.setdefault("faults", {})["mtbf_hours"] = float(ov["mtbf_hours"])
    if ov.get("shift_hours"):
        cfg["shift"]["length_s"] = float(ov["shift_hours"]) * 3600
    return cfg
NEIGH = ((1, 0), (-1, 0), (0, 1), (0, -1))


class Simulation:
    def __init__(self, config_path: str, seed: int | None = None, allocator: str = "hungarian",
                 mode: str = "tuscore", overrides: dict | None = None):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = apply_overrides(yaml.safe_load(f), overrides)
        self.config_path = config_path
        self.mode = mode if mode in MODES else "tuscore"
        self.grid = Grid(self.cfg)
        sep = self.cfg.get("separate_managers", {})
        self.operator_s = sep.get("operator_response_s", 90)
        self.wms_delay = sep.get("wms_handoff_delay_s", 20)
        b, f_, tr = self.cfg["battery"], self.cfg.get("faults", {}), self.cfg.get("traffic", {})
        self.drain, self.charge_rate = b["drain_per_cell"], b["charge_rate_per_s"]
        self.low, self.resume = b["low_threshold"], b["resume_threshold"]
        self.critical, self.opp = b.get("critical_threshold", 12), b.get("opportunistic_threshold", 50)
        self.mtbf_s = f_.get("mtbf_hours", 0) * 3600.0
        self.repair_s = f_.get("repair_s", 120)
        self.block_s, self.replan_s, self.watchdog_s = tr.get("block_s", 1.0), tr.get("replan_s", 3.0), tr.get("watchdog_s", 300)
        self.shift_len = self.cfg["shift"]["length_s"]
        self.time_scale = 1.0
        self.auto_orders = True
        self.publisher = None              # set by the API process to mirror VDA 5050 traffic to Mosquitto
        self.sla_s = self.cfg.get("sla_s", 600)    # an order is "on time" if finished within this many seconds
        self.reset(seed, allocator)

    # ------------------------------------------------------------ setup
    def reset(self, seed: int | None = None, allocator: str | None = None):
        self.seed = self.cfg["shift"]["seed"] if seed is None else seed
        if allocator:
            self.allocator = ALLOCATORS[allocator]()
        self.rng = random.Random(self.seed)
        self.fault_rng = random.Random(self.seed + 1)       # separate stream: faults never change the order stream
        self.sim_time = 0.0
        self.robots: dict[str, Robot] = {}
        self.tasks: dict[int, Task] = {}                    # queued + in progress
        self.queue: list[int] = []                          # queued task ids, oldest first
        self.recent: deque = deque(maxlen=40)
        self.bays: dict[tuple, str | None] = {c: None for c in self.grid.chargers}
        self.traffic = TrafficManager()
        self.events = EventLog()
        self.bus = Bus()
        self.bus.publisher = self.publisher
        self.adapters = build_adapters(self.cfg, self.bus)
        self.factory = OrderFactory(self.grid, self.cfg, self.rng)
        self.arrivals = ArrivalProcess(self.cfg, self.rng)
        self.stats = {"created": 0, "completed": 0, "started": 0, "wait_sum": 0.0, "cycle_sum": 0.0,
                      "mismatches": 0, "by_kind": {k: 0 for k in KINDS},
                      "collisions": 0, "deadlocks": 0, "permanent": 0, "faults": 0, "reassigned": 0,
                      "evasions": 0, "traffic_delay": 0.0, "preempted": 0,
                      "completed_in_shift": 0, "operator": 0, "on_time": 0, "commands": 0}
        self.paused = False                               # fleet-wide pause (emergency stop)
        self.heat: dict = {}                              # cell -> seconds robots spent waiting there (congestion map)
        self.grid.closed = set()
        self.closed_rows: set = set()
        self.history: deque = deque(maxlen=720)          # one KPI sample per simulated minute (12 h)
        self._next_sample = 0.0
        self._done_times: deque = deque()                # completion times in the last hour (rolling throughput)
        self._qcaps: set = set()
        self._cycle_hits: dict = {}
        self.site_lock: dict = {}            # work cell -> order id (an order reserves all its work cells)
        self._poll_i = 0
        self._spawn_robots()
        self._assign_bays()
        self.events.add(0, "info", "system", f"Shift started - seed {self.seed}, allocator {self.allocator.name}, "
                        + ("one TusCore manager for all vendors" if self.mode == "tuscore" else "separate manager per vendor"))

    def _assign_bays(self):
        """TusCore shares every charger. Separate managers: each vendor has its own dedicated chargers."""
        self.bay_owner: dict = {c: None for c in self.grid.chargers}
        if self.mode != "separate":
            return
        sep = self.cfg.get("separate_managers", {}).get("chargers_per_vendor")
        types = [t for t, n in self.type_count.items() if n > 0]
        bays = list(self.grid.chargers)
        if sep:
            order = [t for t in types for _ in range(int(sep.get(t, 1)))]
        else:                                  # proportional, at least one each
            order = list(types)
            while len(order) < len(bays):
                order.append(max(types, key=lambda t: self.type_count[t] / (1 + order.count(t))))
        for c, t in zip(bays, order):
            self.bay_owner[c] = t

    def _spawn_robots(self):
        z = self.cfg["zones"]["charging_area"]   # robots start parked in the charging area
        spots = [(x, y) for y in range(z["y"], z["y"] + z["h"]) for x in range(z["x"], z["x"] + z["w"])]
        self.rng.shuffle(spots)
        self.type_count = {}
        for tname, t in self.cfg["robot_types"].items():
            self.type_count[tname] = t["count"]
            for i in range(1, t["count"] + 1):
                r = Robot(id=f"{tname}_{i}", rtype=tname, label=t["label"], vendor=t["vendor"],
                          interface=t["interface"], speed=t["speed"], flying=t["flying"],
                          capabilities=list(t["capabilities"]), kwh=t["battery_kwh"], cell=spots.pop(),
                          priority=t.get("priority", 1))
                self.robots[r.id] = r
                if not r.flying:
                    self.traffic.claim(r.cell, r.id)
        # processing order: highest priority first, so it wins any contested cell
        self._order = sorted(self.robots.values(), key=lambda r: (-r.priority, r.id))

    # ------------------------------------------------------------ time
    def tick(self, real_dt: float):
        dt = real_dt * self.time_scale
        n = max(1, math.ceil(dt / SUBSTEP))
        for _ in range(n):
            self._step(dt / n)
        self._poll_telemetry()

    def _step(self, dt: float):
        self.sim_time += dt
        if self.auto_orders:
            for kind, t in self.arrivals.due(self.sim_time):
                self.add_order(kind, at=t)
        if not self.paused:                      # paused = every robot holds position; orders keep arriving
            self._random_faults(dt)
            self._qcaps = {role for tid in self.queue for role in self.tasks[tid].plans}
            self._allocate()
            for r in self._order:
                self._tick_robot(r, dt)
            self._resolve_traffic()
            self._check_collisions()
        if self.sim_time >= self._next_sample:
            self._sample()
            self._next_sample += 60.0

    def _sample(self):
        """One KPI sample per simulated minute - feeds the analytics charts."""
        now = self.sim_time
        while self._done_times and self._done_times[0] < now - 3600:
            self._done_times.popleft()
        busy, prev = {}, getattr(self, "_busy_prev", {})
        span_s = max(1e-6, now - getattr(self, "_sample_prev", 0.0))
        for r in self.robots.values():                 # share of the last minute each robot type was busy
            b = busy.setdefault(r.rtype, [0.0, 0])
            b[1] += 1
            b[0] += (r.busy_time - prev.get(r.id, 0.0)) / span_s
        self._busy_prev = {r.id: r.busy_time for r in self.robots.values()}
        self._sample_prev = now
        span = min(now, 3600.0) or 1.0
        self.history.append({
            "t": round(now), "completed": self.stats["completed"], "queued": len(self.queue),
            "active": len(self.tasks) - len(self.queue),
            "throughput": round(len(self._done_times) * 3600.0 / span, 1),
            "busy": {k: round(min(100.0, 100 * v[0] / max(1, v[1]))) for k, v in busy.items()},
            "battery": round(sum(r.battery for r in self.robots.values()) / max(1, len(self.robots)), 1),
            "energy": round(sum(r.energy_kwh for r in self.robots.values()), 2),
            "faulted": sum(1 for r in self.robots.values() if r.state == "fault"),
        })

    def shift_over(self) -> bool:
        return self.sim_time >= self.shift_len and not self.tasks

    def run_headless(self, max_time: float | None = None) -> dict:
        """Run the whole shift as fast as possible (tests, scripts, Phase 4 comparison)."""
        limit = max_time or self.shift_len * 1.5
        while self.sim_time < limit and not self.shift_over():
            self._step(SUBSTEP)
        return self.kpis()

    def _poll_telemetry(self):
        """Every tick one robot reports state through its vendor adapter (round robin)."""
        r = self._order[self._poll_i % len(self._order)]
        self._poll_i += 1
        self.adapters[r.rtype].poll(self.sim_time, r)

    # ------------------------------------------------------------ orders
    def add_order(self, kind: str, row=None, bay=None, at: float | None = None, manual=False):
        t = self.factory.make(kind, self.sim_time if at is None else at, row, bay, coordinated=self.mode == "tuscore")
        t.manual = manual
        self.tasks[t.id] = t
        bisect.insort(self.queue, t.id)
        self.stats["created"] += 1
        return t

    def _allocate(self):
        if not self.queue:
            return
        free = [r for r in self.robots.values()
                if r.state == "idle" and r.task_id is None and r.battery >= self.low and self.adapters[r.rtype].online]
        if not free:
            return
        # an order may only start if none of its work cells is reserved by another active order
        now = self.sim_time
        queued = [self.tasks[i] for i in self.queue if self.tasks[i].not_before <= now and self._sites_free(self.tasks[i])]
        for tid, mapping in self.allocator.allocate(queued, free, self.sim_time).items():
            if self._sites_free(self.tasks[tid]):          # re-check: two orders in this round may clash
                self._dispatch(self.tasks[tid], mapping)

    @staticmethod
    def _sites(t: Task) -> set:
        return {a.cell for plan in t.plans.values() for a in plan}

    def _sites_free(self, t: Task) -> bool:
        return all(self.site_lock.get(c, t.id) == t.id and c not in self.grid.closed for c in self._sites(t))

    def _unlock_sites(self, t: Task):
        for c in self._sites(t):
            if self.site_lock.get(c) == t.id:
                del self.site_lock[c]

    def _dispatch(self, t: Task, mapping: dict):
        t.status, t.started, t.robots = "in_progress", self.sim_time, dict(mapping)
        self.queue.remove(t.id)
        for c in self._sites(t):
            self.site_lock[c] = t.id
        if not t.counted:                         # wait time counts from the first dispatch only
            t.counted = True
            self.stats["started"] += 1
            self.stats["wait_sum"] += t.started - t.created
        for role, rid in mapping.items():
            r = self.robots[rid]
            r.task_id, r.role, r.plan, r.idx = t.id, role, t.plans[role], 0
        for rid in mapping.values():
            self._begin_action(self.robots[rid])

    # ------------------------------------------------------------ talking to robots (via adapters)
    def _send_path(self, r: Robot, path: list, target, label: str, state: str = "moving"):
        """The ONLY way the manager moves a robot: command goes through the vendor adapter,
        the robot receives the decoded vendor message."""
        self.stats["commands"] += 1
        got_path, got_target = self.adapters[r.rtype].send_order(self.sim_time, r, path, target, label, r.task_id)
        r.set_path(got_path, got_target, state)

    # ------------------------------------------------------------ robot state machine
    def _tick_robot(self, r: Robot, dt: float):
        s = r.state
        if s in ("moving", "to_charge"):
            if s == "moving":
                r.busy_time += dt
            self._move(r, dt)
            if not r.path:
                self._arrived(r)
        elif s == "working":
            r.busy_time += dt
            r.dwell_left -= dt
            if r.dwell_left <= 0:
                self._action_done(r)
        elif s == "waiting":
            # waiting at a hand-over: we depend on the partner robot(s) that have not arrived yet.
            # Recording that edge lets deadlock detection see loops that go through a hand-over.
            r.busy_time += dt
            t = self.tasks[r.task_id]
            here = t.sync.get(r.plan[r.idx].sync, ())
            missing = [rid for rid in t.robots.values() if rid not in here]
            r.blocked_by = missing[0] if missing else None
            r.blocked_for += dt
        elif s == "charging":
            r.battery = min(100.0, r.battery + self.charge_rate * dt)
            if r.battery >= self.resume:
                self._release_bay(r)
                r.state = "idle"
        elif s == "fault":
            if self.sim_time >= r.repair_at:
                self._repair(r)
        elif s == "idle" and r.task_id is None and r.battery < self.opp:
            self._charge_decision(r)

    def _move(self, r: Robot, dt: float):
        """Move along the path. A ground robot must CLAIM the next cell before leaving its cell."""
        budget, moved, ground = r.speed * dt, False, not r.flying
        while budget > 0 and r.path:
            nxt = r.path[0]
            if r.frac == 0 and ground:
                owner = self.traffic.owner(nxt)
                if owner not in (None, r.id):          # cell taken -> wait
                    if not moved:
                        r.blocked_by = owner
                        r.blocked_for += dt
                        self.heat[r.cell] = self.heat.get(r.cell, 0.0) + dt
                        self.stats["traffic_delay"] += dt
                    return
                self.traffic.claim(nxt, r.id)
            remaining = 1.0 - r.frac
            moved = True
            if budget >= remaining:
                if ground:
                    self.traffic.release(r.cell, r.id)
                r.cell = r.path.pop(0)
                r.frac = 0.0
                budget -= remaining
                r.dist += 1
                r.battery = max(0.0, r.battery - self.drain)
                r.energy_kwh += self.drain / 100.0 * r.kwh
            else:
                r.frac += budget
                budget = 0
        r.blocked_by, r.blocked_for, r.replan_mark = None, 0.0, 0.0
        if not r.path:
            r.target = None

    def _begin_action(self, r: Robot):
        a = r.plan[r.idx]
        r.action = a.label
        if r.cell == a.cell:
            r.set_path([], None)
            self._arrived(r)
            return
        path = astar(self.grid, r.cell, a.cell, r.flying)
        if path is None:                      # cannot happen on a valid layout
            raise RuntimeError(f"no path for {r.id} {r.cell}->{a.cell}")
        self._send_path(r, path, a.cell, a.label)

    def _arrived(self, r: Robot):
        if r.state == "to_charge":
            r.state = "charging"
            return
        if r.task_id is None:                 # manual goto / evade finished
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
                    rr.blocked_by, rr.blocked_for = None, 0.0
            else:
                r.state = "waiting"
        else:
            r.state, r.dwell_left = "working", a.dwell

    def _action_done(self, r: Robot):
        t = self.tasks[r.task_id]
        t.done_actions += 1
        label = r.plan[r.idx].label
        if label in LOAD_AFTER:
            r.load = LOAD_AFTER[label]
        r.idx += 1
        if r.idx < len(r.plan):
            self._begin_action(r)
            return
        t.done_roles.add(r.role)
        r.clear_task()
        r.state = "idle"
        if len(t.done_roles) == len(t.plans):
            self._complete(t)

    def _complete(self, t: Task):
        t.status, t.finished = "done", self.sim_time
        self._unlock_sites(t)
        del self.tasks[t.id]
        if t.then:
            # Separate managers: the lift's manager reports "done"; the WMS notices and creates the
            # tote job for the other vendor's manager after its polling/hand-over delay.
            role, actions = t.then
            nxt = Task(id=self.factory.new_id(), kind=t.kind, title=t.title + " (tote leg)", created=t.created,
                       plans={role: actions}, counted=True, not_before=self.sim_time + self.wms_delay, leg=2)
            nxt.manual = t.manual
            self.tasks[nxt.id] = nxt
            bisect.insort(self.queue, nxt.id)
            return
        self.recent.appendleft(t)
        s = self.stats
        s["completed"] += 1
        self._done_times.append(self.sim_time)
        if t.finished <= self.shift_len:
            s["completed_in_shift"] += 1
        if t.finished - t.created <= self.sla_s:
            s["on_time"] += 1
        s["cycle_sum"] += t.finished - t.created
        s["by_kind"][t.kind] += 1
        if t.mismatch:
            s["mismatches"] += 1
            self.events.add(self.sim_time, "warn", "inventory", f"{t.title}: stock mismatch flagged")

    # ------------------------------------------------------------ traffic: deadlocks, evasion, replanning
    def _resolve_traffic(self):
        stuck = {r.id: r for r in self._order if not r.flying and r.blocked_by and r.blocked_for >= self.block_s}
        if not stuck:
            return
        # 1) deadlock = cycle in the wait-for graph -> the lowest-priority robot in it gives way
        in_cycle: set = set()
        for cyc in find_cycles({rid: r.blocked_by for rid, r in stuck.items()}):
            in_cycle.update(cyc)
            if self.mode == "separate" and len({self.robots[i].rtype for i in cyc}) > 1:
                self._operator_deadlock(cyc)          # no single manager can see this deadlock
                continue
            # Escalation: if giving way keeps failing (e.g. two hand-overs hold each other's cells),
            # preempt - send the NEWEST order involved back to the queue so the older one can finish.
            key = frozenset(cyc)
            first, last = self._cycle_hits.get(key, (self.sim_time, self.sim_time))
            if self.sim_time - last > 5:                  # it had been resolved; this is a new episode
                first = self.sim_time
            self._cycle_hits[key] = (first, self.sim_time)
            if len(self._cycle_hits) > 300:
                self._cycle_hits.clear()
            involved = [self.tasks[self.robots[i].task_id] for i in cyc if self.robots[i].task_id in self.tasks]
            if self.sim_time - first >= 10 and involved:      # same deadlock for 10 s: giving way did not work
                victim_task = max(involved, key=lambda t: t.id)
                self._requeue(victim_task, reason="deadlock")
                self.stats["deadlocks"] += 1
                self._cycle_hits.pop(key, None)
                self.events.add(self.sim_time, "warn", "deadlock",
                                f"Deadlock between {', '.join(cyc)} - order #{victim_task.id} preempted and re-queued")
                continue
            # who gives way? Prefer a movable robot whose cell another cycle member is waiting for;
            # among those the lowest priority yields (pallet stackers keep the right of way).
            wanted = {self.robots[m].blocked_by for m in cyc if self.robots[m].path}
            for v in sorted((self.robots[i] for i in cyc),
                            key=lambda r: (0 if r.id in wanted else 1, r.priority, r.id)):
                if self._yield_way(v):
                    self.stats["deadlocks"] += 1
                    self.events.add(self.sim_time, "warn", "deadlock",
                                    f"Deadlock between {', '.join(cyc)} resolved - {v.id} took a detour")
                    break
        # 2) blocked by something standing still -> make it move away, or go around it
        for rid, r in stuck.items():
            if rid in in_cycle or r.blocked_by is None or not r.path:   # state may have changed above
                continue
            b = self.robots[r.blocked_by]
            if b.state in STATIONARY or not b.path:
                # TusCore can ask ANY idle robot to make way; a separate manager only its own robots -
                # another vendor's parked robot stays until an operator moves it by hand.
                if b.state == "idle" and b.task_id is None and (self.mode == "tuscore" or b.rtype == r.rtype):
                    self._evade(b, r)
                elif b.state == "idle" and b.task_id is None and r.blocked_for >= self.operator_s:
                    if self._evade(b, r):
                        self.stats["operator"] += 1
                        self.events.add(self.sim_time, "error", "operator",
                                        f"Operator moved {b.id} out of {r.id}'s way after {r.blocked_for:.0f}s")
                elif b.state == "idle" and b.task_id is None and r.blocked_for - r.replan_mark >= self.replan_s:
                    self._replan(r, avoid_moving=False)
                elif r.blocked_for - r.replan_mark >= self.replan_s:
                    self._replan(r, avoid_moving=False)
            elif r.blocked_for >= 30 and r.blocked_for - r.replan_mark >= 30:
                self._replan(r, avoid_moving=True)
            if r.blocked_for > self.watchdog_s:       # safety net - should never trigger
                self.stats["permanent"] += 1
                self.events.add(self.sim_time, "error", "watchdog", f"{r.id} stuck > {self.watchdog_s}s - forced fault")
                self.inject_fault(r.id, "watchdog")

    def _operator_deadlock(self, cyc: list):
        """Separate managers: a deadlock between two vendors' robots is invisible to both managers.
        It lasts until an operator notices and clears it manually (operator_response_s)."""
        key = frozenset(cyc)
        first, last = self._cycle_hits.get(key, (self.sim_time, self.sim_time))
        if self.sim_time - last > 5:
            first = self.sim_time
        self._cycle_hits[key] = (first, self.sim_time)
        if self.sim_time - first < self.operator_s:
            return
        self._cycle_hits.pop(key, None)
        self.stats["operator"] += 1
        self.stats["deadlocks"] += 1
        for v in sorted((self.robots[i] for i in cyc), key=lambda r: (r.priority, r.id)):
            if self._yield_way(v):
                break
        else:
            involved = [self.tasks[self.robots[i].task_id] for i in cyc if self.robots[i].task_id in self.tasks]
            if involved:
                self._requeue(max(involved, key=lambda t: t.id), reason="deadlock")
        self.events.add(self.sim_time, "error", "operator",
                        f"Cross-vendor deadlock ({', '.join(cyc)}) cleared by an operator after {self.operator_s}s")

    def _yield_way(self, v: Robot) -> bool:
        """Give way: detour around everybody, else sidestep into a free neighbour cell."""
        if not v.path:
            return False
        goal, occ = v.path[-1], self.traffic.occupied(v.id)
        path = astar(self.grid, v.cell, goal, False, blocked=occ - {goal})
        if path and path[0] not in occ:
            self._send_path(v, path, goal, "detour", v.state)
            return True
        for dx, dy in NEIGH:
            n = (v.cell[0] + dx, v.cell[1] + dy)
            if self.grid.passable(*n) and n not in occ and n != v.path[0]:
                rest = astar(self.grid, n, goal, False, blocked=occ - {goal}) or astar(self.grid, n, goal) or []
                self._send_path(v, [n] + rest, goal, "sidestep", v.state)
                return True
        return False

    def _replan(self, r: Robot, avoid_moving: bool):
        if not r.path:
            return
        goal = r.path[-1]
        occ = self.traffic.occupied(r.id)
        avoid = occ if avoid_moving else {self.robots[o].cell for o in set(self.traffic.occ.values()) - {r.id}
                                           if self.robots[o].state in STATIONARY or not self.robots[o].path}
        path = astar(self.grid, r.cell, goal, False, blocked=avoid - {goal})
        r.replan_mark = r.blocked_for
        if path and path != r.path and path[0] not in occ:
            self._send_path(r, path, goal, "replan", r.state)

    def _evade(self, b: Robot, blocked: Robot) -> bool:
        """An idle robot is in somebody's way: send it to the nearest free cell off that route."""
        if self.sim_time - b.last_evade < 5:
            return False
        b.last_evade = self.sim_time
        keep_clear = set(blocked.path) | {blocked.cell}
        occ = self.traffic.occupied(b.id)
        prev, q = {b.cell: None}, deque([b.cell])
        goal = None
        while q and goal is None:
            c = q.popleft()
            for dx, dy in NEIGH:
                n = (c[0] + dx, c[1] + dy)
                if n in prev or not self.grid.passable(*n) or n in occ or abs(n[0] - b.cell[0]) + abs(n[1] - b.cell[1]) > 10:
                    continue
                prev[n] = c
                if n not in keep_clear:
                    goal = n
                    break
                q.append(n)
        if goal is None:
            return False
        path = []
        while goal != b.cell:
            path.append(goal)
            goal = prev[goal]
        path.reverse()
        self._send_path(b, path, path[-1], "make way")
        self.stats["evasions"] += 1
        return True

    def _check_collisions(self):
        seen = set()
        for r in self._order:
            if r.flying:
                continue
            if r.cell in seen:
                self.stats["collisions"] += 1
                self.events.add(self.sim_time, "error", "collision", f"Collision at {r.cell}")
            seen.add(r.cell)

    # ------------------------------------------------------------ faults
    def _random_faults(self, dt: float):
        if self.mtbf_s and self.fault_rng.random() < len(self.robots) / self.mtbf_s * dt:
            ok = [r.id for r in self._order if r.state != "fault"]
            if ok:
                self.inject_fault(self.fault_rng.choice(ok), "random")

    def inject_fault(self, rid: str, reason: str = "injected") -> bool:
        r = self.robots.get(rid)
        if r is None or r.state == "fault":
            return False
        self._stop(r)
        tid = r.task_id
        r.state, r.repair_at = "fault", self.sim_time + self.repair_s
        r.fault_count += 1
        self.stats["faults"] += 1
        self.events.add(self.sim_time, "error", "fault",
                        f"{rid} failed ({reason})" + (f" during order #{tid}" if tid is not None else ""))
        if tid is not None and tid in self.tasks:
            self._requeue(self.tasks[tid])
        r.clear_task()
        return True

    def repair_robot(self, rid: str) -> bool:
        r = self.robots.get(rid)
        if r is None or r.state != "fault":
            return False
        self._repair(r)
        return True

    def _repair(self, r: Robot):
        self._release_bay(r)
        r.state = "idle"
        self.events.add(self.sim_time, "ok", "repair", f"{r.id} repaired and back in service")

    def _stop(self, r: Robot):
        """Halt a robot where it stands (give back the cell it had claimed ahead)."""
        if r.path and r.frac > 0 and not r.flying:
            self.traffic.release(r.path[0], r.id)
        r.path, r.frac, r.target = [], 0.0, None

    def _requeue(self, t: Task, reason: str = "fault"):
        """Put an order back in the queue (robot fault, or deadlock preemption) and free its robots."""
        for rid in t.robots.values():
            rr = self.robots[rid]
            if rr.task_id == t.id and rr.state != "fault":      # skip partners that already finished and moved on
                self._stop(rr)
                rr.clear_task()
                rr.load = None
                rr.state = "idle"
        self._unlock_sites(t)
        t.status, t.robots, t.started = "queued", {}, None
        t.sync, t.done_roles, t.done_actions = {}, set(), 0
        t.reassigned += 1
        bisect.insort(self.queue, t.id)
        if reason == "fault":
            self.stats["reassigned"] += 1
            self.events.add(self.sim_time, "warn", "reassign", f"Order #{t.id} ({t.title}) returned to the queue for reassignment")
        else:
            self.stats["preempted"] += 1

    # ------------------------------------------------------------ charging (staggered)
    def _charge_decision(self, r: Robot):
        b = r.battery
        crit, soft = b < self.critical, b < self.low
        if not (crit or soft) and any(c in self._qcaps for c in r.capabilities):
            return                                  # top-up only when nothing is waiting for this robot's skills
        if not crit:                                # keep at least half of each robot type working
            busy = sum(1 for o in self.robots.values() if o.rtype == r.rtype and o.state in ("charging", "to_charge"))
            if busy >= max(1, self.type_count[r.rtype] // 2):
                return
        self._send_to_charge(r)

    def _send_to_charge(self, r: Robot):
        free = [c for c, who in self.bays.items() if who is None and self.bay_owner.get(c) in (None, r.rtype)]
        if not free:
            return
        bay = min(free, key=lambda c: abs(c[0] - r.cell[0]) + abs(c[1] - r.cell[1]))
        self.bays[bay], r.bay = r.id, bay
        path = astar(self.grid, r.cell, bay, r.flying)
        self.events.add(self.sim_time, "info", "charge", f"{r.id} -> charger ({r.battery:.0f}%)")
        if path:
            self._send_path(r, path, bay, "go charge", "to_charge")
        else:
            r.state = "charging"

    def _release_bay(self, r: Robot):
        if r.bay is not None:
            self.bays[r.bay] = None
            r.bay = None

    # ------------------------------------------------------------ vendors
    def set_vendor_online(self, key: str, online: bool) -> bool:
        a = self.adapters.get(key)
        if a is None:
            return False
        a.online = online
        self.events.add(self.sim_time, "ok" if online else "error", "vendor",
                        f"{a.vendor} ({a.protocol}) {'reconnected' if online else 'went OFFLINE - no new missions can be sent'}")
        return True

    # ------------------------------------------------------------ operator controls (real-time site management)
    def set_paused(self, paused: bool):
        self.paused = paused
        self.events.add(self.sim_time, "error" if paused else "ok", "control",
                        "FLEET PAUSED by operator - all robots holding position" if paused else "Fleet resumed by operator")

    def set_aisle(self, row: int, closed: bool) -> bool:
        if not 0 <= row < len(self.grid.aisles):
            return False
        cells = self.grid.aisles[row]
        if closed:
            self.grid.closed |= cells
            self.closed_rows.add(row)
        else:
            self.grid.closed -= cells
            self.closed_rows.discard(row)
        self.events.add(self.sim_time, "warn" if closed else "ok", "control",
                        f"Aisle A{row + 1} {'CLOSED for maintenance - traffic rerouted, orders there wait' if closed else 're-opened'}")
        self._replan_all()
        return True

    def _replan_all(self):
        """Layout changed: every moving ground robot gets a fresh route (through its vendor adapter)."""
        for r in self._order:
            if r.flying or not r.path or r.state not in ("moving", "to_charge"):
                continue
            goal = r.path[-1]
            start = r.path[0] if r.frac > 0 else r.cell           # mid-cell: keep the cell it is entering
            path = astar(self.grid, start, goal, False)
            if path is not None:
                new = ([r.path[0]] + path) if r.frac > 0 else path
                frac = r.frac
                self._send_path(r, new, goal, "reroute", r.state)
                r.frac = frac

    def charge_now(self, rid: str) -> bool:
        r = self.robots.get(rid)
        if r is None or r.state != "idle" or r.task_id is not None:
            return False
        before = r.state
        self._send_to_charge(r)
        return r.state != before

    def alerts(self) -> list:
        """Conditions that need attention right now (recomputed every snapshot)."""
        out = []
        for r in self._order:
            if r.state == "fault":
                out.append({"level": "error", "title": f"{r.id} out of service", "detail": f"repair in {max(0, r.repair_at - self.sim_time):.0f}s", "robot": r.id})
            elif r.battery < self.critical:
                out.append({"level": "error", "title": f"{r.id} battery critical", "detail": f"{r.battery:.0f}%", "robot": r.id})
            elif r.blocked_for >= 30:
                out.append({"level": "warn", "title": f"{r.id} waiting in traffic", "detail": f"{r.blocked_for:.0f}s for {r.blocked_by}", "robot": r.id})
        for a in self.adapters.values():
            if not a.online:
                out.append({"level": "error", "title": f"{a.vendor} offline", "detail": f"{a.protocol} - no new missions", "robot": None})
        if self.paused:
            out.insert(0, {"level": "error", "title": "Fleet paused", "detail": "all robots holding position", "robot": None})
        for row in sorted(self.closed_rows):
            out.append({"level": "warn", "title": f"Aisle A{row + 1} closed", "detail": "maintenance - traffic rerouted", "robot": None})
        late = [t for t in self.tasks.values() if self.sim_time - t.created > self.sla_s]
        if late:
            out.append({"level": "warn", "title": f"{len(late)} order(s) past SLA", "detail": f"older than {self.sla_s // 60} min", "robot": None})
        return out[:12]

    def heatmap(self) -> dict:
        cells = [[x, y, round(v, 1)] for (x, y), v in self.heat.items() if v >= 1.0]
        return {"cells": cells, "max": max((c[2] for c in cells), default=0)}

    # ------------------------------------------------------------ manual control
    def goto(self, robot_id: str, x: int, y: int):
        r = self.robots.get(robot_id)
        if r is None:
            return False, "unknown robot"
        if r.task_id is not None or r.state not in ("idle",):
            return False, "robot is busy"
        goal = self.grid.nearest_free(x, y, r.flying)
        if goal is None:
            return False, "no reachable cell"
        path = astar(self.grid, r.cell, goal, r.flying)
        if path is None:
            return False, "no path"
        self._send_path(r, path, goal, "manual goto")
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
            "collisions": s["collisions"], "deadlocks": s["deadlocks"], "permanent_deadlocks": s["permanent"],
            "faults": s["faults"], "reassigned": s["reassigned"], "preempted": s["preempted"], "evasions": s["evasions"],
            "traffic_delay_min": round(s["traffic_delay"] / 60.0, 1),
            "completed_in_shift": s["completed_in_shift"], "operator_interventions": s["operator"],
            "on_time_pct": round(100.0 * s["on_time"] / s["completed"], 1) if s["completed"] else 100.0, "sla_s": self.sla_s,
            "dispatched": s["started"], "commands": s["commands"], "messages": self.bus.seq,
            "util_by_type": self._util_by_type(),
        }

    def _util_by_type(self) -> dict:
        out, span = {}, max(self.sim_time, 1.0)
        for r in self.robots.values():
            out.setdefault(r.rtype, []).append(r.busy_time / span)
        return {k: round(100 * sum(v) / len(v), 1) for k, v in out.items()}

    def report(self) -> dict:
        """Shift report: fleet KPIs + per robot + per vendor (JSON; the API also serves CSV)."""
        span = max(self.sim_time, 1.0)
        robots = [{"id": r.id, "type": r.label, "vendor": r.vendor, "interface": r.interface,
                   "busy_pct": round(100 * r.busy_time / span, 1), "distance_cells": int(r.dist),
                   "energy_kwh": round(r.energy_kwh, 2), "battery_pct": round(r.battery, 1),
                   "faults": r.fault_count, "state": r.state} for r in self.robots.values()]
        return {"site": self.cfg.get("name"), "mode": self.mode, "allocator": self.allocator.name, "seed": self.seed,
                "sim_time_h": round(self.sim_time / 3600, 2), "kpis": self.kpis(), "robots": robots,
                "vendors": self.vendors()}

    def vendors(self) -> list:
        out = []
        for key, a in self.adapters.items():
            st = a.status(self.sim_time)
            st.update(label=self.cfg["robot_types"][key]["label"], robots=self.type_count[key],
                      faulted=sum(1 for r in self.robots.values() if r.rtype == key and r.state == "fault"))
            out.append(st)
        return out

    def snapshot(self) -> dict:
        now = self.sim_time
        live = [t.to_dict(now) for t in sorted(self.tasks.values(), key=lambda t: t.id)]
        return {
            "t": round(now, 1), "scale": self.time_scale, "shift": self.shift_len,
            "auto_orders": self.auto_orders, "allocator": self.allocator.name, "seed": self.seed,
            "mode": self.mode, "site": self.cfg.get("name"), "paused": self.paused,
            "closed_rows": sorted(self.closed_rows), "alerts": self.alerts(),
            "robots": [r.to_dict() for r in self.robots.values()],
            "tasks": live[:80], "recent": [t.to_dict(now) for t in list(self.recent)[:15]],
            "kpis": self.kpis(), "vendors": self.vendors(),
            "events": self.events.recent(40), "messages": self.bus.recent(40),
        }

    def types_meta(self) -> dict:
        return self.cfg["robot_types"]
