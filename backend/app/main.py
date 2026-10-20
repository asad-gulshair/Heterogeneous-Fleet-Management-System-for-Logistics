"""FastAPI app: REST + WebSocket + serves the frontend."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


app = FastAPI(title="TusCore Fleet Manager", version="0.1", lifespan=lifespan)


class Goto(BaseModel):
    x: int
    y: int


class Scale(BaseModel):
    scale: float


@app.get("/api/map")
def get_map():
    return {"name": sim.cfg.get("name"), **sim.grid.to_dict(), "types": sim.types_meta()}


@app.get("/api/robots")
def get_robots():
    return sim.snapshot()


@app.post("/api/robots/{robot_id}/goto")
def goto(robot_id: str, body: Goto):
    ok, msg = sim.goto(robot_id, body.x, body.y)
    return {"ok": ok, "message": msg}


@app.post("/api/demo/random")
def demo_random():
    sim.random_targets()
    return {"ok": True}


@app.post("/api/sim/speed")
def set_speed(body: Scale):
    sim.time_scale = max(0.1, min(body.scale, 60.0))
    return {"scale": sim.time_scale}


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
