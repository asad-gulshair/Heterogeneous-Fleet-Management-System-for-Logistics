"""A* path planning on the grid (4-neighbour moves, Manhattan heuristic).

Lane discipline: moves in the 'wrong' lane cost slightly more, so opposing traffic naturally
uses different lanes (like driving on one side of the road) instead of meeting head-on.
  east-bound -> even rows, west-bound -> odd rows, south-bound -> even columns, north-bound -> odd columns
"""
import heapq
from .grid import Grid

LANE_PENALTY = 0.3
CLOSED_PENALTY = 60.0     # aisle closed for maintenance: only used if there is no other way (e.g. to drive out)


def _lane_cost(cur, n) -> float:
    dx, dy = n[0] - cur[0], n[1] - cur[1]
    if dx:                                   # horizontal move: lane = row parity
        return 1.0 if (n[1] % 2 == 0) == (dx > 0) else 1.0 + LANE_PENALTY
    return 1.0 if (n[0] % 2 == 0) == (dy > 0) else 1.0 + LANE_PENALTY


def astar(grid: Grid, start, goal, flying: bool = False, blocked=None):
    """Return list of cells from start (exclusive) to goal (inclusive), or None if unreachable.
    `blocked` = extra cells to avoid (e.g. robots that are standing still)."""
    blocked = blocked or ()
    if start == goal:
        return []
    if not grid.passable(*goal, flying):
        return None

    def h(c):  # Manhattan distance: admissible because every step costs >= 1
        return abs(c[0] - goal[0]) + abs(c[1] - goal[1])

    open_heap = [(h(start), 0.0, start)]
    came_from = {start: None}
    g_cost = {start: 0.0}
    while open_heap:
        _, g, cur = heapq.heappop(open_heap)
        if cur == goal:
            path = []
            while cur != start:
                path.append(cur)
                cur = came_from[cur]
            return path[::-1]
        if g > g_cost[cur]:
            continue  # stale heap entry
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cur[0] + dx, cur[1] + dy)
            if n in blocked or not grid.passable(*n, flying):
                continue
            ng = g + (1.0 if flying else _lane_cost(cur, n) + (CLOSED_PENALTY if n in grid.closed else 0.0))
            if ng < g_cost.get(n, 1e18):
                g_cost[n] = ng
                came_from[n] = cur
                heapq.heappush(open_heap, (ng + h(n), ng, n))
    return None
