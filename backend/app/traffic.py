"""Traffic management primitives.

Every ground robot owns the cell it stands on and, while it is moving, also the next cell it
has claimed. A robot may only leave a cell after it has claimed the next one - so two robots
can never be in the same cell (zero collisions by construction). The drone flies in its own
airspace and is not part of this.
"""


class TrafficManager:
    def __init__(self):
        self.occ: dict = {}          # cell -> robot id

    def owner(self, cell):
        return self.occ.get(cell)

    def claim(self, cell, rid):
        self.occ[cell] = rid

    def release(self, cell, rid):
        if self.occ.get(cell) == rid:
            del self.occ[cell]

    def occupied(self, except_id=None) -> set:
        return {c for c, o in self.occ.items() if o != except_id}


def find_cycles(waits: dict) -> list:
    """waits: robot -> robot it is waiting for. Returns the cycles in that wait-for graph.
    A cycle (A waits for B waits for ... A) means nobody can ever move = a deadlock."""
    seen: set = set()
    cycles = []
    for start in waits:
        if start in seen:
            continue
        chain, pos, cur = [], {}, start
        while cur is not None and cur not in seen:
            pos[cur] = len(chain)
            chain.append(cur)
            cur = waits.get(cur)
            if cur in pos:
                cycles.append(chain[pos[cur]:])
                break
        seen.update(chain)
    return cycles
