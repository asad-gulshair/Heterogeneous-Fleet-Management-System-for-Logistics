"""Alarm management (ISA-18.2 style, kept simple).

An alarm is raised when a condition starts (robot fault, battery critical, vendor offline ...) and has two
independent flags: active (the condition is still there) and acknowledged (an operator has seen it).

    active + unacked   -> raised, needs attention
    active + acked     -> someone is on it, stays until the condition clears
    cleared + unacked  -> it went away by itself, but nobody has seen it yet: stays until acknowledged
    cleared + acked    -> closed (history only)

Events that have no "still active" state (collision, permanent deadlock) are raised already cleared, so they
stay on the list until someone acknowledges them. Every alarm is stored with who acknowledged it and when.
"""
from __future__ import annotations
import time

LEVELS = {"critical": 0, "major": 1, "minor": 2, "info": 3}
CHECK_S = 1.0


class AlarmManager:
    def __init__(self, store=None):
        self.store = store
        self.open: dict[tuple, dict] = {}         # (code, subject) -> alarm
        self.sim = None
        self.sources: list = []                   # extra condition providers: f(sim) -> [(code, subject, level, title, detail)]
        self._seq = 0

    def attach(self, sim):
        for a in list(self.open.values()):        # the old shift is over: close what is left of it
            a["state"] = "cleared"
            a["cleared"] = a.get("cleared") or (self.sim.sim_time if self.sim else 0)
            self._save(a)
        self.open = {}
        self.sim = sim
        self._next = 0.0
        self._count = {"collisions": 0, "permanent": 0}
        sim.step_hooks.append(self._tick)

    # ------------------------------------------------------------ conditions
    def _conditions(self) -> list:
        s, out = self.sim, []
        for r in s._order:
            if r.state == "fault":
                out.append(("robot_fault", r.id, "critical", f"{r.id} out of service",
                            f"repair in {max(0, r.repair_at - s.sim_time):.0f} s"))
            elif r.battery < s.critical:
                out.append(("battery_critical", r.id, "major", f"{r.id} battery critical", f"{r.battery:.0f} %"))
            elif r.blocked_for >= 60:
                out.append(("robot_stuck", r.id, "minor", f"{r.id} stuck in traffic", f"waiting {r.blocked_for:.0f} s for {r.blocked_by}"))
        for a in s.adapters.values():
            if not a.online:
                out.append(("vendor_offline", a.vendor, "critical", f"{a.vendor} offline", f"{a.protocol}: no new missions"))
        if s.paused:
            out.append(("fleet_paused", "fleet", "major", "Fleet paused", "all robots holding position"))
        for row in sorted(s.closed_rows):
            out.append(("aisle_closed", s.grid.rack_name(row), "info", f"Aisle {s.grid.rack_name(row)} closed", "maintenance, traffic re-routed"))
        late = sum(1 for t in s.tasks.values() if s.sim_time - t.created > s.sla_s)
        if late:
            out.append(("orders_late", "sla", "major", "Orders past SLA", f"{late} order(s) older than {s.sla_s // 60} min"))
        if len(s.queue) > max(10, 2 * len(s.robots)):
            out.append(("backlog", "queue", "minor", "Order backlog growing", f"{len(s.queue)} orders waiting"))
        need = sum(1 for r in s._order if r.state == "idle" and r.task_id is None and r.battery < s.low and not r.flying)
        if need >= 2:
            out.append(("charger_shortage", "chargers", "minor", "Robots waiting for a charger", f"{need} robots below {s.low:.0f} %"))
        for f in self.sources:
            out += f(s)
        return out

    def _tick(self):
        s = self.sim
        if s.sim_time < self._next:
            return
        self._next = s.sim_time + CHECK_S
        now_keys = set()
        for code, subj, level, title, detail in self._conditions():
            key = (code, subj)
            now_keys.add(key)
            a = self.open.get(key)
            if a is None or a["state"] == "cleared_unacked":
                if a is not None:                  # came back before anyone acked: count it as a new alarm
                    self._close(a)
                self._raise(code, subj, level, title, detail, "active_unacked")
            else:
                a["detail"] = detail
        for key, a in list(self.open.items()):
            if key in now_keys:
                continue
            if a["state"] == "active_unacked":
                a["state"], a["cleared"] = "cleared_unacked", s.sim_time
                self._save(a)
            elif a["state"] == "active_acked":
                a["cleared"] = s.sim_time
                self._close(a)
        # one-off events
        for stat, code, title in (("collisions", "collision", "Collision"), ("permanent", "deadlock", "Permanent deadlock")):
            n = s.stats[stat]
            if n > self._count[stat]:
                self._count[stat] = n
                self._raise(code, f"{code}-{n}", "critical", title, f"#{n} at {s.sim_time / 60:.1f} min", "cleared_unacked")

    def _raise(self, code, subj, level, title, detail, state):
        s = self.sim
        a = {"id": None, "code": code, "subject": subj, "level": level, "title": title, "detail": detail,
             "raised": round(s.sim_time, 1), "state": state, "acked_by": None, "acked_wall": None,
             "cleared": round(s.sim_time, 1) if state == "cleared_unacked" else None}
        if self.store:
            a["id"] = self.store.alarm_raise(a)
        else:
            self._seq += 1
            a["id"] = self._seq
        self.open[(code, subj)] = a
        sev = "error" if level == "critical" else "warn" if level in ("major", "minor") else "info"
        s.events.add(s.sim_time, sev, "alarm", f"ALARM {level.upper()}: {title} ({detail})")

    def _close(self, a):
        a["state"] = "cleared"
        self._save(a)
        self.open.pop((a["code"], a["subject"]), None)

    def _save(self, a):
        if self.store and a.get("id"):
            self.store.alarm_update(a)

    # ------------------------------------------------------------ operator actions
    def ack(self, aid: int, user: str) -> bool:
        for a in list(self.open.values()):
            if a["id"] == aid:
                self._ack(a, user)
                return True
        return False

    def ack_all(self, user: str) -> int:
        n = 0
        for a in list(self.open.values()):
            if a["state"] in ("active_unacked", "cleared_unacked"):
                self._ack(a, user)
                n += 1
        return n

    def _ack(self, a, user):
        a["acked_by"], a["acked_wall"] = user, time.time()
        if a["state"] == "active_unacked":
            a["state"] = "active_acked"
            self._save(a)
        elif a["state"] == "cleared_unacked":
            self._close(a)

    def list(self) -> list:
        return sorted(self.open.values(), key=lambda a: (a["state"] not in ("active_unacked", "cleared_unacked"),
                                                         LEVELS[a["level"]], -a["raised"]))

    def summary(self) -> dict:
        al = self.list()
        unacked = [a for a in al if a["state"] in ("active_unacked", "cleared_unacked")]
        return {"open": len(al), "unacked": len(unacked),
                "worst": min((a["level"] for a in unacked), key=lambda l: LEVELS[l], default=None)}
