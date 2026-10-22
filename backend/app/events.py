"""Event log: what happened and when (faults, deadlocks, reassignments, outages ...)."""
from collections import deque


class EventLog:
    def __init__(self, maxlen: int = 300):
        self.buf: deque = deque(maxlen=maxlen)
        self.n = 0

    def add(self, t: float, sev: str, kind: str, msg: str):
        """sev: info | ok | warn | error"""
        self.n += 1
        self.buf.appendleft({"id": self.n, "t": round(t, 1), "sev": sev, "kind": kind, "msg": msg})

    def recent(self, n: int = 40) -> list:
        return list(self.buf)[:n]
