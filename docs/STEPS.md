# Build log - what we built and why

## Phase 1 - The twin comes alive
1. **Config-driven map** (`config/warehouse.yaml`): size, shelves, zones, robot types. New layout = edit YAML, no code change (a task requirement).
2. **Grid** (`grid.py`): turns shelf rectangles into a set of blocked cells. Ground robots cannot enter them; the drone can fly over. Clicks on a shelf snap to the nearest free cell (BFS).
3. **A\*** (`planner.py`): 4-neighbour moves, Manhattan heuristic (admissible, so paths are shortest).
4. **Robot model** (`robots.py`): one common model (type, vendor, interface, speed, battery, state, path). Every vendor looks the same to the fleet manager; vendor differences go into adapters in Phase 3.
5. **Movement**: each tick a robot gets a distance budget `speed * dt` cells and consumes it along its path; battery drains per cell.
6. **Simulation** (`sim.py`): owns grid + robots, `time_scale` runs faster than real time (needed for 8-hour shifts and 10-run comparisons).
7. **API** (`main.py`): REST for map/robots/goto/speed, WebSocket pushes the fleet state 10x/second, FastAPI also serves the frontend.
8. **TusCore Fleet Console** (`frontend/index.html`): canvas map, KPI cards, robot/vendor/task tabs, brand colors.
