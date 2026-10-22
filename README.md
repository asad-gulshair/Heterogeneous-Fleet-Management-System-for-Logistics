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
Watch the VDA 5050 traffic on the broker: `mosquitto_sub -h localhost -t "uagv/#" -v`

**Manual:**
```powershell
cd backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --reload-dir app --reload-dir ../config
```

## What the console shows
- **Live** - 3D digital twin (orbit, zoom, follow a robot, auto-orbit) or 2D plan; KPIs (orders/hour, wait, cycle,
  utilization, energy); safety strip (collisions, deadlocks, faults); robot cards with *inject fault*; order center
  with manual order form; vendor panel with online/offline and *simulate outage*; event log; live protocol inspector.
- **Analytics** - throughput & queue, utilization per robot type, battery & faults over the shift, per-robot table,
  CSV export and a printable shift report.
- **Compare** - Scenario A (a separate fleet manager per vendor) vs B (one TusCore), 10 full shifts each with
  identical orders and faults.
- **What-if** - change robot counts, order volume, chargers or layout and see the KPI impact plus an ROI / payback estimate.
- **API** - every endpoint, MQTT status, how to integrate a WMS and how to add a vendor.

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
docs/              STEPS.md (build log and design choices), REQUIREMENTS.md (brief vs implementation)
```

## Tests
```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```
