# Brief vs implementation - checklist

Status as of v1.1. "Verified" = checked by an automated test, a headless full-shift run, or in the browser.

## Scenario
| Requirement | Where | Status |
|---|---|---|
| 60 × 40 grid with high-bay shelves, pallet area, 2 packing stations, loading dock, charging area | `config/warehouse.yaml` | Done |
| Orders arrive continuously over a simulated 8-hour shift | `orders.py` (Poisson per order type, fixed seed) | Verified |
| 6 tote AMR (VDA 5050/MQTT, 1.0 c/s), 3 scissor lifts (REST, 0.7), 2 pallet stackers (TCP/JSON, 0.5), 1 drone (MAVLink-style, 2.0) | YAML `robot_types` | Done |
| Some orders need two robot types in sequence | high-shelf pick = lift + tote with synchronized hand-over | Verified |

## Weeks 1-2 - the twin comes alive
| Requirement | Where | Status |
|---|---|---|
| Python simulation core: grid, robots with type/speed/battery/state | `sim.py`, `robots.py`, `grid.py` | Done |
| A* path planning | `planner.py` (+ lane discipline) | Verified (test) |
| Web view showing robots moving live | Console: 3D twin + 2D plan | Verified (browser) |
| Layout from a config file, new layouts without code changes | `config/*.yaml`; second layout `warehouse_large.yaml` | Verified (test, 80×50 runs) |
| Done check: all 12 robots drive to targets you click | 2D plan: click robot, click floor (or nearest idle robot) | Verified |

## Weeks 3-4 - the right robot for the right job
| Requirement | Where | Status |
|---|---|---|
| Order generator: box picks, high-shelf picks, pallet moves, inventory scans | `orders.py` | Verified |
| Capability-based allocation, greedy (nearest free robot) | `allocator.py` `Greedy` | Done |
| Improved method (Hungarian or auction) | `allocator.py` `Hungarian` (scipy, with aging) | Done |
| Multi-step: lift picks high and hands over to a tote AMR | sync action `handoff` | Verified (3D view + test) |
| Done check: 8-hour shift runs automatically, every order type completed | headless runs: all orders completed | Verified |

## Weeks 5-6 - sharing the floor safely
| Requirement | Where | Status |
|---|---|---|
| Cell reservation | `traffic.py`, `sim._move` (claim before entering) | Verified: 0 collisions in every run |
| Deadlock detection and recovery | wait-for graph cycles incl. hand-over waits; yield → evade → replan → preempt; watchdog | Verified: 0 permanent deadlocks in all stress runs |
| Priority rules (pallets first in narrow aisles) | robot `priority` (pallet 3 > lift 2 > tote 1) | Done |
| Battery and charging scheduling (not all at once) | staggered policy: critical / low / opportunistic, max half a type charging | Done |
| Robot faults: breakdown mid-task, job reassigned | random (MTBF) + console button; order re-queued, partner released | Verified (test) |
| Vendor adapter layer: common model + one adapter per robot type | `adapters/` (4 protocols, round-trip encode/decode) | Verified (test) |
| Adding a vendor = one adapter | subclass + registry line + `adapter:` in YAML | Done |
| Done check: full shift, 0 collisions, 0 permanent deadlocks, fault recovery | stress: 12+ full shifts, ~14 faults each | Verified |

## Weeks 7-8 - dashboard, comparison, handover
| Requirement | Where | Status |
|---|---|---|
| Live dashboard: fleet map, robot cards, task queue | Live page | Verified (browser) |
| KPIs: orders/hour, wait time, utilization %, energy | KPI cards, Analytics page, report | Verified |
| Scenario comparison A (separate managers) vs B (TusCore), 10 runs each, averages | Compare page, `experiments.py`, `sim.mode="separate"` | Verified (10 × 2 × 8 h in ~60-90 s) |
| What-if tool: robot counts or layout → KPI impact | What-if page (+ ROI) | Verified (browser) |
| Small REST API to submit orders and read fleet status | `/api/orders`, `/api/fleet`, … ; docs at `/docs` | Verified (test) |
| Customer-ready demo | console + printable shift report | Done |
| Scale beyond the brief | `warehouse_xl.yaml`: 40 robots, ~4,150 orders per 8 h shift, 0 collisions | Verified (2 shifts per scenario) |
| 3-5 minute demo video | to record (suggested script in STEPS.md) | **To do (you)** |
| Technical report | `docs/TusCore_Technical_Report.pdf` (also a shareable online doc) | Done |

## TusCore interface
| Requirement | Status |
|---|---|
| TusCore logo top-left, "Powered by Tusmec" footer | Done - **placeholder marks in `frontend/assets/`, replace with the official logos from kickoff** |
| Navy #0A1A3C, cyan #2E9FE8, dark theme by default | Done |
| Vendor robots in own colour and icon, click robot to see its card | Done (3D models + vendor markers, 2D shapes) |
| Robot card: type, vendor, interface, state, battery, current task | Done |
| Task center: queue, assignments, progress, manual order form | Done |
| Vendor panel: adapters online/offline, four vendors under one TusCore | Done |
| KPI and comparison views | Done |

## Stack and rules
| Requirement | Status |
|---|---|
| Python + FastAPI backend | Done |
| Web frontend (plain JS + canvas / WebGL) | Done |
| MQTT broker (Mosquitto) | Done - `docker-compose.yml`; bridge verified against a live broker |
| Docker, one command | Done - `docker compose up --build` (compose validated; image build not tested in the dev sandbox because Docker Hub was blocked there - please run once) |
| Git repo with README | Done |

## Optional stretch goals
| Goal | Status |
|---|---|
| ROS 2 / Gazebo bridge for one robot | Not started (suggested next step) |
| Natural-language order entry (LLM) | Not started |
| MOV.AI maturity model level | Not assessed yet |
