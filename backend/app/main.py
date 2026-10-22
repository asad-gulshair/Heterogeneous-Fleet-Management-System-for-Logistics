"""FastAPI app: REST + WebSocket + serves the TusCore Fleet Console.

All endpoints are `async def` so they run on the same event loop as the simulation tick;
the live simulation is therefore never modified from two threads at once. Heavy experiment
runs (comparison, what-if) use separate worker processes and never touch the live simulation.
"""
import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .allocator import ALLOCATORS
from .experiments import JobManager
from .mqtt_bridge import MqttBridge
from .orders import KINDS
from .sim import Simulation

VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[2]            # .../tuscore
CONFIG_DIR = ROOT / "config"
CONFIG = Path(os.environ.get("TUSCORE_CONFIG", str(CONFIG_DIR / "warehouse.yaml")))
TICK = 0.1  # real seconds between simulation updates (10 Hz)

mqtt = MqttBridge()
jobs = JobManager(ROOT / "results")
sim = Simulation(str(CONFIG))


def _wire(s: Simulation) -> Simulation:
    """Attach the live simulation to the MQTT bridge."""
    s.publisher = mqtt.publish if mqtt.host else None
    s.bus.publisher = s.publisher
    return s


_wire(sim)


async def sim_loop():
    while True:
        sim.tick(TICK)
        await asyncio.sleep(TICK)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(sim_loop())
    yield
    task.cancel()


app = FastAPI(
    title="TusCore Fleet Manager API",
    version=VERSION,
    description=("TusCore by Tusmec - one fleet manager for heterogeneous robot fleets. "
                 "Submit orders, read fleet status and KPIs, control the digital twin, "
                 "and run scenario comparisons and what-if analyses."),
    lifespan=lifespan,
)


# ---------------------------------------------------------------- models
class Goto(BaseModel):
    x: int
    y: int


class Scale(BaseModel):
    scale: float = Field(ge=0.1, le=300)


class OrderIn(BaseModel):
    kind: Literal["box_pick", "high_pick", "pallet_move", "inventory_scan"]
    row: Optional[int] = Field(None, ge=0, description="Shelf row index (0 = A1). Random if omitted.")
    bay: Optional[int] = Field(None, ge=0, description="Bay number along the row (0-based). Random if omitted.")


class AllocatorIn(BaseModel):
    name: Literal["greedy", "hungarian"]


class ToggleIn(BaseModel):
    enabled: bool


class ResetIn(BaseModel):
    seed: Optional[int] = None
    allocator: Optional[Literal["greedy", "hungarian"]] = None


class ModeIn(BaseModel):
    mode: Literal["tuscore", "separate"]


class LayoutIn(BaseModel):
    name: str = Field(description="File name from GET /api/layouts, e.g. warehouse.yaml")


class CompareIn(BaseModel):
    runs: int = Field(10, ge=1, le=30, description="Shifts per scenario (the brief asks for 10)")
    shift_hours: float = Field(8, ge=0.5, le=12)
    load_factor: float = Field(1.0, ge=0.2, le=2.0, description="Multiplies every order rate (1.3 = peak season)")
    layout: Optional[str] = None


class WhatIfIn(BaseModel):
    robot_counts: Dict[str, int] = Field(default_factory=dict, description="e.g. {\"tote_amr\": 8}")
    order_rates: Dict[str, float] = Field(default_factory=dict, description="orders per hour per kind")
    load_factor: float = Field(1.0, ge=0.2, le=2.0)
    charging_bays: Optional[int] = Field(None, ge=1, le=20)
    layout: Optional[str] = None
    runs: int = Field(3, ge=1, le=10)
    shift_hours: float = Field(8, ge=0.5, le=12)


# ---------------------------------------------------------------- helpers
def _layout_path(name: Optional[str]) -> Path:
    if not name:
        return Path(sim.config_path)
    p = (CONFIG_DIR / name).resolve()
    if p.parent != CONFIG_DIR.resolve() or p.suffix not in (".yaml", ".yml") or not p.exists():
        raise HTTPException(404, f"unknown layout {name}")
    return p


def _new_sim(path: Path, mode: str) -> Simulation:
    global sim
    scale, auto = sim.time_scale, sim.auto_orders
    sim = _wire(Simulation(str(path), allocator=sim.allocator.name, mode=mode))
    sim.time_scale, sim.auto_orders = scale, auto
    return sim


# ---------------------------------------------------------------- read
@app.get("/api/system", tags=["system"], summary="System status (version, MQTT, mode)")
async def system():
    """Version, live mode/layout, MQTT broker status, CPU cores used for experiments."""
    return {"version": VERSION, "mode": sim.mode, "layout": Path(sim.config_path).name, "site": sim.cfg.get("name"),
            "mqtt": mqtt.status(), "experiment_workers": jobs.workers}


@app.get("/api/map", tags=["fleet"], summary="Warehouse map and robot types")
async def get_map():
    return {"name": sim.cfg.get("name"), **sim.grid.to_dict(), "types": sim.types_meta(), "kinds": KINDS,
            "layout": Path(sim.config_path).name, "mode": sim.mode}


@app.get("/api/fleet", tags=["fleet"], summary="Full live fleet snapshot")
async def get_fleet():
    """Full live snapshot: robots, queued/active orders, recent orders, KPIs, vendors, events, messages."""
    return sim.snapshot()


@app.get("/api/robots", tags=["fleet"], summary="All robots - position, state, battery")
async def get_robots():
    return {"t": round(sim.sim_time, 1), "robots": [r.to_dict() for r in sim.robots.values()]}


@app.get("/api/kpis", tags=["fleet"], summary="Live KPIs")
async def get_kpis():
    return sim.kpis()


@app.get("/api/history", tags=["analytics"], summary="KPI time series (per minute)")
async def get_history():
    """One KPI sample per simulated minute (throughput, queue, utilization by type, battery, energy)."""
    return list(sim.history)


@app.get("/api/report", tags=["analytics"], summary="Shift report (JSON)")
async def get_report():
    """Shift report: fleet KPIs, every robot, every vendor."""
    return sim.report()


@app.get("/api/report.csv", tags=["analytics"], response_class=PlainTextResponse, summary="Shift report (CSV download)")
async def get_report_csv():
    rep = sim.report()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["TusCore shift report", rep["site"], f"mode={rep['mode']}", f"allocator={rep['allocator']}",
                f"sim_hours={rep['sim_time_h']}"])
    w.writerow([])
    w.writerow(["KPI", "value"])
    for k, v in rep["kpis"].items():
        if not isinstance(v, dict):
            w.writerow([k, v])
    w.writerow([])
    cols = ["id", "type", "vendor", "interface", "busy_pct", "distance_cells", "energy_kwh", "battery_pct", "faults", "state"]
    w.writerow(cols)
    for r in rep["robots"]:
        w.writerow([r[c] for c in cols])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=tuscore_shift_report.csv"})


@app.get("/api/vendors", tags=["vendors"], summary="Vendor adapters and status")
async def get_vendors():
    """Connected vendor adapters with online status and message counters."""
    return sim.vendors()


@app.get("/api/messages", tags=["vendors"], summary="Protocol inspector - recent wire messages")
async def get_messages(limit: int = 50, vendor: Optional[str] = None):
    """Recent wire messages (protocol inspector): VDA 5050, REST, TCP/JSON, MAVLink-style."""
    return sim.bus.recent(min(limit, 300), vendor)


@app.get("/api/events", tags=["fleet"], summary="Event log")
async def get_events(limit: int = 50):
    return sim.events.recent(min(limit, 300))


@app.get("/api/orders", tags=["orders"], summary="Open and recent orders")
async def list_orders():
    s = sim.snapshot()
    return {"open": s["tasks"], "recent": s["recent"]}


@app.get("/api/layouts", tags=["simulation"], summary="Available warehouse layouts")
async def layouts():
    """Warehouse layouts available in the config folder (new layout = new YAML file, no code)."""
    import yaml
    out = []
    for p in sorted(CONFIG_DIR.glob("*.yaml")):
        c = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append({"file": p.name, "name": c.get("name"), "width": c["width"], "height": c["height"],
                    "robots": sum(t["count"] for t in c["robot_types"].values()),
                    "active": p.resolve() == Path(sim.config_path).resolve()})
    return out


# ---------------------------------------------------------------- write
@app.post("/api/orders", tags=["orders"], status_code=201, summary="Submit an order")
async def create_order(body: OrderIn):
    """Submit an order (used by the console form and by other software, e.g. a WMS)."""
    t = sim.add_order(body.kind, body.row, body.bay, manual=True)
    return t.to_dict(sim.sim_time)


@app.post("/api/robots/{robot_id}/goto", tags=["fleet"], summary="Send a robot to a cell")
async def goto(robot_id: str, body: Goto):
    ok, msg = sim.goto(robot_id, body.x, body.y)
    return {"ok": ok, "message": msg}


@app.post("/api/robots/{robot_id}/fault", tags=["fleet"], summary="Inject a robot fault")
async def inject_fault(robot_id: str):
    """Break a robot (demo / testing). Its order is returned to the queue and reassigned."""
    if not sim.inject_fault(robot_id, "injected from console"):
        raise HTTPException(404, "unknown robot or already faulted")
    return {"ok": True}


@app.post("/api/robots/{robot_id}/repair", tags=["fleet"], summary="Repair a robot")
async def repair(robot_id: str):
    if not sim.repair_robot(robot_id):
        raise HTTPException(404, "unknown robot or not faulted")
    return {"ok": True}


@app.post("/api/vendors/{vendor_id}/online", tags=["vendors"], summary="Simulate vendor outage / reconnect")
async def vendor_online(vendor_id: str, body: ToggleIn):
    """Simulate a vendor outage / reconnect. While offline no NEW missions are sent to that vendor's robots."""
    if not sim.set_vendor_online(vendor_id, body.enabled):
        raise HTTPException(404, "unknown vendor")
    return {"ok": True}


@app.post("/api/sim/speed", tags=["simulation"], summary="Simulation speed")
async def set_speed(body: Scale):
    sim.time_scale = body.scale
    return {"scale": sim.time_scale}


@app.post("/api/sim/allocator", tags=["simulation"], summary="Choose allocation method")
async def set_allocator(body: AllocatorIn):
    sim.allocator = ALLOCATORS[body.name]()
    return {"allocator": sim.allocator.name}


@app.post("/api/sim/auto-orders", tags=["simulation"], summary="Automatic order stream on/off")
async def set_auto_orders(body: ToggleIn):
    sim.auto_orders = body.enabled
    return {"auto_orders": sim.auto_orders}


@app.post("/api/sim/reset", tags=["simulation"], summary="Restart the shift")
async def reset(body: ResetIn):
    sim.reset(body.seed, body.allocator)
    return {"ok": True, "seed": sim.seed, "allocator": sim.allocator.name}


@app.post("/api/sim/mode", tags=["simulation"], summary="One TusCore manager vs separate managers")
async def set_mode(body: ModeIn):
    """Restart the live twin as ONE TusCore manager ('tuscore') or one manager per vendor ('separate')."""
    _new_sim(Path(sim.config_path), body.mode)
    return {"mode": sim.mode}


@app.post("/api/sim/layout", tags=["simulation"], summary="Load another layout")
async def set_layout(body: LayoutIn):
    """Load another warehouse layout into the live twin."""
    _new_sim(_layout_path(body.name), sim.mode)
    return {"layout": Path(sim.config_path).name, "site": sim.cfg.get("name")}


# ---------------------------------------------------------------- experiments
@app.post("/api/experiments/compare", tags=["experiments"], status_code=202, summary="Start scenario comparison (A vs B)")
async def start_compare(body: CompareIn):
    """Scenario A (separate fleet manager per vendor) vs B (all coordinated by TusCore).
    Each scenario runs `runs` full shifts with the same seeds; returns a job id to poll."""
    path = str(_layout_path(body.layout))
    ov = {"shift_hours": body.shift_hours, "load_factor": body.load_factor}
    seeds = range(1, body.runs + 1)
    runs = [(path, "A", "separate", s, ov) for s in seeds] + [(path, "B", "tuscore", s, ov) for s in seeds]
    meta = {"labels": ["A", "B"], "names": {"A": "Separate manager per vendor", "B": "TusCore (one manager)"},
            "runs": body.runs, "shift_hours": body.shift_hours, "load_factor": body.load_factor,
            "layout": Path(path).name, "assumptions": sim.cfg.get("separate_managers", {})}
    return {"job": jobs.start("comparison", runs, meta)}


@app.post("/api/experiments/whatif", tags=["experiments"], status_code=202, summary="Start what-if analysis")
async def start_whatif(body: WhatIfIn):
    """Baseline vs your scenario (robot counts, order rates, chargers, layout), both run by TusCore."""
    base_path = str(Path(sim.config_path))
    scen_path = str(_layout_path(body.layout)) if body.layout else base_path
    common = {"shift_hours": body.shift_hours, "load_factor": body.load_factor}
    scen = dict(common, robot_counts=body.robot_counts, order_rates=body.order_rates, charging_bays=body.charging_bays)
    seeds = range(1, body.runs + 1)
    runs = [(base_path, "baseline", "tuscore", s, common) for s in seeds] + \
           [(scen_path, "scenario", "tuscore", s, scen) for s in seeds]
    meta = {"labels": ["baseline", "scenario"], "names": {"baseline": "Current setup", "scenario": "What-if scenario"},
            "runs": body.runs, "shift_hours": body.shift_hours, "load_factor": body.load_factor,
            "layout": Path(scen_path).name, "changes": body.model_dump()}
    return {"job": jobs.start("whatif", runs, meta)}


@app.get("/api/experiments/latest/{kind}", tags=["experiments"], summary="Latest experiment result")
async def latest(kind: Literal["comparison", "whatif"]):
    """Most recent finished result (null if none has been run yet)."""
    return jobs.latest(kind)


@app.get("/api/experiments/{job_id}", tags=["experiments"], summary="Experiment job progress")
async def job_status(job_id: str):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(sim.snapshot())
            await asyncio.sleep(TICK)
    except (WebSocketDisconnect, RuntimeError):
        pass


# Frontend last, so /api and /ws take priority.
app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")
