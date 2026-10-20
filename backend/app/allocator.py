"""Task allocation. Both strategies answer: which free robot takes which waiting order?

Cost of giving robot r a role of task t = travel time (Manhattan distance / speed) to the
first place it must go. Multi-robot tasks (high pick = lift + tote) are dispatched only if
every role can be filled in the same round, otherwise the task keeps waiting.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import linear_sum_assignment

AGING = 0.5      # seconds of cost removed per second a task has waited (stops starvation)
WINDOW = 60      # Hungarian looks at the oldest N queued tasks per round


def travel(robot, task, role) -> float:
    c = task.plans[role][0].cell
    return (abs(robot.cell[0] - c[0]) + abs(robot.cell[1] - c[1])) / robot.speed


class Greedy:
    """FIFO: oldest order first, each role gets the nearest free capable robot."""
    name = "greedy"

    def allocate(self, tasks, free, now):
        avail, result = list(free), {}
        for t in tasks:
            pick, taken = {}, set()
            for role in t.plans:
                cands = [r for r in avail if role in r.capabilities and r.id not in taken]
                if not cands:
                    break
                best = min(cands, key=lambda r: travel(r, t, role))
                pick[role] = best.id
                taken.add(best.id)
            else:
                result[t.id] = pick
                avail = [r for r in avail if r.id not in taken]
            if not avail:
                break
        return result


class Hungarian:
    """Globally optimal assignment per capability (minimum total travel time, with aging)."""
    name = "hungarian"

    def allocate(self, tasks, free, now):
        tasks = tasks[:WINDOW]
        picks = {t.id: {} for t in tasks}
        used: set = set()
        caps = sorted({c for r in free for c in r.capabilities})
        for cap in caps:
            robs = [r for r in free if cap in r.capabilities and r.id not in used]
            dem = [t for t in tasks if cap in t.plans]
            if not robs or not dem:
                continue
            cost = np.array([[travel(r, t, cap) - AGING * (now - t.created) for r in robs] for t in dem])
            rows, cols = linear_sum_assignment(cost)   # rectangular matrices are fine
            for i, j in zip(rows, cols):
                picks[dem[i].id][cap] = robs[j].id
                used.add(robs[j].id)
        # keep only tasks where every role found a robot
        return {t.id: picks[t.id] for t in tasks if len(picks[t.id]) == len(t.plans)}


ALLOCATORS = {"greedy": Greedy, "hungarian": Hungarian}
