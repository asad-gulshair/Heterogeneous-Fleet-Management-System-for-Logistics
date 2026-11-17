"""Step 2: priorities, missions, WMS API + mock WMS, alarms, recording / replay, login.  python -m pytest -q"""
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.alarms import AlarmManager                     # noqa: E402
from app.layout import compile_layout                   # noqa: E402
from app.missions import MissionError, MissionManager   # noqa: E402
from app.recorder import Recorder                       # noqa: E402
from app.sim import Simulation                          # noqa: E402
from app.store import Store                             # noqa: E402
from app.wms import MockWms, WmsError, WmsGateway       # noqa: E402

HERE = os.path.dirname(__file__)
CONFIG = os.path.join(HERE, "..", "..", "config")
DATA = os.environ["TUSCORE_DATA"]


def demo_with_waypoints(tmp="wp.yaml", **robots):
    L = json.load(open(os.path.join(CONFIG, "layouts", "demo-warehouse.json"), encoding="utf-8"))
    L["elements"] += [{"id": "w1", "type": "waypoint", "x": 2, "y": 2, "name": "Wait A"},
                      {"id": "w2", "type": "waypoint", "x": 3, "y": 2, "name": "Wait B"}]
    L["robots"].update(robots)
    cfg, rep = compile_layout(L)
    assert rep["ok"], rep["problems"]
    p = os.path.join(DATA, tmp)
    yaml.safe_dump(cfg, open(p, "w"), sort_keys=False)
    return p


def run_for(s, seconds):
    end = s.sim_time + seconds
    while s.sim_time < end:
        s._step(0.5)


def test_task1_result_is_unchanged():
    k = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=3, overrides={"shift_hours": 2}).run_headless()
    assert (k["completed"], k["avg_cycle"]) == (528, 128.1)


def test_urgent_orders_jump_the_queue_and_robot_restrictions_hold():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=1)
    s.auto_orders = False
    normal = [s.add_order("box_pick") for _ in range(20)]
    urgent = s.add_order("box_pick", priority=3)
    s._step(0.5)
    assert urgent.status == "in_progress"
    assert sum(t.status == "in_progress" for t in normal) < len(normal)
    t = s.add_order("box_pick")
    t.require = {"carry_tote": {"robot": "tote_amr_2"}}
    run_for(s, 600)
    assert t.robots.get("carry_tote") == "tote_amr_2"


def test_cancel_queued_and_running_orders():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=1)
    s.auto_orders = False
    seen = []
    s.listeners.append(lambda e, t, x=None: seen.append((e, t.id)))
    a, b = s.add_order("box_pick"), s.add_order("box_pick")
    s._step(0.5)
    assert a.status == "in_progress"
    rid = a.robots["carry_tote"]
    assert s.cancel_task(a.id) and s.robots[rid].task_id is None and s.robots[rid].state == "idle"
    s.paused = True
    c = s.add_order("box_pick")
    assert s.cancel_task(c.id) and c.id not in s.queue
    assert ("cancelled", a.id) in seen and ("cancelled", c.id) in seen and ("started", a.id) in seen


def test_mission_runs_steps_repeats_and_reports():
    s = Simulation(demo_with_waypoints(), seed=2)
    s.auto_orders = False
    mm = MissionManager()
    mm.attach(s)
    m = mm.create({"name": "Replenish", "robot": {"type": "tote_amr"}, "priority": "high", "repeat": 3, "every_min": 2,
                   "steps": [{"to": "A3-05", "do": "load"}, {"to": "Wait A", "do": "wait", "wait_s": 10},
                             {"to": "packing", "do": "unload"}]})
    assert m["status"] == "active" and m["priority"] == 2
    run_for(s, 15 * 60)
    m = mm.public(mm.items[m["id"]])
    assert m["runs"] == 3 and m["status"] == "done" and m["last_cycle"] > 10
    assert s.stats["by_kind"]["mission"] == 3
    for bad in ({"steps": [{"to": "Z99-01", "do": "load"}]}, {"steps": [{"to": "nowhere", "do": "go"}]},
                {"steps": []}, {"steps": [{"to": "Wait A", "do": "dance"}]}, {"robot": {"type": "drone"},
                                                                            "steps": [{"to": "1,1", "do": "go"}]} | {"robot": {"robot": "ghost"}}):
        try:
            mm.create(bad)
            assert False, bad
        except MissionError:
            pass


def test_mission_until_stopped_and_stop_cancels_the_open_run():
    s = Simulation(demo_with_waypoints(), seed=4)
    s.auto_orders = False
    mm = MissionManager()
    mm.attach(s)
    m = mm.create({"name": "Patrol", "robot": {"robot": "tote_amr_1"}, "repeat": 0,
                   "steps": [{"to": "Wait A", "do": "go"}, {"to": "12,20", "do": "go"}]})
    run_for(s, 400)
    assert mm.items[m["id"]]["runs"] >= 2
    mm.set_status(m["id"], "stop")
    assert mm.items[m["id"]]["status"] == "stopped" and mm.items[m["id"]]["open_task"] is None
    assert all(t.mission_id != m["id"] for t in s.tasks.values())


def test_wms_gateway_lifecycle_idempotency_and_rejects():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=5)
    s.auto_orders = False
    gw = WmsGateway()
    gw.attach(s)
    o, new = gw.submit({"ref": "SO-1-1", "type": "box_pick", "location": "A3-05", "destination": "packing_2", "priority": "urgent"})
    assert new and o["status"] == "queued"
    o2, new2 = gw.submit({"ref": "SO-1-1", "type": "box_pick", "location": "A9-01"})
    assert not new2 and o2 is o
    gw.submit({"ref": "SO-1-2", "type": "high_pick", "location": "A2"})
    gw.submit({"ref": "SO-1-3", "type": "transport", "location": "A1-03", "destination": "packing_1"})
    gw.submit({"ref": "SO-1-4", "type": "pallet_move", "priority": "low"})
    for bad in ({"ref": "X1", "type": "box_pick", "location": "Q7-01"}, {"ref": "X2", "type": "teleport"},
                {"ref": "", "type": "box_pick"}, {"ref": "X3", "type": "transport", "location": "A1-01"}):
        try:
            gw.submit(bad)
            assert False, bad
        except WmsError:
            pass
    assert gw.orders["X1"]["status"] == "rejected"
    run_for(s, 900)
    for ref in ("SO-1-1", "SO-1-2", "SO-1-3", "SO-1-4"):
        assert gw.orders[ref]["status"] == "done", (ref, gw.orders[ref])
        assert gw.orders[ref]["cycle"] > 0
    hist = [h[1] for h in gw.orders["SO-1-1"]["history"]]
    assert hist[0] == "queued" and "in_progress" in hist and hist[-1] == "done"
    ev = gw.events(after=0)
    assert [e["seq"] for e in ev["events"]] == sorted(e["seq"] for e in ev["events"]) and ev["last"] == gw.seq
    gw.submit({"ref": "SO-2", "type": "box_pick"})
    gw.cancel("SO-2")
    assert gw.orders["SO-2"]["status"] == "cancelled"
    try:
        gw.cancel("SO-2")
        assert False
    except WmsError:
        pass


def test_wms_works_with_separate_managers_too():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=5, mode="separate")
    s.auto_orders = False
    gw = WmsGateway()
    gw.attach(s)
    gw.submit({"ref": "HP-1", "type": "high_pick"})
    run_for(s, 900)
    assert gw.orders["HP-1"]["status"] == "done"


def test_mock_wms_replaces_the_order_generator():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=6, overrides={"shift_hours": 1})
    gw = WmsGateway()
    gw.attach(s)
    mock = MockWms(gw)
    mock.attach(s)
    mock.set(True, wave_min=5)
    assert not s.auto_orders
    s.run_headless()
    st = gw.stats()
    assert mock.sent > 50 and st["by_status"].get("done", 0) >= mock.sent * 0.9
    assert s.kpis()["collisions"] == 0
    pr = [o["priority"] for o in gw.orders.values()]
    assert 2 in pr and 1 in pr


def test_alarms_raise_ack_clear_and_one_off_events():
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=1)
    am = AlarmManager()
    am.attach(s)
    s.inject_fault("tote_amr_1")
    run_for(s, 2)
    a = next(a for a in am.list() if a["code"] == "robot_fault")
    assert a["state"] == "active_unacked" and a["level"] == "critical"
    assert am.summary()["unacked"] >= 1 and am.summary()["worst"] == "critical"
    am.ack(a["id"], "op1")
    assert a["state"] == "active_acked" and a["acked_by"] == "op1"
    s.repair_robot("tote_amr_1")
    run_for(s, 2)
    assert all(x["code"] != "robot_fault" for x in am.list())          # acked + cleared = closed
    s.set_paused(True)
    run_for(s, 2)
    s.set_paused(False)
    run_for(s, 2)
    p = next(a for a in am.list() if a["code"] == "fleet_paused")
    assert p["state"] == "cleared_unacked"                              # gone, but nobody saw it yet
    s.stats["collisions"] += 1
    run_for(s, 2)
    assert any(a["code"] == "collision" and a["state"] == "cleared_unacked" for a in am.list())
    am.ack_all("op1")
    assert not [a for a in am.list() if a["state"] in ("active_unacked", "cleared_unacked")]


def test_recording_and_replay_data():
    st = Store(os.path.join(DATA, "rec.db"))
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=2)
    rec = Recorder(st)
    rec.attach(s, "warehouse.yaml", {"name": "x"})
    run_for(s, 300)
    rec.flush()
    sh = st.shift(rec.sid)
    assert sh["site"] and len(sh["robots"]) == len(s.robots) and sh["map"] == {"name": "x"}
    assert sh["kpis"]["created"] > 0 and sh["events"] and sh["orders"]
    fr = st.frames(rec.sid, 0, 300)
    assert 295 <= len(fr) <= 302 and len(fr[10][1]) == len(s.robots)
    t, rows = fr[100]
    x, y, state = rows[0][:3]
    assert 0 <= x < s.grid.width and isinstance(state, str)
    assert st.shifts()[0]["id"] == rec.sid


def test_api_end_to_end_with_login():
    from fastapi.testclient import TestClient
    from app import auth, main
    auth.ENABLED = True
    try:
        c = TestClient(main.app)
        assert c.get("/api/robots").status_code == 401
        assert c.get("/api/system").status_code == 200                       # public
        assert c.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "operator", "password": "operator"})
        assert r.status_code == 200 and r.json()["user"]["must_change"]
        assert c.get("/api/robots").status_code == 200
        assert c.post("/api/fleet/pause", json={"enabled": False}).status_code == 200      # operator may
        assert c.post("/api/sim/reset", json={}).status_code == 403                         # admin only
        assert c.get("/api/users").status_code == 403
        assert c.post("/api/auth/password", json={"old": "operator", "new": "short"}).status_code == 422
        assert c.post("/api/auth/password", json={"old": "operator", "new": "operator-2026"}).status_code == 200
        assert not c.get("/api/auth/me").json()["user"]["must_change"]
        # missions + WMS through the API as operator
        tg = c.get("/api/missions/targets").json()
        assert tg["racks"] and tg["zones"]
        m = c.post("/api/missions", json={"name": "T", "steps": [{"to": "A1-02", "do": "load"}, {"to": "packing", "do": "unload"}]})
        assert m.status_code == 201, m.text
        assert c.post("/api/missions", json={"steps": [{"to": "nowhere"}]}).status_code == 422
        assert c.post(f"/api/missions/{m.json()['id']}/pause").json()["status"] == "paused"
        assert c.delete(f"/api/missions/{m.json()['id']}").status_code == 200
        r = c.post("/api/wms/orders", json={"ref": "API-1", "type": "box_pick", "location": "A2-03"})
        assert r.status_code == 201
        assert c.post("/api/wms/orders", json={"ref": "API-1", "type": "box_pick"}).status_code == 200
        assert c.get("/api/wms/orders/API-1").json()["status"] in ("queued", "in_progress")
        assert c.post("/api/wms/mock", json={"on": True}).status_code == 403
        c.post("/api/auth/logout")
        assert c.get("/api/robots").status_code == 401
        # the WMS key reaches the WMS API and nothing else
        key = main.AUTH.wms_key
        h = {"X-API-Key": key}
        assert c.post("/api/wms/orders", json={"ref": "API-2", "type": "box_pick"}, headers=h).status_code == 201
        assert c.get("/api/wms/events?after=0", headers=h).json()["events"]
        assert c.get("/api/robots", headers=h).status_code == 401
        assert c.post("/api/wms/orders", json={"ref": "API-3", "type": "box_pick"}, headers={"X-API-Key": "bad"}).status_code == 401
        # admin: users, audit, alarms, shifts
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        tok = r.json()["token"]
        c2 = TestClient(main.app)
        assert c2.get("/api/users", headers={"Authorization": "Bearer " + tok}).status_code == 200
        assert c.post("/api/users", json={"username": "maria", "password": "short", "role": "operator"}).status_code == 422
        assert c.post("/api/users", json={"username": "maria", "password": "maria-2026!", "role": "operator"}).status_code == 201
        users = c.get("/api/users").json()
        admin_id = next(u["id"] for u in users if u["username"] == "admin")
        assert c.patch(f"/api/users/{admin_id}", json={"role": "operator"}).status_code == 422   # last admin stays
        assert any(a["action"].startswith("POST /api/users") for a in c.get("/api/audit").json())
        assert c.post("/api/wms/mock", json={"on": False}).status_code == 200
        main.sim.inject_fault("tote_amr_1")
        for _ in range(4):
            main.sim._step(0.5)
        al = c.get("/api/alarms").json()
        aid = next(a["id"] for a in al["alarms"] if a["code"] == "robot_fault")
        assert c.post(f"/api/alarms/{aid}/ack").status_code == 200
        hist = c.get("/api/alarms/history").json()
        assert any(h["id"] == aid and h["acked_by"] == "admin" for h in hist)
        sh = c.get("/api/shifts").json()
        assert sh and c.get(f"/api/shifts/{sh[0]['id']}").status_code == 200
        assert "frames" in c.get(f"/api/shifts/{sh[0]['id']}/frames?t0=0&t1=60").json()
        for _ in range(6):                                                    # brute force is slowed down
            c.post("/api/auth/login", json={"username": "maria", "password": "wrong"})
        assert "try again" in c.post("/api/auth/login", json={"username": "maria", "password": "maria-2026!"}).json()["detail"]
    finally:
        auth.ENABLED = False


def test_recording_stops_at_the_end_of_the_shift():
    st = Store(os.path.join(DATA, "rec2.db"))
    s = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=2, overrides={"shift_hours": 0.05})
    rec = Recorder(st)
    rec.attach(s, "warehouse.yaml", {})
    s.run_headless()
    rec.flush(final=True)
    end = st.shift(rec.sid)["sim_end"]
    run_for(s, 600)                         # the twin idles on after the shift
    rec.flush()
    assert st.shift(rec.sid)["sim_end"] == end and st.shift(rec.sid)["ended"]
    assert len(st.frames(rec.sid, 0, 10000)) <= end + 2
