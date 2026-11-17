"""FastAPI app: REST + WebSocket + serves the TusCore Fleet Console.

All endpoints are `async def` so they run on the same event loop as the simulation tick;
the live simulation is therefore never modified from two threads at once. Heavy experiment
runs (comparison, what-if) use separate worker processes and never touch the live simulation.
"""
import asyncio
import csv
import io
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth as auth_mod
from . import designs
from . import layout as layout_mod
from .alarms import AlarmManager
from .missions import MissionError, MissionManager
from .recorder import Recorder
from .store import Store
from .wms import MockWms, WmsError, WmsGateway
from .allocator import ALLOCATORS
from .experiments import JobManager
from .mqtt_bridge import MqttBridge
from .orders import KINDS
from .sim import Simulation

VERSION = "2.0-dev"

# Windows can map .js to text/plain in its registry; browsers then refuse to run ES modules.
# Register the correct types explicitly so the console works on every machine.
for _ext, _type in ((".js", "application/javascript"), (".mjs", "application/javascript"), (".css", "text/css"),
                    (".svg", "image/svg+xml"), (".json", "application/json"), (".html", "text/html")):
    mimetypes.add_type(_type, _ext)
ROOT = Path(__file__).resolve().parents[2]            # .../tuscore
CONFIG_DIR = ROOT / "config"
CONFIG = Path(os.environ.get("TUSCORE_CONFIG", str(CONFIG_DIR / "warehouse.yaml")))
TICK = 0.1  # real seconds between simulation updates (10 Hz)

mqtt = MqttBridge()
jobs = JobManager(ROOT / "results")
STORE = Store(designs.DATA / "tuscore.db")
AUTH = auth_mod.Auth(STORE)
ALARMS = AlarmManager(STORE)
MISSIONS = MissionManager(STORE)
GATEWAY = WmsGateway(STORE)
MOCK_WMS = MockWms(GATEWAY)
RECORDER = Recorder(STORE)
ALARMS.sources.append(lambda s: [("mission_invalid", str(m["id"]), "minor", f"Mission '{m['name']}' cannot run", m["error"] or "")
                                 for m in MISSIONS.items.values() if m["status"] == "invalid"])
sim = Simulation(str(CONFIG))


def _map_dict(s: Simulation) -> dict:
    return {"name": s.cfg.get("name"), **s.grid.to_dict(), "types": s.types_meta(), "kinds": KINDS,
            "dwell_s": s.cfg["orders"]["dwell_s"], "sla_s": s.sla_s,
            "layout": Path(s.config_path).name, "mode": s.mode}


def _wire(s: Simulation) -> Simulation:
    """Attach a new live simulation to MQTT, the recorder (database), alarms, missions and the WMS gateway."""
    s.publisher = mqtt.publish if mqtt.host else None
    s.bus.publisher = s.publisher
    RECORDER.attach(s, Path(s.config_path).name, _map_dict(s))
    ALARMS.attach(s)
    MISSIONS.attach(s)
    GATEWAY.attach(s)
    MOCK_WMS.attach(s)
    return s


_wire(sim)


async def sim_loop():
    n, ended = 0, None
    while True:
        sim.tick(TICK)
        n += 1
        if n % 20 == 0:                                   # write the recording about every 2 s
            done = sim.shift_over()
            RECORDER.flush(final=done and ended is not sim)
            if done:
                ended = sim
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
    priority: int = Field(1, ge=0, le=3, description="0 low, 1 normal, 2 high, 3 urgent")


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old: str
    new: str


class UserIn(BaseModel):
    username: str
    password: str
    role: Literal["operator", "admin"] = "operator"


class UserPatch(BaseModel):
    role: Optional[Literal["operator", "admin"]] = None
    active: Optional[bool] = None
    password: Optional[str] = None


class MissionIn(BaseModel):
    """A multi-step mission. robot: {"role": "carry_tote"} | {"type": "tote_amr"} | {"robot": "tote_amr_3"}."""
    model_config = {"extra": "allow"}
    name: str = "Mission"
    robot: Dict[str, str] = Field(default_factory=lambda: {"role": "carry_tote"})
    steps: List[Dict[str, Any]] = Field(description='[{"to": "A3-05", "do": "load"}, {"to": "packing_1", "do": "unload"}]; '
                                                    'do: go | load | unload | lift | set | scan | wait (wait_s)')
    priority: Any = Field(1, description="low | normal | high | urgent or 0-3")
    repeat: int = Field(1, ge=0, le=10000, description="runs; 0 = until stopped")
    every_min: float = Field(0, ge=0, le=1440, description="minutes between run starts")
    start: bool = True


class WmsOrderIn(BaseModel):
    """One order line from a WMS."""
    model_config = {"extra": "allow"}
    ref: str = Field(description="Your order reference (unique). Sending the same ref again returns the first order.")
    type: Literal["box_pick", "high_pick", "pallet_move", "inventory_scan", "transport"]
    location: Optional[str] = Field(None, description="Rack bay (A3-05), rack (A3) for a scan, or from-place for transport / pallets")
    destination: Optional[str] = Field(None, description="Packing station, dock, waypoint or x,y")
    priority: Any = Field("normal", description="low | normal | high | urgent")
    load: Optional[Literal["tote", "pallet"]] = Field(None, description="transport only")


class WebhookIn(BaseModel):
    url: str


class MockIn(BaseModel):
    on: bool
    wave_min: Optional[float] = Field(None, ge=1, le=60)
    scale: Optional[float] = Field(None, ge=0.1, le=5)


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


class LayoutDoc(BaseModel):
    """A layout drawn in the designer (metres). Unknown fields are kept; values are snapped and clamped on the server."""
    model_config = {"extra": "allow"}
    name: str = "Untitled layout"
    width_m: float = Field(60, ge=1, le=500)
    height_m: float = Field(40, ge=1, le=500)
    elements: List[Dict[str, Any]] = Field(default_factory=list,
                                           description="{id, type: rack|wall|nogo|packing|dock|pallet_area|charging, x, y, w, h}")
    robots: Dict[str, int] = Field(default_factory=dict)
    orders: Dict[str, float] = Field(default_factory=dict)


class SaveIn(BaseModel):
    layout: LayoutDoc
    note: str = ""


class ApplyIn(BaseModel):
    version: Optional[int] = None


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
    if name.startswith("design:"):                      # a layout compiled from the designer
        p = (designs.COMPILED / (name[7:] + ".yaml")).resolve()
        if p.parent != designs.COMPILED.resolve() or not p.exists():
            raise HTTPException(404, f"unknown layout {name}")
        return p
    p = (CONFIG_DIR / name).resolve()
    if p.parent != CONFIG_DIR.resolve() or p.suffix not in (".yaml", ".yml") or not p.exists():
        raise HTTPException(404, f"unknown layout {name}")
    return p


def _new_sim(path: Path, mode: str, seed: Optional[int] = None, allocator: Optional[str] = None) -> Simulation:
    global sim
    scale, auto = sim.time_scale, sim.auto_orders
    new = Simulation(str(path), seed=seed, allocator=allocator or sim.allocator.name, mode=mode)
    new.time_scale, new.auto_orders = scale, auto
    sim = _wire(new)
    return sim


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or auth_mod.LOCAL


@app.middleware("http")
async def guard(request: Request, call_next):
    """Login and roles on every /api call; mutating calls are written to the audit log."""
    need = auth_mod.required(request.method, request.url.path)
    user = None
    if need and auth_mod.ENABLED:
        token = request.cookies.get(auth_mod.COOKIE)
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            token = h[7:]
        user = AUTH.user(token)
        if need == "wms" and user is None:
            if not AUTH.wms_ok(request.headers.get("x-api-key")):
                return JSONResponse({"detail": "API key (X-API-Key) or login required"}, status_code=401)
            user = {"id": -1, "username": "wms", "role": "wms", "must_change": False}
        elif user is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        elif need != "wms" and auth_mod.ROLES[user["role"]] < auth_mod.ROLES[need]:
            return JSONResponse({"detail": f"{need} only"}, status_code=403)
    request.state.user = user or auth_mod.LOCAL
    resp = await call_next(request)
    if (request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/") and resp.status_code < 400
            and not request.url.path.startswith(("/api/auth/", "/api/wms/orders", "/api/designs/check", "/api/designs/suggest"))):
        STORE.audit(request.state.user["username"], f"{request.method} {request.url.path}")
    return resp


# ---------------------------------------------------------------- read
@app.get("/api/system", tags=["system"], summary="System status (version, MQTT, mode)")
async def system():
    """Version, live mode/layout, MQTT broker status, CPU cores used for experiments."""
    return {"version": VERSION, "mode": sim.mode, "layout": Path(sim.config_path).name, "site": sim.cfg.get("name"),
            "mqtt": mqtt.status(), "experiment_workers": jobs.workers}


@app.get("/api/map", tags=["fleet"], summary="Warehouse map and robot types")
async def get_map():
    return _map_dict(sim)


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
    cur = Path(sim.config_path).resolve()
    if cur.parent == designs.COMPILED.resolve():         # the live twin runs a designed layout
        out.append({"file": "design:" + cur.stem, "name": sim.cfg.get("name"), "width": sim.cfg["width"],
                    "height": sim.cfg["height"], "robots": len(sim.robots), "active": True, "design": True})
    return out


# ---------------------------------------------------------------- layout designer
@app.get("/api/catalog", tags=["designer"], summary="Element types and robot catalog for the designer")
async def get_catalog():
    """Element types (rack, wall, no-go, packing, dock, pallet area, charging), robot types with footprint and price,
    the jobs a custom robot can do, the vendor protocols, and the saved custom robot library."""
    return layout_mod.catalog(designs.robot_library())


@app.get("/api/robot-types", tags=["designer"], summary="Saved custom robot types")
async def list_robot_types():
    return designs.robot_library()


@app.put("/api/robot-types/{key}", tags=["designer"], summary="Save a custom robot type to the library")
async def put_robot_type(key: str, body: Dict[str, Any]):
    """{label, role: carry_tote|high_pick|move_pallet|scan, vendor, adapter, width_m, length_m, speed_mps, battery_kwh,
    cost_eur, color}. The robot is planned exactly like the built-in robot that does the same job."""
    if not layout_mod.CUSTOM_KEY.match(key):
        raise HTTPException(400, "key must look like c_my_robot")
    d = layout_mod.clean_robot(body)
    if not d:
        raise HTTPException(422, "role must be one of " + ", ".join(layout_mod.ROLES))
    designs.save_robot(key, d)
    return {"key": key, "robot": d}


@app.delete("/api/robot-types/{key}", tags=["designer"], summary="Remove a custom robot type from the library")
async def delete_robot_type(key: str):
    """Layouts that already use it keep their own copy, so their saved versions still run."""
    return designs.delete_robot(key)


@app.post("/api/backgrounds", tags=["designer"], status_code=201, summary="Upload a floor plan (image or PDF)")
async def upload_background(request: Request, name: str = "", page: int = 1):
    """Send the file as the raw request body. PNG, JPG, WEBP, BMP, TIFF, GIF or PDF (one page is rendered at 150 dpi).
    Returns {id, w_px, h_px, pages, page, source}; put it in layout.background and set the scale in the designer."""
    data = await request.body()
    try:
        return designs.save_background(data, name, page)
    except designs.UploadError as e:
        raise HTTPException(415, str(e))


@app.post("/api/backgrounds/pdf/{source}/page/{page}", tags=["designer"], summary="Use another page of an uploaded PDF")
async def background_page(source: str, page: int, name: str = ""):
    try:
        return designs.background_page(source, page, name)
    except KeyError:
        raise HTTPException(404, "unknown PDF")
    except designs.UploadError as e:
        raise HTTPException(422, str(e))


@app.get("/api/backgrounds/{bid}", tags=["designer"], summary="Floor plan image")
async def get_background(bid: str):
    try:
        return FileResponse(designs.background_file(bid), headers={"Cache-Control": "public, max-age=31536000, immutable"})
    except KeyError:
        raise HTTPException(404, "unknown floor plan")


@app.get("/api/designs", tags=["designer"], summary="Saved layouts and example templates")
async def list_designs():
    return designs.list_designs()


@app.get("/api/designs/{did}", tags=["designer"], summary="One layout (latest or a given version)")
async def get_design(did: str, version: Optional[int] = None):
    try:
        return designs.get(did, version)
    except KeyError:
        raise HTTPException(404, f"unknown design {did}")


@app.get("/api/designs/{did}/versions", tags=["designer"], summary="Version history of a layout")
async def design_versions(did: str):
    try:
        return designs.versions(did)
    except KeyError:
        raise HTTPException(404, f"unknown design {did}")


@app.post("/api/designs", tags=["designer"], status_code=201, summary="Save a new layout")
async def create_design(body: SaveIn):
    L = layout_mod.normalise(body.layout.model_dump())
    return designs.save(L, None, body.note)


@app.put("/api/designs/{did}", tags=["designer"], summary="Save a new version of a layout")
async def update_design(did: str, body: SaveIn):
    """Every save is a new version. Saving an example template creates your own copy."""
    L = layout_mod.normalise(body.layout.model_dump())
    try:
        return designs.save(L, did, body.note)
    except KeyError:
        raise HTTPException(404, f"unknown design {did}")


@app.post("/api/designs/check", tags=["designer"], summary="Check a layout for problems")
async def check_design(body: LayoutDoc):
    """Errors (block a run) and warnings: unreachable stations, racks without a face, aisles too narrow for a robot ..."""
    rep = layout_mod.check_layout(body.model_dump())
    rep.pop("config", None)
    return rep


@app.post("/api/designs/suggest-traffic", tags=["designer"], summary="Suggest one-way lanes and door zones")
async def suggest_traffic(body: LayoutDoc):
    """Traffic assistant: 1 m aisles open at both ends become one-way lanes (neighbours in opposite directions),
    door gaps in walls become one-robot zones. Only suggestions that add no errors are returned; nothing is saved."""
    return layout_mod.suggest_traffic(body.model_dump())


@app.post("/api/designs/{did}/apply", tags=["designer"], summary="Run a shift on a saved layout")
async def apply_design(did: str, body: ApplyIn):
    """Compiles the layout to the simulation map and loads it into the live twin (the running shift is replaced)."""
    try:
        d = designs.get(did, body.version)
    except KeyError:
        raise HTTPException(404, f"unknown design {did}")
    cfg, rep = layout_mod.compile_layout(d["layout"])
    if not rep["ok"]:
        raise HTTPException(422, {"msg": "Fix the errors before running.", "problems": rep["problems"]})
    path = designs.write_compiled(did, d["version"], cfg)
    _new_sim(path, sim.mode)
    return {"layout": "design:" + path.stem, "site": sim.cfg.get("name"), "warnings": rep["warnings"]}


# ---------------------------------------------------------------- write
@app.post("/api/orders", tags=["orders"], status_code=201, summary="Submit an order")
async def create_order(body: OrderIn):
    """Submit an order (used by the console form and by other software, e.g. a WMS)."""
    t = sim.add_order(body.kind, body.row, body.bay, manual=True, priority=body.priority)
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


class AisleIn(BaseModel):
    closed: bool


@app.get("/api/heatmap", tags=["analytics"], summary="Congestion heatmap (where robots wait)")
async def heatmap():
    """Seconds robots spent waiting for a free cell, per cell, this shift."""
    return sim.heatmap()


@app.post("/api/fleet/pause", tags=["control"], summary="Pause / resume the whole fleet")
async def pause(body: ToggleIn):
    """Emergency-style pause: every robot holds position; orders keep queuing. enabled=true pauses."""
    sim.set_paused(body.enabled)
    return {"paused": sim.paused}


@app.post("/api/aisles/{row}", tags=["control"], summary="Close / re-open an aisle for maintenance")
async def aisle(row: int, body: AisleIn):
    """Closed aisles are avoided by route planning; orders located there wait until it re-opens."""
    if not sim.set_aisle(row, body.closed):
        raise HTTPException(404, "unknown aisle")
    return {"closed_rows": sorted(sim.closed_rows)}


@app.post("/api/robots/{robot_id}/charge", tags=["control"], summary="Send an idle robot to a charger")
async def charge(robot_id: str):
    if not sim.charge_now(robot_id):
        raise HTTPException(409, "robot is not idle or no charger is free")
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
    """Start a new shift on the same layout (a new recording in the database)."""
    _new_sim(Path(sim.config_path), sim.mode, body.seed, body.allocator)
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


# ---------------------------------------------------------------- login, users, audit
@app.post("/api/auth/login", tags=["auth"], summary="Log in")
async def login(body: LoginIn):
    """Sets an HttpOnly session cookie (also returned as `token` for scripts: Authorization: Bearer <token>)."""
    try:
        token, user = AUTH.login(body.username.strip(), body.password)
    except PermissionError as e:
        STORE.audit(body.username[:32], "login failed")
        raise HTTPException(401, str(e))
    STORE.audit(user["username"], "login")
    resp = JSONResponse({"user": user, "token": token})
    resp.set_cookie(auth_mod.COOKIE, token, max_age=auth_mod.SESSION_S, httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/logout", tags=["auth"], summary="Log out")
async def logout(request: Request):
    AUTH.logout(request.cookies.get(auth_mod.COOKIE))
    STORE.audit(_user(request)["username"], "logout")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth_mod.COOKIE)
    return resp


@app.get("/api/auth/me", tags=["auth"], summary="Who is logged in")
async def me(request: Request):
    if not auth_mod.ENABLED:
        return {"user": auth_mod.LOCAL, "auth": False}
    return {"user": AUTH.user(request.cookies.get(auth_mod.COOKIE)), "auth": True}     # user null = not logged in


@app.post("/api/auth/password", tags=["auth"], summary="Change my password")
async def change_password(body: PasswordIn, request: Request):
    try:
        AUTH.change_password(_user(request), body.old, body.new)
    except (ValueError, IndexError) as e:
        raise HTTPException(422, str(e))
    STORE.audit(_user(request)["username"], "password changed")
    return {"ok": True}


@app.get("/api/users", tags=["auth"], summary="Users (admin)")
async def list_users():
    return AUTH.users()


@app.post("/api/users", tags=["auth"], status_code=201, summary="Add a user (admin)")
async def add_user(body: UserIn, request: Request):
    try:
        u = AUTH.create_user(body.username.strip(), body.password, body.role)
    except ValueError as e:
        raise HTTPException(422, str(e))
    STORE.audit(_user(request)["username"], "user created", f"{u['username']} ({u['role']})")
    return u


@app.patch("/api/users/{uid}", tags=["auth"], summary="Change a user's role, switch off, reset password (admin)")
async def patch_user(uid: int, body: UserPatch, request: Request):
    try:
        AUTH.update_user(uid, body.role, body.active, body.password, by=_user(request))
    except KeyError:
        raise HTTPException(404, "unknown user")
    except ValueError as e:
        raise HTTPException(422, str(e))
    STORE.audit(_user(request)["username"], "user changed", f"#{uid} {body.model_dump(exclude_none=True, exclude={'password'})}"
                + (" password reset" if body.password else ""))
    return {"ok": True}


@app.get("/api/audit", tags=["auth"], summary="Audit log: who did what (admin)")
async def audit(limit: int = 200):
    return STORE.audit_log(min(1000, max(1, limit)))


# ---------------------------------------------------------------- alarms
@app.get("/api/alarms", tags=["alarms"], summary="Open alarms")
async def alarms():
    """Open alarms (active, or cleared but not yet acknowledged), most urgent first."""
    return {"alarms": ALARMS.list(), "summary": ALARMS.summary()}


@app.get("/api/alarms/history", tags=["alarms"], summary="Alarm history (all shifts)")
async def alarm_history(limit: int = 200):
    return STORE.alarm_history(min(2000, max(1, limit)))


@app.post("/api/alarms/ack-all", tags=["alarms"], summary="Acknowledge all alarms")
async def ack_all(request: Request):
    return {"acked": ALARMS.ack_all(_user(request)["username"])}


@app.post("/api/alarms/{aid}/ack", tags=["alarms"], summary="Acknowledge an alarm")
async def ack(aid: int, request: Request):
    if not ALARMS.ack(aid, _user(request)["username"]):
        raise HTTPException(404, "no open alarm with this id")
    return {"ok": True}


# ---------------------------------------------------------------- missions
@app.get("/api/missions", tags=["missions"], summary="Mission templates and their runs")
async def missions():
    return MISSIONS.list()


@app.get("/api/missions/targets", tags=["missions"], summary="Places a mission step can go to on this site")
async def mission_targets():
    return MISSIONS.targets.catalog()


@app.post("/api/missions", tags=["missions"], status_code=201, summary="Create (and start) a mission")
async def create_mission(body: MissionIn, request: Request):
    """Multi-step job with a priority, run once or repeated. Each run is one fleet order."""
    try:
        return MISSIONS.create(body.model_dump(), _user(request)["username"])
    except MissionError as e:
        raise HTTPException(422, str(e))


@app.post("/api/missions/{mid}/{action}", tags=["missions"], summary="pause | resume | stop | run_now")
async def mission_action(mid: int, action: Literal["pause", "resume", "stop", "run_now"]):
    if mid not in MISSIONS.items:
        raise HTTPException(404, "unknown mission")
    try:
        return MISSIONS.set_status(mid, action)
    except MissionError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/missions/{mid}", tags=["missions"], summary="Delete a mission (cancels an open run)")
async def delete_mission(mid: int):
    if mid not in MISSIONS.items:
        raise HTTPException(404, "unknown mission")
    MISSIONS.delete(mid)
    return {"ok": True}


# ---------------------------------------------------------------- WMS
@app.post("/api/wms/orders", tags=["wms"], status_code=201, summary="WMS: submit an order line")
async def wms_submit(body: WmsOrderIn):
    """Idempotent by `ref`: the same ref again returns the existing order with HTTP 200.
    Auth: header `X-API-Key` (see the API page) or a logged-in user."""
    try:
        o, created = GATEWAY.submit(body.model_dump(exclude_none=True))
    except WmsError as e:
        raise HTTPException(422, str(e))
    return JSONResponse({k: v for k, v in o.items() if k != "history"}, status_code=201 if created else 200)


@app.get("/api/wms/orders", tags=["wms"], summary="WMS: orders (newest first)")
async def wms_list(status: Optional[str] = None, limit: int = 100):
    return GATEWAY.list(status, min(1000, max(1, limit)))


@app.get("/api/wms/orders/{ref:path}", tags=["wms"], summary="WMS: status of one order")
async def wms_get(ref: str):
    if ref in GATEWAY.orders:
        return GATEWAY.get(ref)
    old = STORE.wms_lookup(ref)
    if old is None:
        raise HTTPException(404, "unknown ref")
    return old


@app.delete("/api/wms/orders/{ref:path}", tags=["wms"], summary="WMS: cancel an order")
async def wms_cancel(ref: str):
    if ref not in GATEWAY.orders:
        raise HTTPException(404, "unknown ref")
    try:
        o = GATEWAY.cancel(ref)
    except WmsError as e:
        raise HTTPException(409, str(e))
    return {k: v for k, v in o.items() if k != "history"}


@app.get("/api/wms/events", tags=["wms"], summary="WMS: status changes after a sequence number (polling)")
async def wms_events(after: int = 0, limit: int = 200):
    """Poll with the `last` value of the previous answer. Each event: {seq, ref, status, t, robots, reason, cycle}."""
    return GATEWAY.events(after, min(1000, max(1, limit)))


@app.get("/api/wms/status", tags=["wms"], summary="WMS gateway and mock WMS status")
async def wms_status():
    return {**GATEWAY.stats(), "mock": MOCK_WMS.status(), "hooks": GATEWAY.webhooks}


@app.post("/api/wms/mock", tags=["wms"], summary="Mock WMS on / off (admin)")
async def wms_mock(body: MockIn):
    """On: a built-in WMS releases order waves through the WMS API and the built-in order generator stops."""
    MOCK_WMS.set(body.on, body.wave_min, body.scale)
    return MOCK_WMS.status()


@app.post("/api/wms/webhooks", tags=["wms"], summary="Add a webhook (admin)")
async def wms_hook_add(body: WebhookIn):
    try:
        return GATEWAY.add_webhook(body.url.strip())
    except WmsError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/wms/webhooks", tags=["wms"], summary="Remove a webhook (admin)")
async def wms_hook_del(url: str):
    return GATEWAY.remove_webhook(url)


@app.get("/api/wms/key", tags=["wms"], summary="WMS API key (admin)")
async def wms_key():
    return {"key": AUTH.wms_key, "header": "X-API-Key"}


@app.post("/api/wms/key/rotate", tags=["wms"], summary="New WMS API key (admin; the old one stops working)")
async def wms_key_rotate():
    return {"key": AUTH.rotate_wms_key(), "header": "X-API-Key"}


# ---------------------------------------------------------------- shift history + replay
@app.get("/api/shifts", tags=["replay"], summary="Recorded shifts")
async def shifts(limit: int = 50):
    RECORDER.flush()
    return STORE.shifts(min(500, max(1, limit)))


@app.get("/api/shifts/{sid}", tags=["replay"], summary="One shift: map, robots, KPIs, events, orders, alarms")
async def shift(sid: int):
    if sid == RECORDER.sid:
        RECORDER.flush()
    try:
        return STORE.shift(sid)
    except KeyError:
        raise HTTPException(404, "unknown shift")


@app.get("/api/shifts/{sid}/frames", tags=["replay"], summary="Robot positions for replay")
async def shift_frames(sid: int, t0: float = 0, t1: float = 120):
    """One frame per simulated second: [t, [[x, y, state, battery, load, blocked_s, order], ...]] in the robot order of the shift."""
    if sid == RECORDER.sid:
        RECORDER.flush()
    return {"frames": STORE.frames(sid, t0, min(t1, t0 + 900))}


@app.delete("/api/shifts/{sid}", tags=["replay"], summary="Delete a recorded shift (admin)")
async def delete_shift(sid: int):
    if sid == RECORDER.sid:
        raise HTTPException(409, "this shift is running")
    STORE.delete_shift(sid)
    return {"ok": True}


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
    if auth_mod.ENABLED and AUTH.user(websocket.cookies.get(auth_mod.COOKIE)) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            snap = sim.snapshot()
            snap["alarms"] = ALARMS.list()[:40]
            snap["alarm_summary"] = ALARMS.summary()
            await websocket.send_json(snap)
            await asyncio.sleep(TICK)
    except (WebSocketDisconnect, RuntimeError):
        pass


# Frontend last, so /api and /ws take priority.
app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")
