"""Layout Designer back end: compiler, problem checker, design store, API.  python -m pytest -q"""
import json
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("TUSCORE_DATA", tempfile.mkdtemp(prefix="tuscore-test-"))

from app.layout import check_layout, compile_layout, from_config, normalise   # noqa: E402
from app.sim import Simulation   # noqa: E402

HERE = os.path.dirname(__file__)
CONFIG = os.path.join(HERE, "..", "..", "config")
EXAMPLES = ["demo-warehouse", "regional-dc", "l-shaped-crossdock"]


def example(name):
    return json.load(open(os.path.join(CONFIG, "layouts", name + ".json"), encoding="utf-8"))


def run(layout, hours=1.0, mode="tuscore", seed=3):
    cfg, rep = compile_layout(layout)
    assert rep["ok"], rep["problems"]
    p = os.path.join(os.environ["TUSCORE_DATA"], "t.yaml")
    yaml.safe_dump(cfg, open(p, "w"), sort_keys=False)
    return Simulation(p, seed=seed, mode=mode, overrides={"shift_hours": hours}).run_headless()


def small(**robots):
    """A 30 x 20 m site: two racks, packing, charging."""
    return {"name": "t", "width_m": 30, "height_m": 20, "elements": [
        {"id": "r1", "type": "rack", "x": 4, "y": 3, "w": 16, "h": 2},
        {"id": "r2", "type": "rack", "x": 4, "y": 7, "w": 16, "h": 2},
        {"id": "p1", "type": "packing", "x": 22, "y": 2, "w": 5, "h": 4},
        {"id": "c1", "type": "charging", "x": 4, "y": 15, "w": 10, "h": 4}],
        "robots": robots or {"tote_amr": 3, "scissor_lift": 0, "pallet_stacker": 0, "drone": 0},
        "orders": {"box_pick": 60, "high_pick": 0, "pallet_move": 0, "inventory_scan": 0}}


def codes(rep):
    return {p["code"] for p in rep["problems"]}


def test_examples_have_no_errors_and_run_without_collisions():
    for name in EXAMPLES:
        k = run(example(name), hours=1)
        assert k["completed"] > 100 and k["collisions"] == 0 and k["permanent_deadlocks"] == 0, name


def test_drawn_task1_site_gives_the_task1_result():
    """The Task 1 site redrawn in the designer must behave exactly like the hand-written YAML."""
    cfg = yaml.safe_load(open(os.path.join(CONFIG, "warehouse.yaml")))
    a = Simulation(os.path.join(CONFIG, "warehouse.yaml"), seed=5, overrides={"shift_hours": 1}).run_headless()
    b = run(from_config(cfg), hours=1, seed=5)
    assert (a["completed"], a["avg_cycle"]) == (b["completed"], b["avg_cycle"])


def test_unusual_layout_with_walls_and_vertical_racks_runs_both_modes():
    for mode in ("tuscore", "separate"):
        k = run(example("l-shaped-crossdock"), hours=1, mode=mode)
        assert k["collisions"] == 0 and k["permanent_deadlocks"] == 0


def test_station_behind_a_wall_is_reported():
    L = small()
    L["elements"].append({"id": "w1", "type": "wall", "x": 21, "y": 0, "w": 1, "h": 8})
    L["elements"].append({"id": "w2", "type": "wall", "x": 21, "y": 7, "w": 9, "h": 1})
    rep = check_layout(L)
    assert not rep["ok"] and "unreachable" in codes(rep)
    assert any("p1" in p["ids"] for p in rep["problems"] if p["code"] == "unreachable")


def test_rack_with_both_faces_blocked_is_an_error():
    L = small()
    L["elements"] += [{"id": "w1", "type": "wall", "x": 4, "y": 2, "w": 16, "h": 1},
                      {"id": "w2", "type": "wall", "x": 4, "y": 5, "w": 16, "h": 2}]
    rep = check_layout(L)
    assert "rack_no_face" in codes(rep) and not rep["ok"]


def test_narrow_aisle_blocks_high_picks_but_not_box_picks():
    L = small(tote_amr=3, scissor_lift=1, pallet_stacker=0, drone=0)
    L["orders"]["high_pick"] = 20
    for e in L["elements"]:                     # racks 1 m apart, the outer faces against walls
        if e["id"] == "r2":
            e["y"] = 6
    L["elements"] += [{"id": "w1", "type": "wall", "x": 3, "y": 2, "w": 18, "h": 1},
                      {"id": "w2", "type": "wall", "x": 3, "y": 8, "w": 18, "h": 1}]
    rep = check_layout(L)
    assert "aisle_lift" in codes(rep) and "no_lift_aisle" in codes(rep) and not rep["ok"]
    L["orders"]["high_pick"] = 0
    L["robots"]["scissor_lift"] = 0
    assert check_layout(L)["ok"]


def test_missing_charging_overlap_and_order_without_robot():
    L = small()
    L["elements"] = [e for e in L["elements"] if e["type"] != "charging"]
    assert "no_charging" in codes(check_layout(L))
    L = small()
    L["elements"].append({"id": "x", "type": "packing", "x": 5, "y": 3, "w": 3, "h": 3})   # on a rack
    assert "overlap" in codes(check_layout(L))
    L = small()
    L["orders"]["inventory_scan"] = 5                                                         # no drone
    assert "kind_no_robot" in codes(check_layout(L))


def test_orders_without_places_are_switched_off():
    rep = check_layout(small())
    assert rep["ok"] and rep["effective_orders"]["pallet_move"] == 0


def test_normalise_snaps_and_drops_unknown_types():
    L = normalise({"width_m": 30.4, "elements": [{"type": "rack", "x": 1.4, "y": 2.6, "w": 0.2, "h": 3},
                                                  {"type": "spaceship", "x": 0, "y": 0, "w": 1, "h": 1}]})
    assert L["width_m"] == 30 and len(L["elements"]) == 1
    assert {k: L["elements"][0][k] for k in "xywh"} == {"x": 1, "y": 3, "w": 1, "h": 3}


def test_api_save_versions_check_and_apply():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    assert {"rack", "wall", "charging"} <= set(c.get("/api/catalog").json()["elements"])
    ids = [d["id"] for d in c.get("/api/designs").json()]
    assert set(EXAMPLES) <= set(ids)
    L = small()
    r = c.post("/api/designs", json={"layout": L, "note": "first"}).json()
    did = r["id"]
    assert r["version"] == 1
    L["elements"][0]["w"] = 14
    assert c.put(f"/api/designs/{did}", json={"layout": L}).json()["version"] == 2
    assert [v["version"] for v in c.get(f"/api/designs/{did}/versions").json()] == [1, 2]
    assert c.get(f"/api/designs/{did}?version=1").json()["layout"]["elements"][0]["w"] == 16
    assert c.post("/api/designs/check", json=L).json()["ok"]
    a = c.post(f"/api/designs/{did}/apply", json={}).json()
    assert a["layout"].startswith("design:") and main.sim.cfg["name"] == "t"
    assert any(l.get("design") and l["active"] for l in c.get("/api/layouts").json())
    # a template is never overwritten: saving it makes a copy
    t = c.put("/api/designs/demo-warehouse", json={"layout": example("demo-warehouse")}).json()
    assert t["id"] != "demo-warehouse"
    # broken layout cannot run
    bad = small()
    bad["elements"] = [e for e in bad["elements"] if e["type"] != "charging"]
    bid = c.post("/api/designs", json={"layout": bad}).json()["id"]
    assert c.post(f"/api/designs/{bid}/apply", json={}).status_code == 422


# ---------------------------------------------------------------- Part 2: custom robot types, floor plans
def test_custom_robot_type_runs_and_is_checked_by_its_size():
    L = small(tote_amr=0, scissor_lift=0, pallet_stacker=0, drone=0)
    L["custom_robots"] = {"c_p800": {"label": "Geek+ P800", "role": "carry_tote", "vendor": "Geek+", "adapter": "rest",
                                     "width_m": 0.8, "speed_mps": 1.5, "cost_eur": 41000}}
    L["robots"]["c_p800"] = 3
    rep = check_layout(L)
    assert rep["ok"], rep["problems"]
    t = rep["config"]["robot_types"]["c_p800"]
    assert t["capabilities"] == ["carry_tote"] and t["adapter"] == "rest" and t["model"] == "tote_amr" and t["speed"] == 1.5
    k = run(L, hours=1)
    assert k["completed"] > 30 and k["collisions"] == 0
    # the same robot 1.9 m wide does not fit the 2 m aisle between the racks (outer sides walled off)
    L["custom_robots"]["c_p800"]["width_m"] = 1.9
    L["elements"] += [{"id": "w1", "type": "wall", "x": 3, "y": 2, "w": 18, "h": 1},
                      {"id": "w2", "type": "wall", "x": 3, "y": 9, "w": 18, "h": 1}]
    rep = check_layout(L)
    assert "aisle_tote" in codes(rep) and not rep["ok"]


def test_custom_robot_values_are_clamped_and_bad_ones_dropped():
    L = normalise({"custom_robots": {"c_x": {"role": "move_pallet", "width_m": 99, "speed_mps": -3},
                                     "c_bad": {"role": "teleport"}, "tote_amr": {"role": "scan"}},
                   "robots": {"c_x": 2, "c_bad": 4}})
    assert set(L["custom_robots"]) == {"c_x"} and L["robots"]["c_x"] == 2 and "c_bad" not in L["robots"]
    assert L["custom_robots"]["c_x"]["width_m"] == 3.0 and L["custom_robots"]["c_x"]["speed_mps"] == 0.1


def test_floor_plan_upload_png_and_pdf_pages():
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    im = Image.new("RGB", (800, 500), "white")
    b = io.BytesIO(); im.save(b, "PNG")
    r = c.post("/api/backgrounds?name=plan.png", content=b.getvalue())
    assert r.status_code == 201 and r.json()["w_px"] == 800 and r.json()["id"].endswith(".png")
    assert c.get("/api/backgrounds/" + r.json()["id"]).status_code == 200
    b = io.BytesIO(); im.save(b, "PDF", save_all=True, append_images=[im.rotate(90, expand=True)], resolution=72)
    j = c.post("/api/backgrounds?name=plan.pdf", content=b.getvalue()).json()
    assert j["pages"] == 2 and j["page"] == 1
    j2 = c.post(f"/api/backgrounds/pdf/{j['source']}/page/2").json()
    assert (j2["w_px"], j2["h_px"]) == (j["h_px"], j["w_px"])
    assert c.post("/api/backgrounds?name=x.dwg", content=b"AC1027 not an image").status_code == 415
    assert c.get("/api/backgrounds/../../etc/passwd").status_code == 404
    # the plan travels with the layout and never changes the simulation
    L = small()
    L["background"] = {"id": j["id"], "w_px": j["w_px"], "h_px": j["h_px"], "m_per_px": 0.05, "x": 1, "y": 2}
    assert normalise(L)["background"]["id"] == j["id"]
    a, b = compile_layout(L)[0], compile_layout(small())[0]
    assert a["shelves"] == b["shelves"] and a["zones"] == b["zones"] and a["walls"] == b["walls"]
    assert normalise({"background": {"id": "../evil.png"}})["background"] is None


def test_robot_library_api():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    assert c.put("/api/robot-types/c_fast", json={"label": "Fast", "role": "carry_tote", "speed_mps": 2}).status_code == 200
    assert "c_fast" in c.get("/api/catalog").json()["library"]
    assert c.put("/api/robot-types/c_fast", json={"role": "fly"}).status_code == 422
    assert c.put("/api/robot-types/Bad Key", json={"role": "scan"}).status_code == 400
    assert "c_fast" not in c.delete("/api/robot-types/c_fast").json()


# ---------------------------------------------------------------- Step 2 part 1: traffic rules
def corridor(**extra):
    """30 x 20 m: two racks with a 1 m aisle between them (y = 5), open at both ends."""
    L = small()
    for e in L["elements"]:
        if e["id"] == "r2":
            e["y"] = 6
    L["elements"] += [{"id": "w1", "type": "wall", "x": 4, "y": 2, "w": 16, "h": 1},
                      {"id": "w2", "type": "wall", "x": 4, "y": 8, "w": 16, "h": 1}]
    L.update(extra)
    return L


def test_one_way_lane_into_a_dead_end_is_an_error():
    L = corridor()
    L["elements"] += [{"id": "end", "type": "wall", "x": 20, "y": 2, "w": 1, "h": 7},     # close the east end
                      {"id": "ln", "type": "lane", "x": 4, "y": 5, "w": 16, "h": 1, "dir": "E"}]
    rep = check_layout(L)
    assert "lane_dead_end" in codes(rep) and not rep["ok"]
    for e in L["elements"]:                                                                  # pointing out is fine
        if e["id"] == "ln":
            e["dir"] = "W"
    rep = check_layout(L)
    assert "lane_dead_end" not in codes(rep)


def test_lanes_that_trap_a_station_and_lanes_that_clash():
    L = small()
    # the packing station sits behind a 1 m door; a one-way lane through the door only lets robots in
    L["elements"] += [{"id": "w1", "type": "wall", "x": 21, "y": 0, "w": 1, "h": 3},
                      {"id": "w2", "type": "wall", "x": 21, "y": 4, "w": 1, "h": 4},
                      {"id": "w3", "type": "wall", "x": 21, "y": 7, "w": 9, "h": 1},
                      {"id": "ln", "type": "lane", "x": 20, "y": 3, "w": 3, "h": 1, "dir": "E"}]
    rep = check_layout(L)
    assert "lane_trap" in codes(rep) or "lane_dead_end" in codes(rep)
    assert not rep["ok"]
    L = corridor()
    L["elements"] += [{"id": "a", "type": "lane", "x": 4, "y": 5, "w": 10, "h": 1, "dir": "E"},
                      {"id": "b", "type": "lane", "x": 10, "y": 5, "w": 10, "h": 1, "dir": "W"}]
    assert "lane_conflict" in codes(check_layout(L))


def test_traffic_compiles_and_waypoints_are_checked():
    L = corridor()
    L["elements"] += [{"id": "ln", "type": "lane", "x": 4, "y": 5, "w": 16, "h": 1, "dir": "N"},   # wrong axis -> fixed
                      {"id": "sz", "type": "slow", "x": 22, "y": 10, "w": 4, "h": 4, "max_speed": 0.3},
                      {"id": "one", "type": "single", "x": 20, "y": 4, "w": 3, "h": 3},
                      {"id": "wp", "type": "waypoint", "x": 25, "y": 16, "w": 5, "h": 5, "name": "Wait A"},
                      {"id": "wp2", "type": "waypoint", "x": 8, "y": 3}]                              # inside a rack
    rep = check_layout(L)
    t = rep["config"]["traffic_rules"]
    assert t["lanes"][0]["dir"] in ("E", "W") and t["speed"][0]["max"] == 0.3 and len(t["single"]) == 1
    assert t["waypoints"]["Wait A"] == [25, 16]
    assert "traffic_on_solid" in codes(rep) and not rep["ok"]


def test_suggested_traffic_on_the_l_shaped_site():
    from app.layout import suggest_traffic
    L = example("l-shaped-crossdock")
    sug = suggest_traffic(L)
    kinds = [e["type"] for e in sug["elements"]]
    assert kinds.count("lane") >= 2 and kinds.count("single") >= 1
    dirs = {e["dir"] for e in sug["elements"] if e["type"] == "lane"}
    assert dirs == {"E", "W"}                                  # neighbouring aisles go opposite ways
    L["elements"] += sug["elements"]
    assert check_layout(L)["ok"]
    assert suggest_traffic(example("demo-warehouse"))["elements"] == []


def test_simulation_obeys_one_way_lanes_one_robot_zones_and_slow_zones():
    from app.layout import suggest_traffic
    L = example("l-shaped-crossdock")
    L["elements"] += suggest_traffic(L)["elements"]
    L["elements"].append({"id": "slow", "type": "slow", "x": 40, "y": 38, "w": 20, "h": 6, "max_speed": 0.5})
    cfg, rep = compile_layout(L)
    assert rep["ok"]
    p = os.path.join(os.environ["TUSCORE_DATA"], "traffic.yaml")
    yaml.safe_dump(cfg, open(p, "w"), sort_keys=False)
    for mode in ("tuscore", "separate"):
        s = Simulation(p, seed=2, mode=mode, overrides={"shift_hours": 0.5})
        g, prev, wrong, crowded, slow_fast = s.grid, {r.id: r.cell for r in s.robots.values()}, 0, 0, 0
        while s.sim_time < 1800:
            before = {r.id: (r.cell, r.frac) for r in s.robots.values()}
            s._step(0.1)
            for r in s.robots.values():
                if r.flying:
                    continue
                if r.cell != prev[r.id]:
                    wrong += not g.can_move(prev[r.id], r.cell)
                    prev[r.id] = r.cell
                c0, f0 = before[r.id]
                if c0 == r.cell and c0 in g.slow and r.frac - f0 > g.slow[c0] * 0.1 + 1e-9:
                    slow_fast += 1
            for cs in g.mutex_cells:
                crowded += sum(1 for r in s.robots.values() if not r.flying and r.cell in cs) > 1
        k = s.kpis()
        assert wrong == 0 and crowded == 0 and slow_fast == 0, (mode, wrong, crowded, slow_fast)
        assert k["collisions"] == 0 and k["permanent_deadlocks"] == 0 and k["completed"] > 100
        assert s.stats["zone_entries"] > 0


def test_suggest_traffic_api():
    from fastapi.testclient import TestClient
    from app import main
    r = TestClient(main.app).post("/api/designs/suggest-traffic", json=example("l-shaped-crossdock")).json()
    assert r["elements"] and r["message"].startswith("Suggested")
