"""Orders (tasks). A task is a set of per-role plans; each plan is a list of Actions."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Action:
    cell: tuple             # where the robot must be
    dwell: float            # seconds it works there (picking, dropping, ...)
    label: str              # human readable, shown in the UI
    sync: str | None = None  # if set, all robots with the same sync key must arrive before work starts


@dataclass
class Task:
    id: int
    kind: str                       # box_pick | high_pick | pallet_move | inventory_scan
    title: str
    created: float                  # simulated seconds
    plans: dict                     # role (capability) -> list[Action]
    status: str = "queued"          # queued | in_progress | done
    robots: dict = field(default_factory=dict)       # role -> robot id
    started: float | None = None
    finished: float | None = None
    done_roles: set = field(default_factory=set)
    done_actions: int = 0
    sync: dict = field(default_factory=dict)         # sync key -> set(robot ids waiting)
    mismatch: bool = False           # inventory scan found a stock mismatch
    manual: bool = False
    counted: bool = False            # wait time already added to the KPI (only the first dispatch counts)
    reassigned: int = 0              # how often the order was re-queued after a robot fault
    then: tuple | None = None        # (role, actions) follow-up leg created when this leg finishes (separate managers)
    not_before: float = 0.0          # may not be dispatched before this time (WMS hand-over delay)
    leg: int = 1                     # 1 = first leg, 2 = follow-up leg

    @property
    def total_actions(self) -> int:
        return sum(len(p) for p in self.plans.values())

    def pickup(self) -> tuple:
        """First place a robot has to go - drawn as a marker on the map while queued."""
        return next(iter(self.plans.values()))[0].cell

    def to_dict(self, now: float) -> dict:
        wait = (self.started if self.started is not None else now) - self.created
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "robots": self.robots, "roles": list(self.plans.keys()),
            "progress": round(self.done_actions / max(1, self.total_actions), 2),
            "created": round(self.created, 1), "wait": round(wait, 1),
            "pickup": list(self.pickup()), "manual": self.manual,
            "mismatch": self.mismatch, "reassigned": self.reassigned,
            "cycle": round(self.finished - self.created, 1) if self.finished is not None else None,
        }
