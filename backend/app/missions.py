"""Missions: multi-step jobs that an operator or a WMS defines, run with a priority, once or repeated.

A mission is a template:
    {"name": "Replenish packing 1", "robot": {"role": "carry_tote"} | {"type": "tote_amr"} | {"robot": "tote_amr_3"},
     "steps": [{"to": "A3-05", "do": "load"}, {"to": "Wait A", "do": "wait", "wait_s": 20}, {"to": "packing_1", "do": "unload"}],
     "priority": 2, "repeat": 5, "every_min": 10}

Each run becomes one order in the fleet manager, so allocation, traffic rules, charging, faults and
re-assignment work exactly like for every other order. `repeat` 0 = keep repeating until stopped;
`every_min` = minutes between run starts (a run never starts while the previous one is still open).

Step targets ("to"):
    waypoint name        "Wait A"                     (drawn in the Layout Designer)
    zone                 "packing_1", "Packing 1", "dock", "charging"
    rack bay             "A3-05"                      (the aisle cell in front of bay 5 of rack A3)
    cell                 "12,7"                       (x, y in metres)
"""
from __future__ import annotations
import random
import re
import time

from .tasks import Action, Task

PRIORITIES = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
STEP_ACTIONS = {          # key -> (label shown and used for load handling, dwell config key or seconds)
    "go":     ("drive", 0),
    "load":   ("load tote", "pick"),
    "unload": ("unload tote", "drop"),
    "lift":   ("lift pallet", "pallet_pick"),
    "set":    ("set down pallet", "pallet_drop"),
    "scan":   ("scan", "scan"),
    "wait":   ("wait", 30),
}
ROLE_LABEL = {"carry_tote": "Tote carrier", "high_pick": "High-shelf picker", "move_pallet": "Pallet mover", "scan": "Scanner"}
MAX_STEPS = 20


class MissionError(ValueError):
    pass


def priority_of(v) -> int:
    if isinstance(v, str):
        if v.lower() not in PRIORITIES:
            raise MissionError(f"priority must be one of {', '.join(PRIORITIES)}")
        return PRIORITIES[v.lower()]
    try:
        return max(0, min(3, int(v if v is not None else 1)))
    except (TypeError, ValueError):
        raise MissionError("priority must be low, normal, high, urgent or 0-3")


class Targets:
    """Turns a target name into a cell on the current site."""

    BAY = re.compile(r"^\s*([A-Za-z]+\d+)\s*[-/ ]\s*(\d+)\s*$")
    CELL = re.compile(r"^\s*(\d+)\s*[, ]\s*(\d+)\s*$")

    def __init__(self, grid):
        self.grid = grid
        self.racks = {grid.rack_name(i).upper(): i for i in range(len(grid.rows))}

    def catalog(self) -> dict:
        g = self.grid
        return {"waypoints": sorted(g.waypoints),
                "zones": [{"id": k, "type": g.ztype[k]} for k in sorted(g.zones)],
                "racks": [{"name": g.rack_name(i), "bays": g.rack_len(g.rows[i]),
                           "pickable": sorted(b + 1 for b, _ in g.faces[i])} for i in range(len(g.rows))]}

    def resolve(self, target: str, flying: bool, rng: random.Random) -> tuple:
        g, t = self.grid, str(target or "").strip()
        if not t:
            raise MissionError("a step has no target")
        wp = {k.lower(): v for k, v in g.waypoints.items()}
        if t.lower() in wp:
            return self._ok(tuple(wp[t.lower()]), t, flying)
        key = t.lower().replace(" ", "_")
        if key in g.zones:
            return self._ok(g.free_cell_in(key, rng), t, flying)
        if key in ("packing", "dock", "pallet_area", "pallets", "charging"):
            names = g.zones_of("pallet_area" if key == "pallets" else key)
            if not names:
                raise MissionError(f"this site has no {key.replace('_', ' ')} area")
            return self._ok(g.free_cell_in(names[0] if len(names) == 1 else rng.choice(names), rng), t, flying)
        m = self.BAY.match(t)
        if m:
            row = self.racks.get(m.group(1).upper())
            if row is None:
                raise MissionError(f"unknown rack {m.group(1)}")
            bay = int(m.group(2)) - 1
            if not any(b == bay for b, _ in g.faces[row]):
                raise MissionError(f"bay {t} cannot be reached (check the rack's aisles)")
            return self._ok(g.face(row, bay)[0], t, flying)
        m = self.CELL.match(t)
        if m:
            return self._ok((int(m.group(1)), int(m.group(2))), t, flying)
        raise MissionError(f"unknown target '{t}': use a waypoint, a zone (packing_1), a rack bay (A3-05) or x,y")

    def _ok(self, c: tuple, name: str, flying: bool) -> tuple:
        g = self.grid
        if not g.in_bounds(*c):
            raise MissionError(f"{name} is outside the floor")
        if not flying and c not in g.reach:
            raise MissionError(f"{name} cannot be reached by ground robots")
        return c


class MissionManager:
    """Mission templates and their runs. Re-attached to every new simulation (layout change, reset)."""

    def __init__(self, store=None):
        self.store = store
        self.items: dict[int, dict] = {}
        self.sim = None
        self._next = 1
        if store:
            for m in store.load_missions():
                self.items[m["id"]] = m
                self._next = max(self._next, m["id"] + 1)

    # ------------------------------------------------------------ wiring
    def attach(self, sim):
        self.sim = sim
        self.targets = Targets(sim.grid)
        sim.listeners.append(self._on_task)
        sim.step_hooks.append(self._tick)
        for m in self.items.values():             # a new shift: open runs are gone, check targets on this site
            m["open_task"] = None
            m["next_at"] = 0.0
            if m["status"] in ("active", "invalid"):
                try:
                    self._check(m)
                    m["status"], m["error"] = "active", None
                except MissionError as e:
                    m["status"], m["error"] = "invalid", str(e)

    # ------------------------------------------------------------ API
    def create(self, spec: dict, user: str = "") -> dict:
        m = self._normalise(spec)
        self._check(m)
        m.update(id=self._next, status="active" if spec.get("start", True) else "paused", runs=0, failed=0,
                 open_task=None, next_at=self.sim.sim_time if self.sim else 0.0, created=time.time(), created_by=user,
                 last_cycle=None, error=None)
        self._next += 1
        self.items[m["id"]] = m
        self._save(m)
        self.sim.events.add(self.sim.sim_time, "info", "mission", f"Mission '{m['name']}' created ({self._repeat_txt(m)})")
        return self.public(m)

    def set_status(self, mid: int, action: str) -> dict:
        m = self.items[mid]
        if action == "pause":
            m["status"] = "paused"
        elif action == "resume":
            self._check(m)
            m["status"], m["error"] = "active", None
            m["next_at"] = min(m["next_at"], self.sim.sim_time)
        elif action == "stop":
            if m["open_task"] is not None:
                self.sim.cancel_task(m["open_task"], f"mission '{m['name']}' stopped")
            m["status"] = "stopped"
        elif action == "run_now":
            self._check(m)
            if m["open_task"] is None:
                m["next_at"] = self.sim.sim_time
            if m["status"] != "active":
                m["status"] = "active"
        else:
            raise MissionError("action must be pause, resume, stop or run_now")
        self._save(m)
        return self.public(m)

    def delete(self, mid: int):
        m = self.items.pop(mid)
        if m["open_task"] is not None:
            self.sim.cancel_task(m["open_task"], f"mission '{m['name']}' deleted")
        if self.store:
            self.store.delete_mission(mid)

    def list(self) -> list:
        return [self.public(m) for m in sorted(self.items.values(), key=lambda m: -m["id"])]

    def public(self, m: dict) -> dict:
        t = self.sim.tasks.get(m["open_task"]) if self.sim and m["open_task"] is not None else None
        return {k: m[k] for k in ("id", "name", "robot", "steps", "priority", "repeat", "every_min", "status", "runs",
                                  "failed", "error", "created", "created_by", "last_cycle")} | {
            "priority_name": [k for k, v in PRIORITIES.items() if v == m["priority"]][0],
            "repeat_text": self._repeat_txt(m),
            "open": t.to_dict(self.sim.sim_time) if t else None,
            "next_in_s": max(0.0, round(m["next_at"] - self.sim.sim_time, 1)) if self.sim and m["status"] == "active" and t is None else None}

    # ------------------------------------------------------------ building runs
    def _normalise(self, spec: dict) -> dict:
        name = str(spec.get("name") or "").strip()[:60] or "Mission"
        steps = spec.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise MissionError("a mission needs at least one step")
        if len(steps) > MAX_STEPS:
            raise MissionError(f"at most {MAX_STEPS} steps")
        clean = []
        for i, st in enumerate(steps, 1):
            do = str(st.get("do") or "go")
            if do not in STEP_ACTIONS:
                raise MissionError(f"step {i}: 'do' must be one of {', '.join(STEP_ACTIONS)}")
            c = {"to": str(st.get("to") or "").strip()[:40], "do": do}
            if st.get("wait_s") not in (None, ""):
                c["wait_s"] = float(max(0, min(3600, float(st["wait_s"]))))
            clean.append(c)
        rob = spec.get("robot") or {}
        if isinstance(rob, str):
            rob = {"role": rob}
        rob = {k: str(v) for k, v in rob.items() if k in ("role", "type", "robot") and v}
        repeat = int(max(0, min(10000, int(spec.get("repeat", 1) if spec.get("repeat") is not None else 1))))
        every = float(max(0, min(24 * 60, float(spec.get("every_min") or 0))))
        return {"name": name, "steps": clean, "robot": rob, "priority": priority_of(spec.get("priority", 1)),
                "repeat": repeat, "every_min": every}

    def _role(self, m: dict) -> tuple[str, dict]:
        """(capability, require) from the mission's robot choice."""
        s, rob = self.sim, m["robot"]
        if "robot" in rob:
            r = s.robots.get(rob["robot"])
            if r is None:
                raise MissionError(f"no robot {rob['robot']} on this site")
            return r.capabilities[0], {"robot": r.id}
        if "type" in rob:
            t = s.cfg["robot_types"].get(rob["type"])
            if t is None or not t.get("count"):
                raise MissionError(f"no robots of type {rob['type']} on this site")
            return t["capabilities"][0], {"type": rob["type"]}
        role = rob.get("role") or "carry_tote"
        if not any(role in r.capabilities for r in s.robots.values()):
            raise MissionError(f"no robot on this site can do the job '{ROLE_LABEL.get(role, role)}'")
        return role, {}

    def _actions(self, m: dict, rng: random.Random) -> tuple[str, dict, list]:
        role, req = self._role(m)
        flying = role == "scan"
        dwell = self.sim.cfg["orders"]["dwell_s"]
        acts = []
        for st in m["steps"]:
            label, d = STEP_ACTIONS[st["do"]]
            cell = self.targets.resolve(st["to"], flying, rng)
            sec = st.get("wait_s", dwell.get(d, 10) if isinstance(d, str) else d)
            acts.append(Action(cell, float(sec), label if st["do"] != "go" else f"drive to {st['to']}"))
        return role, req, acts

    def _check(self, m: dict):
        if self.sim:
            self._actions(m, random.Random(0))

    def build_task(self, m: dict, title: str, rng: random.Random) -> Task:
        role, req, acts = self._actions(m, rng)
        s = self.sim
        return Task(id=s.factory.new_id(), kind="mission", title=title, created=s.sim_time, plans={role: acts},
                    priority=m["priority"], source="mission", require={role: req} if req else {})

    # ------------------------------------------------------------ running
    def _tick(self):
        s = self.sim
        for m in self.items.values():
            if m["status"] != "active" or m["open_task"] is not None or s.sim_time < m["next_at"]:
                continue
            run = m["runs"] + m["failed"] + 1
            try:
                t = self.build_task(m, f"{m['name']} #{run}", random.Random(m["id"] * 100003 + run))
            except MissionError as e:
                m["status"], m["error"] = "invalid", str(e)
                s.events.add(s.sim_time, "error", "mission", f"Mission '{m['name']}' cannot run: {e}")
                continue
            t.mission_id = m["id"]
            m["open_task"], m["run_started"] = t.id, s.sim_time
            s.submit(t)

    def _on_task(self, event: str, t: Task, extra=None):
        if t.mission_id is None or t.mission_id not in self.items:
            return
        m, s = self.items[t.mission_id], self.sim
        if event == "leg" and extra is not None:
            m["open_task"] = extra.id
        elif event in ("done", "cancelled"):
            m["open_task"] = None
            if event == "done":
                m["runs"] += 1
                m["last_cycle"] = round(t.finished - t.created, 1)
            else:
                m["failed"] += 1
            if m["repeat"] and m["runs"] >= m["repeat"]:
                m["status"] = "done"
                s.events.add(s.sim_time, "ok", "mission", f"Mission '{m['name']}' finished: {m['runs']} run(s)")
            m["next_at"] = max(s.sim_time, m.get("run_started", s.sim_time) + m["every_min"] * 60.0)
            self._save(m)

    def _save(self, m: dict):
        if self.store:
            self.store.save_mission(m)

    @staticmethod
    def _repeat_txt(m: dict) -> str:
        n = "repeat until stopped" if m["repeat"] == 0 else ("once" if m["repeat"] == 1 else f"{m['repeat']} times")
        return n + (f", every {m['every_min']:g} min" if m["every_min"] and m["repeat"] != 1 else "")
