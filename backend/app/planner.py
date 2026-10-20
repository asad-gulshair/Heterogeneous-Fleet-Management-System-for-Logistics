"""A* path planning on the grid (4-neighbour moves, Manhattan heuristic)."""
import heapq
from .grid import Grid


def astar(grid: Grid, start, goal, flying: bool = False):
    """Return list of cells from start (exclusive) to goal (inclusive), or None if unreachable."""
    if start == goal:
        return []
    if not grid.passable(*goal, flying):
        return None

    def h(c):  # Manhattan distance: admissible for 4-neighbour moves
        return abs(c[0] - goal[0]) + abs(c[1] - goal[1])

    open_heap = [(h(start), 0, start)]
    came_from = {start: None}
    g_cost = {start: 0}
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
            if not grid.passable(*n, flying):
                continue
            ng = g + 1
            if ng < g_cost.get(n, 1 << 30):
                g_cost[n] = ng
                came_from[n] = cur
                heapq.heappush(open_heap, (ng + h(n), ng, n))
    return None
