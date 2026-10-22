"""Vendor adapter base class + the message bus used by the protocol inspector.

The fleet manager only ever talks in ONE common language (a path of cells + an action label,
and a robot state). Each vendor adapter translates that into the vendor's own wire format and
back. Adding a new vendor = write one subclass and register it in adapters/__init__.py.
"""
from __future__ import annotations
import json
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass


@dataclass
class Wire:
    """One message on the (simulated) wire."""
    proto: str                 # e.g. "VDA5050/MQTT"
    topic: str                 # MQTT topic, HTTP route, frame name ...
    payload: object            # JSON-able dict
    direction: str = "out"     # out = manager -> robot, in = robot -> manager

    def text(self) -> str:
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload, separators=(",", ":"))


class Bus:
    """Ring buffer of recent wire messages (feeds the console's Protocol tab)."""
    def __init__(self, maxlen: int = 400):
        self.buf: deque = deque(maxlen=maxlen)
        self.seq = 0

    def log(self, t: float, vendor: str, vendor_name: str, w: Wire):
        self.seq += 1
        self.buf.appendleft({"id": self.seq, "t": round(t, 1), "vendor": vendor, "vendor_name": vendor_name,
                             "proto": w.proto, "dir": w.direction, "topic": w.topic, "body": w.text()[:900]})

    def recent(self, n: int = 40, vendor: str | None = None) -> list:
        out = [m for m in self.buf if vendor is None or m["vendor"] == vendor]
        return out[:n]


def compress(start: tuple, path: list) -> list:
    """Cell path -> corner nodes (start, every turn, end). Vendors take waypoints, not every cell."""
    pts = [tuple(start)] + [tuple(c) for c in path]
    if len(pts) <= 2:
        return pts
    nodes = [pts[0]]
    for i in range(1, len(pts) - 1):
        d1 = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        d2 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if d1 != d2:
            nodes.append(pts[i])
    nodes.append(pts[-1])
    return nodes


def expand(nodes: list) -> list:
    """Corner nodes -> every cell on the way (excluding the first node). Inverse of compress()."""
    cells: list = []
    for a, b in zip(nodes, nodes[1:]):
        dx, dy = (b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])
        x, y = a
        while (x, y) != tuple(b):
            x, y = x + dx, y + dy
            cells.append((x, y))
    return cells


def iso(t: float) -> str:
    t = int(t)
    return f"2026-10-21T{(8 + t // 3600) % 24:02d}:{t % 3600 // 60:02d}:{t % 60:02d}.000Z"


class VendorAdapter(ABC):
    kind = "base"
    protocol = "?"

    def __init__(self, type_key: str, type_cfg: dict, bus: Bus):
        self.type_key, self.cfg, self.bus = type_key, type_cfg, bus
        self.vendor = type_cfg["vendor"]
        self.latency_ms = type_cfg.get("latency_ms", 20)
        self.online = True
        self.sent = self.received = 0
        self.last_seen: float | None = None
        self.header = 0

    # ---- the four methods a new vendor has to implement ----------------------------------
    @abstractmethod
    def encode_order(self, now, robot, path, target, label, task_id) -> list: ...
    @abstractmethod
    def decode_order(self, msgs: list, start: tuple) -> tuple: ...
    @abstractmethod
    def encode_state(self, now, robot) -> list: ...
    @abstractmethod
    def decode_state(self, msgs: list) -> dict: ...

    # ---- common behaviour ---------------------------------------------------------------
    def next_header(self) -> int:
        self.header += 1
        return self.header

    def send_order(self, now, robot, path, target, label, task_id):
        """Translate -> put on the wire -> robot side decodes. Returns what the ROBOT received."""
        msgs = self.encode_order(now, robot, path, target, label, task_id)
        for m in msgs:
            self.bus.log(now, self.type_key, self.vendor, m)
            if m.direction == "out":
                self.sent += 1
            else:
                self.received += 1
        outs = [m for m in msgs if m.direction == "out"]
        return self.decode_order(outs, robot.cell)

    def poll(self, now, robot):
        """One telemetry round trip: robot publishes vendor-format state, adapter decodes it."""
        if not self.online:
            return
        msgs = self.encode_state(now, robot)
        for m in msgs:
            self.bus.log(now, self.type_key, self.vendor, m)
            self.received += 1
        robot.telemetry = self.decode_state(msgs)
        self.last_seen = now

    def status(self, now) -> dict:
        return {"id": self.type_key, "vendor": self.vendor, "kind": self.kind, "protocol": self.protocol,
                "online": self.online, "sent": self.sent, "received": self.received,
                "last_seen_age": None if self.last_seen is None else round(now - self.last_seen, 1),
                "latency_ms": self.latency_ms}
