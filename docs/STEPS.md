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

## Phase 3 - Sharing the floor safely
Note: the Phase 2 numbers above were measured *before* traffic control, faults and charging staggering were added; they are the idealised baseline.

1. **Cell reservation** (`traffic.py`, `sim._move`): a ground robot owns the cell it stands on and must *claim* the next cell before leaving. Two robots can therefore never share a cell - collisions are impossible by construction, and a collision counter verifies it every step (always 0).
2. **Lane discipline** (`planner.py`): A* charges slightly more for the "wrong" lane (east on even rows, west on odd rows, ...), so opposing traffic naturally uses different lanes. Deadlocks dropped from ~1300 to ~150 per shift.
3. **Priority** (`robots.priority`): processing order and who yields. Pallet stacker 3 > scissor lift 2 > tote 1 -> pallets first in narrow aisles.
4. **Deadlock handling** (`sim._resolve_traffic`): build the wait-for graph (robot -> robot whose cell it needs, or whose hand-over it waits for). A cycle = deadlock. Resolution ladder: (a) the robot that holds a contested cell and has the lowest priority detours or side-steps; (b) idle robots standing in someone's way are told to make way (evade); (c) robots blocked by a standing robot re-plan around it; (d) if the same deadlock lasts 10 s, the newest order is preempted and re-queued; (e) watchdog after 300 s (never triggered in stress tests).
5. **Order site reservation**: an order only starts if none of its work cells (pick, hand-over, drop) is reserved by another active order. This removed the "two high picks on the same bay" deadlock at its root.
6. **Faults** (`inject_fault`, `_requeue`): random (Poisson, MTBF 6 h per robot) or injected from the console. The robot stops where it is (and blocks its cell), its order - and the partner robot - are released and the order returns to the queue front; the robot is repaired after 120 s.
7. **Staggered charging** (`_charge_decision`): critical (<12 %) charges at once; <30 % charges if at most half of that robot type is already charging; <50 % tops up only when nothing is waiting for that robot's skills. Limited charger bays.
8. **Vendor adapters** (`adapters/`): the manager speaks one common language; each vendor adapter encodes it into the vendor's wire format and decodes the robot's replies - VDA 5050 over MQTT topics (`uagv/v2/.../order|state`), REST/JSON missions, CRC-framed TCP/JSON, MAVLink-style mission items. All traffic is logged to the Protocol tab. Adding a vendor = one subclass + one line in the registry + `adapter:` in the YAML. Outage toggle: no new missions to that vendor while offline.
9. **Event log + safety strip**: collisions, deadlocks auto-resolved, permanent deadlocks, faults/reassignments, time lost to traffic.
10. **Tests** (`backend/tests`): A*, adapter round-trips for all four protocols, deadlock detection, a 1-hour safe shift, fault recovery. Run `python -m pytest -q`.

### Verified (headless 8-hour shifts, high load 190 box + 50 high picks/h, ~15 random faults per shift)
12 runs (6 seeds x greedy/Hungarian): every order completed, **0 collisions, 0 permanent deadlocks**.
Honest finding: with real traffic, the allocator (greedy vs Hungarian) changes average wait only a few percent - the bottleneck is robot count and congestion, not assignment. The headline for clients is the Phase 4 comparison of separate vendor managers vs one TusCore.

## Phase 4 - Dashboard, comparison and handover
1. **Scenario A - separate vendor managers** (`sim.mode = "separate"`). Modelled, not assumed: each vendor's manager only sees its own robots, so
   - a deadlock between two vendors' robots is invisible to both managers -> it lasts until an operator clears it (`operator_response_s`, 90 s);
   - another vendor's idle robot standing in the way cannot be asked to move -> operator moves it after the same delay;
   - a high-shelf pick becomes two separate jobs: the lift's manager reports "done", the WMS notices and creates the tote job `wms_handoff_delay_s` (20 s) later - no synchronized hand-over;
   - each vendor has dedicated chargers (`chargers_per_vendor`).
   Everything else (A*, cell reservation inside one vendor's fleet, allocation algorithm, the orders, the faults) is identical, so the comparison isolates the effect of *coordination*.
2. **Fair experiments** (`experiments.py`): both scenarios use the same seeds -> identical orders and identical fault times. Runs execute in parallel worker processes (never touching the live twin). Results are saved in `results/` and shown on the Compare page with mean ± standard deviation.
3. **Result** (busy day x1.15, 10 x 8 h each): cycle time -22 %, wait -16 %, time lost to traffic -79 %, operator interventions 46 -> 0, zero collisions in both. Normal day: cycle -14 %, traffic -78 %. Peak x1.3: both setups saturate (waits ~18 min) - TusCore still completes +1.7 % more orders and is 18 % faster per order. Large layout (19 robots, 3 runs): cycle -11 %, traffic -72 %, wait +9 % (small sample) - reported as measured.
   Honest reading: throughput is equal whenever demand is below fleet capacity; TusCore's value is speed, smoothness and no manual intervention, and extra capacity only at saturation.
4. **What-if** (`apply_overrides`): robot counts per type, order rates / volume factor, number of chargers, layout file. Baseline and scenario run with the same seeds. The ROI card turns the KPI deltas into extra orders and robot hours per year vs the extra robot investment (all inputs editable).
5. **KPI history**: one sample per simulated minute (rolling throughput, queue, utilization per type from busy-time deltas, battery, faulted robots) -> Analytics charts.
6. **Shift report**: `/api/report` (JSON), `/api/report.csv`, and a printable branded page `report.html` (print -> PDF).
7. **Real MQTT**: `mqtt_bridge.py` publishes every VDA 5050 order/state message on its real topic (`uagv/v2/<manufacturer>/<serial>/order|state`) when `MQTT_HOST` is set. Verified against a live broker (62 messages in a short run). `docker-compose.yml` starts Eclipse Mosquitto next to TusCore.
8. **3D digital twin** (`frontend/js/twin3d.js`, Three.js, bundled locally): racks with stock, pallet area, packing stations, chargers, dock doors. Each robot type is its own animated model - the scissor lift raises its platform to the high shelf and lowers it for the hand-over, the pallet stacker lifts pallets on its forks, the tote AMR carries a bin, the drone takes off, spins its rotors and shows a scan beam over the racks. Floating vendor markers keep every robot readable from far away; red beacon = fault, amber = waiting for traffic. Orbit / top / follow / auto-orbit cameras.
9. **Console structure**: `index.html` + `css/app.css` + ES modules (`app.js` live data and panels, `plan2d.js`, `twin3d.js`, `pages.js`). The robot list updates in place (no re-render), so buttons are never replaced while being clicked.

### Suggested 3-5 minute demo video
1. (0:00) Live 3D twin at 10x - "12 robots, 4 vendors, 4 protocols, one TusCore". Orbit, then follow the scissor lift during a high-shelf pick and hand-over.
2. (0:45) Protocol tab - the same core speaking VDA 5050, REST, TCP/JSON and MAVLink-style.
3. (1:15) Inject a fault on a busy robot - beacon turns red, order is reassigned (Events tab). Simulate a vendor outage.
4. (2:00) Click the mode pill -> separate managers: watch congestion and operator interventions grow.
5. (2:40) Compare page - headline numbers. What-if: add two tote AMRs at peak volume -> ROI and payback.
6. (3:40) API page + `/docs`: "any WMS can submit orders". Close on the shift report.


## v1.1 - client readiness
1. **Navigation bug fixed**: the left menu buttons had no click handler (pages only opened via URL). Every page now opens from the menu; verified by clicking at 1366×768, 1536×864 and 1920×1080.
2. **Windows fix**: `.js` / `.css` MIME types registered explicitly (some Windows registries serve `.js` as `text/plain`, which blocks ES modules).
3. **No overlapping text**: safety strip stacks value over label, header collapses labels below 1500 px, KPI sub-lines rewritten, parked robots get small faint markers.
4. **Realistic material flow in 3D**: real shelf slots empty when picked and restock later; cartons fly into the tote bin, onto the packing table and along the conveyor; lift takes cartons from the top level and hands them to the tote; waiting pallets drawn at their real pickup cell, carried on the forks, stacked at the dock, truck departs when 6 are staged.
5. **Real-time operations**: fleet pause (emergency), aisle maintenance closure (high A* cost + orders wait), send-to-charger, live alerts, congestion heatmap (`/api/heatmap`), on-time KPI (SLA 10 min).
6. **Overview page** for clients (vendor health, live pipeline counters, alerts, proven value).
7. **Scale**: `warehouse_xl.yaml`, 40 robots. Full 8 h shifts: ~4,100-4,250 orders, 0 collisions, 0 permanent deadlocks, TusCore cycle -12 %, traffic -76 %, operator interventions 94 -> 0.
8. **3D performance**: shadow map refreshed every 5th frame, pixel ratio capped at 1.5, automatic low-quality mode (no shadows) if a machine renders < 22 fps.

### v1.2 - realistic carton handling in the 3D twin
- Tote AMR has a telescopic handling unit: the mast rises to the shelf level, two side arms reach into the rack,
  the carton is pulled straight onto the tray and lowered into the robot's bin (no flying boxes).
- The carton keeps its own colour and size from shelf slot to bin to conveyor.
- At packing the robot docks side-on at the conveyor, lifts the carton to belt height and pushes it onto the rollers.
  The carton rides into the PACK & SHIP tunnel and leaves the twin there.
- Scissor lift: carton slides straight out of the top level onto the raised platform; hand-over slides it across into the tote AMR's bin.
- Visual only: the simulation, its KPIs and all results are unchanged.
