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
