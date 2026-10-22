"""Simulation core: owns the grid, robots, orders and the fleet-manager logic.

Layers (top to bottom):
  allocator   which free robot takes which order
  sim (here)  task execution state machine, traffic control, charging, faults
  adapters    translate our common commands to each vendor's protocol and back
  robots      the simulated machines (the "plant")
"""
from __future__ import annotations
import bisect
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
NEIGH = ((1, 0), (-1, 0), (0, 1), (0, -1))


class Simulation:
    def __init__(self, config_path: str, seed: int | None = None, allocator: str = "hungarian"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.grid = Grid(self.cfg)
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
        self.adapters = build_adapters(self.cfg, self.bus)
        self.factory = OrderFactory(self.grid, self.cfg, self.rng)
        self.arrivals = ArrivalProcess(self.cfg, self.rng)
        self.stats = {"created": 0, "completed": 0, "started": 0, "wait_sum": 0.0, "cycle_sum": 0.0,
                      "mismatches": 0, "by_kind": {k: 0 for k in KINDS},
                      "collisions": 0, "deadlocks": 0, "permanent": 0, "faults": 0, "reassigned": 0,
                      "evasions": 0, "traffic_delay": 0.0, "preempted": 0}
        self._qcaps: set = set()
        self._cycle_hits: dict = {}
        self.site_lock: dict = {}            # work cell -> order id (an order reserves all its work cells)
        self._poll_i = 0
        self._spawn_robots()
        self.events.add(0, "info", "system", f"Shift started - seed {self.seed}, allocator {self.allocator.name}")

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
        self._random_faults(dt)
        self._qcaps = {role for tid in self.queue for role in self.tasks[tid].plans}
        self._allocate()
        for r in self._order:
            self._tick_robot(r, dt)
        self._resolve_traffic()
        self._check_collisions()

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
        t = self.factory.make(kind, self.sim_time if at is None else at, row, bay)
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
        queued = [self.tasks[i] for i in self.queue if self._sites_free(self.tasks[i])]
        for tid, mapping in self.allocator.allocate(queued, free, self.sim_time).items():
            if self._sites_free(self.tasks[tid]):          # re-check: two orders in this round may clash
                self._dispatch(self.tasks[tid], mapping)

    @staticmethod
    def _sites(t: Task) -> set:
        return {a.cell for plan in t.plans.values() for a in plan}

    def _sites_free(self, t: Task) -> bool:
        return all(self.site_lock.get(c, t.id) == t.id for c in self._sites(t))

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
        self.recent.appendleft(t)
        s = self.stats
        s["completed"] += 1
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
                if b.state == "idle" and b.task_id is None:
                    self._evade(b, r)
                elif r.blocked_for - r.replan_mark >= self.replan_s:
                    self._replan(r, avoid_moving=False)
            elif r.blocked_for >= 30 and r.blocked_for - r.replan_mark >= 30:
                self._replan(r, avoid_moving=True)
            if r.blocked_for > self.watchdog_s:       # safety net - should never trigger
                self.stats["permanent"] += 1
                self.events.add(self.sim_time, "error", "watchdog", f"{r.id} stuck > {self.watchdog_s}s - forced fault")
                self.inject_fault(r.id, "watchdog")

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

    def _evade(self, b: Robot, blocked: Robot):
        """An idle robot is in somebody's way: send it to the nearest free cell off that route."""
        if self.sim_time - b.last_evade < 5:
            return
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
            return
        path = []
        while goal != b.cell:
            path.append(goal)
            goal = prev[goal]
        path.reverse()
        self._send_path(b, path, path[-1], "make way")
        self.stats["evasions"] += 1

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
        free = [c for c, who in self.bays.items() if who is None]
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
        }

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
            "robots": [r.to_dict() for r in self.robots.values()],
            "tasks": live[:80], "recent": [t.to_dict(now) for t in list(self.recent)[:15]],
            "kpis": self.kpis(), "vendors": self.vendors(),
            "events": self.events.recent(40), "messages": self.bus.recent(40),
        }

    def types_meta(self) -> dict:
        return self.cfg["robot_types"]
