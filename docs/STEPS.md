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

## Phase 2 - The right robot for the right job
1. **Capabilities** (`warehouse.yaml`): each robot type lists what it can do (`carry_tote`, `high_pick`, `move_pallet`, `scan`). Orders ask for capabilities, never for a specific vendor - this is what makes the fleet heterogeneous but manageable as one.
2. **Task model** (`tasks.py`): an order is a set of *plans*, one per role. A plan is a list of `Action(cell, dwell, label, sync)`. Simple orders have one plan; a high-shelf pick has two (scissor lift + tote AMR).
3. **Order generator** (`orders.py`): builds the plans for the four order kinds and schedules arrivals as a Poisson process per kind (exponential gaps). A fixed random seed makes every run reproducible - required for the fair Phase 4 comparison.
4. **Hand-over** (`sync="handoff"`): the lift picks from the high shelf while the tote AMR drives to the neighbouring aisle cell; whoever arrives first waits, and the hand-over starts only when both are there. Then the tote delivers to packing.
5. **Allocation** (`allocator.py`): cost = travel time (Manhattan distance / robot speed) to the first action.
   - *Greedy*: oldest order first, nearest free capable robot. Simple baseline.
   - *Hungarian*: per capability, solves the optimal assignment of waiting orders to free robots (minimum total travel time) with `scipy.optimize.linear_sum_assignment`. An aging term (0.5 s of cost per second waited) stops far-away orders from starving. A multi-role order is dispatched only when every role can be filled.
6. **Robot state machine** (`sim.py`): idle -> moving -> working (dwell) -> next action ... -> idle. `waiting` is used at hand-overs. Robots below 30 % battery stop taking orders and go to a free charger bay until 90 %. (Smart charge scheduling comes in Phase 3.)
7. **KPIs**: orders completed, orders/hour, average wait (created -> started), average cycle, utilization (busy time / total time), energy (battery % x pack kWh), stock mismatches found by the drone.
8. **API** (`/docs` shows it live): `POST /api/orders`, `GET /api/fleet`, `GET /api/orders`, `GET /api/kpis`, `POST /api/sim/allocator`, `/api/sim/speed`, `/api/sim/auto-orders`, `/api/sim/reset`.
9. **Headless run** (`scripts/run_shift.py`): simulates a whole 8-hour shift in ~10 s so we can compare strategies without waiting.

### Measured result (seed 42, default load, 8 h shift, 2435 orders - all completed)
| | Greedy | Hungarian |
|---|---|---|
| Avg wait | 156 s | 138 s (-12 %) |
| Avg cycle | 234 s | 215 s (-8 %) |
| Energy | 49.4 kWh | 48.8 kWh |

At light load (utilization ~34 %) both are identical because a free robot is always nearby - the allocator only matters when robots are scarce. The pallet stackers (2 robots) are the bottleneck at higher loads.
