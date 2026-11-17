# TusCore Fleet Manager - Logistics Digital Twin

**One brain for many robots.** TusCore coordinates a heterogeneous warehouse fleet - four robot types
from four vendors speaking four different protocols - as one system, and shows it live as a 3D digital twin.

| Robot | Count | Vendor | Interface (adapter) | Job |
|---|---|---|---|---|
| Tote AMR | 6 | Vendor A | VDA 5050 over MQTT | boxes from shelves to packing |
| Scissor-lift AMR (TL-65) | 3 | Tusmec | REST API | high-shelf picks, hands totes to a tote AMR |
| Pallet stacker AGV | 2 | Vendor C | custom TCP/JSON (CRC-framed) | pallets from pallet area to loading dock |
| Inventory drone | 1 | Vendor D | MAVLink-style | flies over racks, counts stock, flags mismatches |

## Quick start

**Windows (no Docker):** double-click `run.bat`, then open http://localhost:8000

**Docker (one command, includes a real Mosquitto MQTT broker):**
```bash
docker compose up --build
```
Open http://localhost:8000 - API docs at http://localhost:8000/docs.

**First login:** `admin` / `admin` (full access) or `operator` / `operator` (control room). Both must choose a new
password at the first login. Set `TUSCORE_ADMIN_PASSWORD` before the first start to skip the default admin password,
or `TUSCORE_AUTH=off` for a single-user demo without login. Everything that must survive a restart (users, shift
recordings, alarms, missions, layouts, floor plans) is in the `data/` folder (`data/tuscore.db` is the database).
Watch the VDA 5050 traffic on the broker: `mosquitto_sub -h localhost -t "uagv/#" -v`

**Manual:**
```powershell
cd backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --reload-dir app --reload-dir ../config
```

## What the console shows
- **Layout** - upload a floor plan (PDF / image), set its scale with two clicks, trace racks, walls, no-go zones,
  packing, docks, pallet areas and charging on top, add robots (built-in or your own robot types from any vendor),
  add traffic rules (one-way lanes, one-robot zones for intersections and doors, slow zones, waypoints) or let the
  traffic assistant suggest them, see problems live (unreachable stations, aisles too narrow for a robot ...), save versions and run a shift on it.
  Three example layouts and a sample floor plan are included. No code or config files to edit. Guide: `docs/USER_GUIDE.md`.
- **Missions** - multi-step jobs (load at A3-05, wait at a waypoint, unload at packing ...) for any robot, a robot type
  or one named robot, with priority (low / normal / high / urgent), run once, N times or until stopped, every N minutes.
- **History** - every shift is recorded in the database: replay it on the 2D plan with a timeline, and read its events,
  alarms (who acknowledged them) and orders at any moment.
- **Alarms** (bell in the header) - robot faults, critical batteries, vendor outages, robots stuck in traffic, late
  orders, charger shortage, collisions; acknowledge one or all; full history.
- **Login** - operator and admin roles, users and audit log (who did what) for admins.
- **Overview** - the client view: every vendor's fleet at a glance, how TusCore runs the site live (orders in →
  allocation → reserved routes → vendor protocols → recovery, with live counters), what needs attention now, and the
  proven value from the comparison.
- **Live** - 3D digital twin (orbit, zoom, follow a robot, auto-orbit) or 2D plan; KPIs (orders/hour, wait, cycle,
  utilization, energy); safety strip (collisions, deadlocks, faults); robot cards with *inject fault*; order center
  with manual order form; vendor panel with online/offline and *simulate outage*; event log; live protocol inspector.
- **Analytics** - throughput & queue, utilization per robot type, battery & faults over the shift, per-robot table,
  CSV export and a printable shift report.
- **Compare** - Scenario A (a separate fleet manager per vendor) vs B (one TusCore), 10 full shifts each with
  identical orders and faults.
- **What-if** - change robot counts, order volume, chargers or layout and see the KPI impact plus an ROI / payback estimate.
- **API** - every endpoint, the **WMS connection** (API key, webhooks, live WMS order list, built-in mock WMS that
  releases order waves), MQTT status and how to add a vendor. WMS guide: `docs/API.md`;
  stand-alone mock WMS over HTTP: `python backend/scripts/mock_wms.py --key <key>`.

Operator controls on the twin: **Pause fleet** (every robot holds position), **Maintenance** (close / re-open an aisle -
traffic reroutes, orders there wait), **Heatmap** (where robots lose time waiting), **Send to charger** on a robot card.
In 3D you can watch real work: cartons leave their shelf slot into the tote bin, ride the packing conveyor; the scissor lift
takes cartons from the top level and hands them to a tote robot; pallets wait at their pickup spot, ride the forks and
stack at the dock until the truck takes them.

Click the mode pill in the header to switch the *live* twin between one TusCore manager and separate vendor managers.

## Headline result (busy day, 10 × 8 h shifts per scenario, same seeds)
| KPI | A · separate managers | B · TusCore | Change |
|---|---|---|---|
| Average order cycle time | 219 s | 170 s | **−22 %** |
| Average wait before a robot starts | 104 s | 87 s | **−16 %** |
| Robot time lost to traffic | 214 min | 44 min | **−79 %** |
| Operator interventions per shift | 46 | 0 | **−100 %** |
| Collisions / permanent deadlocks | 0 / 0 | 0 / 0 | - |

Throughput is equal at this volume (demand-limited). All results are from the simulation; the modelling assumptions
for scenario A are in `config/warehouse.yaml` (`separate_managers`) and shown on the Compare page.
More result files: `results/`.

## Project layout
```
config/            warehouse layouts (YAML) - a new layout needs no code change
  warehouse.yaml          60 x 40 demo warehouse, 12 robots
  warehouse_large.yaml    80 x 50 regional DC, 19 robots
  warehouse_xl.yaml       100 x 64 mega DC, 40 robots (scale demo)
backend/app/
  sim.py           simulation core + fleet manager (task execution, traffic, charging, faults, KPIs)
  allocator.py     greedy and Hungarian task allocation
  planner.py       A* with lane discipline
  traffic.py       cell reservation + deadlock (wait-for cycle) detection
  orders.py tasks.py   order generator (Poisson) and multi-robot task model
  adapters/        one adapter per vendor protocol (VDA 5050, REST, TCP/JSON, MAVLink-style)
  experiments.py   scenario comparison and what-if runs (parallel, all CPU cores)
  mqtt_bridge.py   publishes VDA 5050 traffic to a real MQTT broker when MQTT_HOST is set
  main.py          FastAPI REST + WebSocket, serves the console
backend/tests/     python -m pytest -q
backend/scripts/run_shift.py   headless 8 h shift in ~6 s
frontend/          TusCore Fleet Console (Three.js 3D twin, Chart.js analytics - bundled, works offline)
docs/              TusCore_Technical_Report.pdf, STEPS.md (build log), REQUIREMENTS.md (brief vs implementation), REAL_WORLD.md (live-site architecture)
```

## Tests
```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```
