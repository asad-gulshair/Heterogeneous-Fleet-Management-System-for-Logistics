"""Common robot model. Every vendor's robot looks like this to the fleet manager."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Robot:
    id: str
    rtype: str            # key in robot_types config
    label: str
    vendor: str
    interface: str
    speed: float          # cells per second
    flying: bool
    cell: tuple           # last cell the robot fully reached
    capabilities: list = field(default_factory=list)
    kwh: float = 1.0      # battery pack size, for the energy KPI
    battery: float = 100.0
    # idle | moving | working | waiting | to_charge | charging | fault
    state: str = "idle"
    target: tuple | None = None
    path: list = field(default_factory=list)   # remaining cells to visit
    frac: float = 0.0     # progress (0..1) from `cell` towards path[0]
    dist: float = 0.0     # odometer: cells travelled
    # --- task execution ---
    task_id: int | None = None
    role: str | None = None
    plan: list = field(default_factory=list)   # list[Action] for the current task
    idx: int = 0                               # index of the current action
    dwell_left: float = 0.0
    action: str | None = None                  # label of the current action
    bay: tuple | None = None                   # reserved charger bay
    # --- statistics ---
    busy_time: float = 0.0
    energy_kwh: float = 0.0

    @property
    def pos(self) -> tuple[float, float]:
        """Smooth (float) position for drawing, interpolated between cells."""
        if self.path:
            nx, ny = self.path[0]
            return (self.cell[0] + (nx - self.cell[0]) * self.frac,
                    self.cell[1] + (ny - self.cell[1]) * self.frac)
        return (float(self.cell[0]), float(self.cell[1]))

    def set_path(self, path, target, state="moving"):
        self.path = list(path)
        self.target = target
        self.frac = 0.0
        self.state = state

    def clear_task(self):
        self.task_id = None
        self.role = None
        self.plan = []
        self.idx = 0
        self.action = None
        self.dwell_left = 0.0

    def step(self, dt: float, drain_per_cell: float):
        """Advance along the path using a distance budget = speed * dt (in cells)."""
        budget = self.speed * dt
        while budget > 0 and self.path:
            remaining = 1.0 - self.frac
            if budget >= remaining:
                self.cell = self.path.pop(0)
                self.frac = 0.0
                budget -= remaining
                self.dist += 1
                self.battery = max(0.0, self.battery - drain_per_cell)
                self.energy_kwh += drain_per_cell / 100.0 * self.kwh
            else:
                self.frac += budget
                budget = 0
        if not self.path:
            self.target = None

    def to_dict(self) -> dict:
        x, y = self.pos
        return {
            "id": self.id, "type": self.rtype, "label": self.label,
            "vendor": self.vendor, "interface": self.interface,
            "x": round(x, 3), "y": round(y, 3),
            "battery": round(self.battery, 1), "state": self.state,
            "target": list(self.target) if self.target else None,
            "path_len": len(self.path),
            "path": [list(c) for c in self.path],
            "dist": round(self.dist, 1),
            "task": self.task_id, "action": self.action,
        }
