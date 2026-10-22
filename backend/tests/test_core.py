"""Run with:  python -m pytest -q   (from the backend folder)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.adapters import Bus, build_adapters
from app.adapters.base import compress, expand
from app.grid import Grid
from app.planner import astar
from app.sim import Simulation
from app.traffic import find_cycles

CFG = os.path.join(os.path.dirname(__file__), "..", "..", "config", "warehouse.yaml")


def test_astar_avoids_shelves_and_flies_over_them():
    sim = Simulation(CFG)
    g = sim.grid
    ground = astar(g, (0, 0), (20, 8))
    assert ground and all(c not in g.blocked for c in ground)
    air = astar(g, (10, 4), (10, 12), flying=True)
    assert air and len(air) == 8                      # straight over the shelves


def test_compress_expand_roundtrip():
    path = [(1, 0), (2, 0), (2, 1), (2, 2), (3, 2)]
    assert expand(compress((0, 0), path)) == path


def test_every_adapter_roundtrips_orders_and_state():
    sim = Simulation(CFG)
    for key, adapter in sim.adapters.items():
        r = next(x for x in sim.robots.values() if x.rtype == key)
        path = [(r.cell[0] + 1, r.cell[1]), (r.cell[0] + 2, r.cell[1]), (r.cell[0] + 2, r.cell[1] + 1)]
        got_path, got_target = adapter.send_order(0.0, r, path, path[-1], "test", 7)
        assert got_path == path and got_target == path[-1], key
        adapter.poll(1.0, r)
        assert abs(r.telemetry["battery"] - r.battery) < 1.0, key
    assert len(sim.bus.recent(100)) > 8


def test_deadlock_cycle_detection():
    assert find_cycles({"a": "b", "b": "a"}) == [["a", "b"]]
    assert find_cycles({"a": "b", "b": "c"}) == []


def test_one_hour_shift_is_safe_and_completes_orders():
    sim = Simulation(CFG, seed=3)
    sim.cfg["shift"]["length_s"] = 3600
    sim.shift_len = 3600
    sim.arrivals.shift = 3600
    k = sim.run_headless()
    assert k["collisions"] == 0 and k["permanent_deadlocks"] == 0
    assert k["completed"] == k["created"] > 100


def test_fault_returns_order_to_queue_and_it_still_completes():
    sim = Simulation(CFG, seed=1)
    sim.mtbf_s = 0
    sim.add_order("high_pick")
    while not sim.robots["scissor_lift_1"].task_id and not any(r.task_id for r in sim.robots.values()):
        sim._step(0.5)
    victim = next(r for r in sim.robots.values() if r.task_id)
    sim.inject_fault(victim.id)
    assert victim.state == "fault" and sim.kpis()["reassigned"] == 1
    for _ in range(4000):
        sim._step(0.5)
    assert sim.kpis()["completed"] >= 1


def test_separate_managers_mode_runs_safely_and_splits_handoff():
    sim = Simulation(CFG, seed=4, mode="separate", overrides={"shift_hours": 1})
    k = sim.run_headless()
    assert k["collisions"] == 0 and k["completed"] == k["created"] > 100
    # dedicated chargers per vendor
    assert set(sim.bay_owner.values()) == set(sim.type_count)


def test_large_layout_loads_from_yaml_only():
    big = os.path.join(os.path.dirname(CFG), "warehouse_large.yaml")
    sim = Simulation(big, seed=2, overrides={"shift_hours": 0.5})
    assert sim.grid.width == 80 and len(sim.robots) == 19
    k = sim.run_headless()
    assert k["collisions"] == 0 and k["completed"] == k["created"]


def test_whatif_overrides_apply():
    sim = Simulation(CFG, overrides={"robot_counts": {"tote_amr": 9}, "load_factor": 2.0, "charging_bays": 4})
    assert sum(1 for r in sim.robots.values() if r.rtype == "tote_amr") == 9
    assert sim.cfg["orders"]["rate_per_hour"]["box_pick"] == 340
    assert len(sim.grid.chargers) == 4


def test_api_end_to_end_single_control_point():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    c.post("/api/sim/auto-orders", json={"enabled": False})
    c.post("/api/sim/reset", json={"seed": 7})
    for kind in ("box_pick", "high_pick", "pallet_move", "inventory_scan"):
        assert c.post("/api/orders", json={"kind": kind}).status_code == 201
    for _ in range(1200):
        main.sim._step(0.5)
    k = c.get("/api/kpis").json()
    assert k["completed"] == 4 and k["collisions"] == 0
    assert c.get("/api/report.csv").status_code == 200
    assert len(c.get("/api/layouts").json()) >= 2
    assert c.post("/api/sim/mode", json={"mode": "separate"}).json()["mode"] == "separate"
    assert c.post("/api/sim/mode", json={"mode": "tuscore"}).json()["mode"] == "tuscore"
