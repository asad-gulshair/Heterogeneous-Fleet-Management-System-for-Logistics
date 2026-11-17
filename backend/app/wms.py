"""WMS integration: the REST gateway a warehouse management system talks to, and a mock WMS.

A WMS sends order lines with its own reference; TusCore turns each into a fleet order, runs it and
reports the status back - by webhook (push) and by an event feed the WMS can poll (pull):

    POST   /api/wms/orders            {"ref": "SO-1001-1", "type": "box_pick", "location": "A3-05",
                                       "destination": "packing_2", "priority": "high"}
    GET    /api/wms/orders/{ref}      status: queued -> in_progress -> done   (or cancelled / rejected)
    DELETE /api/wms/orders/{ref}      cancel
    GET    /api/wms/events?after=N    every status change, in order (for polling)
    POST   /api/wms/webhooks          {"url": "https://wms.example.com/tuscore"}  -> status changes are POSTed there

Submitting the same ref twice returns the first order (idempotent): a WMS that retries after a timeout
never creates duplicate robot work.
"""
from __future__ import annotations
import json
import queue
import random
import re
import threading
import time
import urllib.request
import zlib
from collections import deque

from .missions import MissionError, Targets, priority_of, STEP_ACTIONS
from .tasks import Action, Task

TYPES = ("box_pick", "high_pick", "pallet_move", "inventory_scan", "transport")
REF = re.compile(r"^[A-Za-z0-9_.:\-/]{1,64}$")
FINAL = ("done", "cancelled", "rejected")


class WmsError(ValueError):
    pass


class WmsGateway:
    def __init__(self, store=None):
        self.store = store
        self.orders: dict[str, dict] = {}         # ref -> record
        self.by_task: dict[int, str] = {}         # current task id -> ref
        self.feed: deque = deque(maxlen=5000)
        self.seq = 0
        self.webhooks: list[dict] = store.get_json("wms_webhooks", []) if store else []
        self._q: queue.Queue = queue.Queue(maxsize=10000)
        self._worker = None
        self.sim = None

    def attach(self, sim):
        self.sim = sim
        self.targets = Targets(sim.grid)
        sim.listeners.append(self._on_task)
        for ref, o in self.orders.items():        # a new shift: open orders of the old shift cannot finish any more
            if o["status"] not in FINAL:
                self._set(o, "cancelled", reason="fleet manager restarted (new shift or layout)")
        self.by_task = {}

    # ------------------------------------------------------------ submit / query / cancel
    def submit(self, body: dict, source: str = "wms") -> tuple[dict, bool]:
        """Returns (order record, created). Raises WmsError for a bad order (HTTP 422)."""
        ref = str(body.get("ref") or "").strip()
        if not REF.match(ref):
            raise WmsError("ref is required: 1-64 letters, digits or _ . : - /")
        if ref in self.orders:
            return self.orders[ref], False
        typ = body.get("type") or body.get("kind")
        if typ not in TYPES:
            raise WmsError(f"type must be one of {', '.join(TYPES)}")
        try:
            prio = priority_of(body.get("priority", "normal"))
            t = self._build(typ, body)
        except (MissionError, WmsError) as e:
            self._reject(ref, typ, str(e), body)
            raise WmsError(str(e))
        t.priority, t.ref, t.source = prio, ref, source
        o = {"ref": ref, "type": typ, "status": "queued", "task_id": t.id, "title": t.title, "priority": prio,
             "location": body.get("location"), "destination": body.get("destination"), "robots": {},
             "received": round(self.sim.sim_time, 1), "started": None, "finished": None, "cycle": None,
             "reason": None, "source": source, "wall": time.time(), "history": []}
        self.orders[ref] = o
        self.by_task[t.id] = ref
        self.sim.submit(t)
        self._set(o, "queued")
        return o, True

    def _build(self, typ: str, b: dict) -> Task:
        s, rng = self.sim, random.Random(zlib.crc32(str(b.get("ref")).encode()))
        loc, dst = b.get("location"), b.get("destination")
        if typ == "transport":                       # generic A -> B move with a tote (or pallet with robot_type)
            if not loc or not dst:
                raise WmsError("transport needs location (from) and destination (to)")
            role = "move_pallet" if b.get("load") == "pallet" else "carry_tote"
            if not any(role in r.capabilities for r in s.robots.values()):
                raise WmsError("no robot on this site can do this transport")
            d = s.cfg["orders"]["dwell_s"]
            pick, drop = ("lift", "set") if role == "move_pallet" else ("load", "unload")
            acts = [Action(self.targets.resolve(loc, False, rng), float(d[STEP_ACTIONS[pick][1]]), STEP_ACTIONS[pick][0]),
                    Action(self.targets.resolve(dst, False, rng), float(d[STEP_ACTIONS[drop][1]]), STEP_ACTIONS[drop][0])]
            return Task(id=s.factory.new_id(), kind="transport", title=f"Move {loc} > {dst}", created=s.sim_time,
                        plans={role: acts})
        row = bay = None
        if loc:
            m = Targets.BAY.match(str(loc))
            rack = (m.group(1) if m else str(loc)).upper()
            row = self.targets.racks.get(rack)
            if typ in ("box_pick", "high_pick", "inventory_scan"):
                if row is None:
                    raise WmsError(f"unknown location {loc}: use a rack bay like A3-05")
                if m:
                    bay = int(m.group(2)) - 1
                    if not any(x == bay for x, _ in s.grid.faces[row]):
                        raise WmsError(f"bay {loc} cannot be reached")
        need = {"box_pick": "carry_tote", "high_pick": "high_pick", "pallet_move": "move_pallet", "inventory_scan": "scan"}[typ]
        if not any(need in r.capabilities for r in s.robots.values()):
            raise WmsError(f"no robot on this site can do {typ.replace('_', ' ')}")
        if typ == "pallet_move" and not (s.grid.zones_of("pallet_area") and s.grid.zones_of("dock")):
            raise WmsError("this site has no pallet area and dock")
        lift_face = None
        if typ == "high_pick" and row is not None and bay is not None and s.grid.rows[row].get("lift_bays") is not None:
            ok = {b_: f for b_, f in s.grid.rows[row]["lift_bays"]}
            if bay not in ok:
                raise WmsError(f"the high-shelf picker does not fit in the aisle at {loc}")
            lift_face = s.grid.face(row, bay, ok[bay])
        t = s.factory.make(typ, s.sim_time, row, bay, coordinated=s.mode == "tuscore")
        if lift_face:                                            # pick from the side where the lift fits
            a_, b_ = lift_face
            for act in t.plans["high_pick"]:
                act.cell = a_
            first = t.plans["carry_tote"][0] if "carry_tote" in t.plans else (t.then[1][0] if t.then else None)
            if first is not None:
                first.cell = b_
        if typ == "pallet_move" and loc:                        # pallet from a given place
            t.plans["move_pallet"][0].cell = self.targets.resolve(loc, False, rng)
        if dst and typ in ("box_pick", "high_pick", "pallet_move"):   # deliver to a given station / dock
            role = "move_pallet" if typ == "pallet_move" else "carry_tote"
            cell = self.targets.resolve(dst, False, rng)
            if role in t.plans:
                t.plans[role][-1].cell = cell
            elif t.then and t.then[0] == role:
                t.then[1][-1].cell = cell
        return t

    def _reject(self, ref, typ, reason, body):
        o = {"ref": ref, "type": typ, "status": "rejected", "task_id": None, "title": None, "priority": None,
             "location": body.get("location"), "destination": body.get("destination"), "robots": {},
             "received": round(self.sim.sim_time, 1), "started": None, "finished": None, "cycle": None,
             "reason": reason, "source": "wms", "wall": time.time(), "history": []}
        self.orders[ref] = o
        self._set(o, "rejected", reason=reason)

    def get(self, ref: str) -> dict:
        o = self.orders[ref]
        t = self.sim.tasks.get(o["task_id"]) if o["task_id"] is not None else None
        out = dict(o)
        if t is not None:
            out["progress"] = t.to_dict(self.sim.sim_time)["progress"]
        return out

    def list(self, status: str | None = None, limit: int = 100) -> list:
        rows = [o for o in reversed(list(self.orders.values())) if status is None or o["status"] == status]
        return [{k: v for k, v in o.items() if k != "history"} for o in rows[:limit]]

    def cancel(self, ref: str, reason: str = "cancelled by WMS") -> dict:
        o = self.orders[ref]
        if o["status"] in FINAL:
            raise WmsError(f"order {ref} is already {o['status']}")
        self.sim.cancel_task(o["task_id"], reason)
        return o

    def events(self, after: int = 0, limit: int = 200) -> dict:
        items = [e for e in self.feed if e["seq"] > after][:limit]
        return {"events": items, "last": self.seq}

    def stats(self) -> dict:
        by = {}
        for o in self.orders.values():
            by[o["status"]] = by.get(o["status"], 0) + 1
        return {"orders": len(self.orders), "by_status": by, "webhooks": len(self.webhooks), "events": self.seq}

    # ------------------------------------------------------------ status from the fleet manager
    def _on_task(self, event: str, t: Task, extra=None):
        ref = self.by_task.get(t.id)
        if ref is None:
            return
        o = self.orders[ref]
        if event == "started":
            o["started"] = o["started"] if o["started"] is not None else round(t.started, 1)
            o["robots"].update(t.robots)
            self._set(o, "in_progress")
        elif event == "requeued":
            self._set(o, "queued", reason=f"robot {extra}: order re-queued")
        elif event == "leg" and extra is not None:     # separate managers: second leg has a new task id
            self.by_task.pop(t.id, None)
            self.by_task[extra.id] = ref
            o["task_id"] = extra.id
            self._set(o, "queued", reason="hand-over to the second vendor's manager")
        elif event == "done":
            self.by_task.pop(t.id, None)
            o["finished"] = round(t.finished, 1)
            o["cycle"] = round(t.finished - o["received"], 1)
            self._set(o, "done")
        elif event == "cancelled":
            self.by_task.pop(t.id, None)
            self._set(o, "cancelled", reason=str(extra or "cancelled"))

    def _set(self, o: dict, status: str, reason: str | None = None):
        o["status"] = status
        if reason:
            o["reason"] = reason
        now = round(self.sim.sim_time, 1) if self.sim else 0.0
        o["history"].append([now, status])
        self.seq += 1
        ev = {"seq": self.seq, "ref": o["ref"], "status": status, "t": now, "robots": dict(o["robots"]),
              "reason": reason, "cycle": o["cycle"] if status == "done" else None}
        self.feed.append(ev)
        if self.store and status in FINAL:
            self.store.save_wms(o)
        if self.webhooks:
            self._push(ev)

    # ------------------------------------------------------------ webhooks (background thread, retries)
    def add_webhook(self, url: str) -> list:
        if not re.match(r"^https?://[^\s]{3,300}$", url or ""):
            raise WmsError("url must start with http:// or https://")
        if not any(w["url"] == url for w in self.webhooks):
            self.webhooks.append({"url": url, "ok": 0, "failed": 0, "last_error": None})
        self._persist_hooks()
        return self.webhooks

    def remove_webhook(self, url: str) -> list:
        self.webhooks = [w for w in self.webhooks if w["url"] != url]
        self._persist_hooks()
        return self.webhooks

    def _persist_hooks(self):
        if self.store:
            self.store.set_json("wms_webhooks", [{"url": w["url"], "ok": 0, "failed": 0, "last_error": None} for w in self.webhooks])

    def _push(self, ev: dict):
        if self._worker is None:
            self._worker = threading.Thread(target=self._deliver, daemon=True, name="wms-webhooks")
            self._worker.start()
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            pass                                  # the WMS can always catch up through /api/wms/events

    def _deliver(self):
        while True:
            ev = self._q.get()
            body = json.dumps(ev).encode()
            for w in list(self.webhooks):
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(w["url"], data=body, method="POST",
                                                     headers={"Content-Type": "application/json", "User-Agent": "TusCore/2.0"})
                        urllib.request.urlopen(req, timeout=3).close()
                        w["ok"] += 1
                        break
                    except Exception as e:                       # noqa: BLE001 - any network error: retry, then count
                        w["last_error"] = str(e)[:200]
                        time.sleep(0.5 * (attempt + 1))
                else:
                    w["failed"] += 1


class MockWms:
    """A stand-in for a real WMS: releases order waves through the same gateway a real WMS uses.

    Realistic shape: picks follow an ABC profile (20 % of the bays get about 80 % of the picks), orders come in
    waves every few minutes, about 10 % are express (high) and 2 % urgent; pallets go out per truck; cycle counts
    are low priority. Rates come from the site's order mix."""

    def __init__(self, gateway: WmsGateway):
        self.gw = gateway
        self.on = False
        self.wave_min = 5.0
        self.scale = 1.0
        self.n = 0
        self.sent = 0
        self.rejected = 0
        self.sim = None

    def attach(self, sim):
        self.sim = sim
        self.rng = random.Random(sim.seed + 77)
        g = sim.grid
        bays = [(g.rack_name(i), b + 1) for i in range(len(g.rows)) for b, _ in g.faces[i]]
        self.rng.shuffle(bays)
        self.bays = bays
        self.weights = [1.0 / (k + 1) ** 0.9 for k in range(len(bays))]       # ABC / Pareto popularity
        self.next_wave = sim.sim_time
        sim.step_hooks.append(self._tick)
        if self.on:
            sim.auto_orders = False

    def set(self, on: bool, wave_min: float | None = None, scale: float | None = None):
        self.on = on
        if wave_min:
            self.wave_min = max(1.0, min(60.0, wave_min))
        if scale:
            self.scale = max(0.1, min(5.0, scale))
        self.sim.auto_orders = not on            # the WMS replaces the built-in order generator
        self.next_wave = self.sim.sim_time
        self.sim.events.add(self.sim.sim_time, "info", "wms", "Mock WMS " + ("connected: orders now come from the WMS" if on else "stopped"))

    def status(self) -> dict:
        return {"on": self.on, "wave_min": self.wave_min, "scale": self.scale, "sent": self.sent, "rejected": self.rejected,
                "next_wave_in_s": max(0, round(self.next_wave - self.sim.sim_time)) if self.on else None}

    def _tick(self):
        s = self.sim
        if not self.on or s.sim_time < self.next_wave or s.sim_time >= s.shift_len:
            return
        self.next_wave = s.sim_time + self.wave_min * 60.0
        rates = s.cfg["orders"]["rate_per_hour"]
        for typ, rate in rates.items():
            mean = rate * self.scale * self.wave_min / 60.0
            k = self._poisson(mean)
            for _ in range(k):
                self._send(typ)

    def _poisson(self, lam: float) -> int:
        n, p, L = 0, 1.0, pow(2.718281828, -lam)
        while True:
            p *= self.rng.random()
            if p <= L:
                return n
            n += 1

    def _send(self, typ: str):
        self.n += 1
        body = {"ref": f"WMS-{self.n:06d}", "type": typ}
        r = self.rng.random()
        body["priority"] = "urgent" if r < 0.02 else "high" if r < 0.12 else "low" if typ == "inventory_scan" else "normal"
        if typ in ("box_pick", "high_pick") and self.bays:
            rack, bay = self.rng.choices(self.bays, weights=self.weights)[0]
            body["location"] = f"{rack}-{bay:02d}"
        elif typ == "inventory_scan" and self.bays:
            body["location"] = self.rng.choice(self.bays)[0]
        try:
            self.gw.submit(body, source="mock_wms")
            self.sent += 1
        except WmsError:
            self.rejected += 1
