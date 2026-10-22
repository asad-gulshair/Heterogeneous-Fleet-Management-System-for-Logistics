"""FastAPI app: REST + WebSocket + serves the frontend.

All endpoints are `async def` so they run on the same event loop as the simulation tick;
this means the simulation is never modified from two threads at once.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .allocator import ALLOCATORS
from .orders import KINDS
from .sim import Simulation

ROOT = Path(__file__).resolve().parents[2]            # .../tuscore
CONFIG = os.environ.get("TUSCORE_CONFIG", str(ROOT / "config" / "warehouse.yaml"))
sim = Simulation(CONFIG)
TICK = 0.1  # real seconds between simulation updates (10 Hz)


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
    version="0.3",
    description="Heterogeneous fleet manager / logistics digital twin. Submit orders, read fleet status, control the simulation.",
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


# ---------------------------------------------------------------- read
@app.get("/api/map", tags=["fleet"])
async def get_map():
    return {"name": sim.cfg.get("name"), **sim.grid.to_dict(), "types": sim.types_meta(), "kinds": KINDS}


@app.get("/api/fleet", tags=["fleet"])
async def get_fleet():
    """Full live snapshot: robots, queued/active orders, recent orders, KPIs."""
    return sim.snapshot()


@app.get("/api/robots", tags=["fleet"])
async def get_robots():
    return {"t": round(sim.sim_time, 1), "robots": [r.to_dict() for r in sim.robots.values()]}


@app.get("/api/kpis", tags=["fleet"])
async def get_kpis():
    return sim.kpis()


@app.get("/api/vendors", tags=["vendors"])
async def get_vendors():
    """Connected vendor adapters with online status and message counters."""
    return sim.vendors()


@app.get("/api/messages", tags=["vendors"])
async def get_messages(limit: int = 50, vendor: Optional[str] = None):
    """Recent wire messages (protocol inspector): VDA 5050, REST, TCP/JSON, MAVLink-style."""
    return sim.bus.recent(min(limit, 300), vendor)


@app.get("/api/events", tags=["fleet"])
async def get_events(limit: int = 50):
    return sim.events.recent(min(limit, 300))


@app.get("/api/orders", tags=["orders"])
async def list_orders():
    s = sim.snapshot()
    return {"open": s["tasks"], "recent": s["recent"]}


# ---------------------------------------------------------------- write
@app.post("/api/orders", tags=["orders"], status_code=201)
async def create_order(body: OrderIn):
    """Submit an order (used by the console form and by other software)."""
    t = sim.add_order(body.kind, body.row, body.bay, manual=True)
    return t.to_dict(sim.sim_time)


@app.post("/api/robots/{robot_id}/goto", tags=["fleet"])
async def goto(robot_id: str, body: Goto):
    ok, msg = sim.goto(robot_id, body.x, body.y)
    return {"ok": ok, "message": msg}


@app.post("/api/robots/{robot_id}/fault", tags=["fleet"])
async def inject_fault(robot_id: str):
    """Break a robot (demo / testing). Its order is returned to the queue and reassigned."""
    if not sim.inject_fault(robot_id, "injected from console"):
        raise HTTPException(404, "unknown robot or already faulted")
    return {"ok": True}


@app.post("/api/robots/{robot_id}/repair", tags=["fleet"])
async def repair(robot_id: str):
    if not sim.repair_robot(robot_id):
        raise HTTPException(404, "unknown robot or not faulted")
    return {"ok": True}


@app.post("/api/vendors/{vendor_id}/online", tags=["vendors"])
async def vendor_online(vendor_id: str, body: ToggleIn):
    """Simulate a vendor outage / reconnect. While offline no NEW missions are sent to that vendor's robots."""
    if not sim.set_vendor_online(vendor_id, body.enabled):
        raise HTTPException(404, "unknown vendor")
    return {"ok": True}


@app.post("/api/sim/speed", tags=["simulation"])
async def set_speed(body: Scale):
    sim.time_scale = body.scale
    return {"scale": sim.time_scale}


@app.post("/api/sim/allocator", tags=["simulation"])
async def set_allocator(body: AllocatorIn):
    sim.allocator = ALLOCATORS[body.name]()
    return {"allocator": sim.allocator.name}


@app.post("/api/sim/auto-orders", tags=["simulation"])
async def set_auto_orders(body: ToggleIn):
    sim.auto_orders = body.enabled
    return {"auto_orders": sim.auto_orders}


@app.post("/api/sim/reset", tags=["simulation"])
async def reset(body: ResetIn):
    sim.reset(body.seed, body.allocator)
    return {"ok": True, "seed": sim.seed, "allocator": sim.allocator.name}


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
