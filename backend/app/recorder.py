"""Records the live shift into the database: robot positions once per simulated second (for replay),
the event log, every finished order and the KPIs. Buffered in memory and written in one transaction
about every two seconds, so recording costs the simulation almost nothing."""
from __future__ import annotations
import json

FRAME_S = 1.0


class Recorder:
    def __init__(self, store):
        self.store = store
        self.sim = None
        self.sid = None

    def attach(self, sim, layout: str, map_: dict):
        if self.sim is not None:
            self.flush(final=True)
        self.sim = sim
        self.ids = list(sim.robots)
        robots = [{"id": r.id, "type": r.rtype, "model": r.model or r.rtype} for r in sim.robots.values()]
        self.sid = self.store.start_shift({"site": sim.cfg.get("name"), "layout": layout, "mode": sim.mode,
                                           "allocator": sim.allocator.name, "seed": sim.seed}, robots, map_)
        self.frames, self.orders = [], []
        self.ev_seen = 0
        self.next_frame = 0.0
        self.closed = False
        sim.step_hooks.append(self._tick)
        sim.listeners.append(self._on_task)
        self.store.prune()

    def _tick(self):
        s = self.sim
        if self.closed or s.sim_time < self.next_frame:
            return
        self.next_frame = (int(s.sim_time / FRAME_S) + 1) * FRAME_S
        rows = []
        for rid in self.ids:
            r = s.robots[rid]
            x, y = r.pos
            rows.append([round(x, 2), round(y, 2), r.state, round(r.battery), r.load, int(r.blocked_for), r.task_id])
        self.frames.append((round(s.sim_time, 1), rows))

    def _on_task(self, event, t, extra=None):
        if event in ("done", "cancelled"):
            self.orders.append((t.id, t.ref, t.kind, t.title, t.source, t.priority, round(t.created, 1),
                                round(t.started, 1) if t.started is not None else None,
                                round(t.finished if t.finished is not None else self.sim.sim_time, 1),
                                event, json.dumps(t.robots)))

    def flush(self, final: bool = False):
        if self.sid is None or self.closed:
            return
        s, st = self.sim, self.store
        st.add_frames(self.sid, self.frames)
        self.frames = []
        new = [e for e in s.events.buf if e["id"] > self.ev_seen]
        if new:
            self.ev_seen = max(e["id"] for e in new)
            st.add_events(self.sid, sorted(new, key=lambda e: e["id"]))
        st.add_orders(self.sid, self.orders)
        self.orders = []
        st.update_shift(self.sid, s.kpis(), list(s.history), round(s.sim_time, 1), ended=final)
        if final:
            self.closed = True              # shift over: the twin idles on, the recording is complete
