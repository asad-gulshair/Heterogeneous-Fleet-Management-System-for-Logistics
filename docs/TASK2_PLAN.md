# Task 2 plan - Layout Designer and Fleet Manager

Goal from the brief: *a person who has never seen the code opens the application, draws or imports a warehouse,
runs a shift on it, and gets a recommendation for how many robots of each type they need - without editing code or
configuration files.*

## 1. How professional systems do it (what we copy)

| System | What it does | What we take |
|---|---|---|
| Open-RMF traffic editor (ROS) | Floor-plan image as background, scale set from a measured line, robots' lanes and waypoints drawn on top, exported as a navigation graph | Background image + two-point scale; layout is a drawing *on top of* the plan; graph generated from it (Step 2) |
| OTTO / Clearpath fleet manager | Lanes and junctions, exclusion (no-go) zones, preferred-direction (one-way) zones, single-robot zones, speed limits | Same zone vocabulary; no-go zones now, traffic zones in Step 2 |
| VDA 5050 + LIF (Layout Interchange Format) | Vendor-neutral layout of nodes, edges and stations, shared by all AGV vendors | Our layout file is vendor-neutral JSON with stations; LIF export later |
| Nav2 / SLAM maps | Robots drive on an occupancy grid (`map.pgm` + `map.yaml`): black = obstacle, white = free | Compiled simulation map is an occupancy grid at 1 m; optional SLAM-map import in Step 3 (ROS 2 adapter) |
| Fleet-sizing simulators (Simio, GreyOrange) | Run the layout with an order profile, compare robot counts and cost | What-if + ROI from Task 1, made automatic in Step 3 |

Two map layers, like real sites:
1. **Geometry** - walls, racks, no-go zones: where a robot physically cannot be.
2. **Meaning** - packing stations, docks, pallet areas, chargers, rack faces, later lanes and traffic rules.

## 2. User flow (Step 1)

1. Open **Layout** page -> start blank, from an example, or (Part 2) load a floor plan image / PDF and set its scale.
2. Drag elements from the palette: wall, rack, pallet area, packing station, dock, charging area, no-go zone.
3. Move, resize, rotate (90 deg), duplicate, delete; undo / redo; properties panel with exact metres.
4. Set robot counts from the catalog (tote AMR, Tusmec TL-65, pallet stacker, drone).
5. **Problems panel** updates live: errors block the run, warnings explain risk; clicking a problem highlights it on the map.
6. **Save** (every save is a new version) -> **Run shift** -> the Live page shows the 2D / 3D twin of the new layout.

## 3. Architecture

```
Layout Designer (browser, SVG editor)
   │  layout JSON (metres, vendor-neutral)
   ▼
/api/designs  ── store: designs/<id>/v001.json, v002.json ...   (Step 2: database)
   │
   ▼
compiler (backend/app/layout.py)
   ├─ rasterise to 1 m occupancy grid (walls / racks / no-go blocked)
   ├─ derive rack faces, aisles, charger bays, spawn spots
   ├─ validate (problems list)
   └─ write sim config  ->  same schema the Task 1 simulation already runs
   ▼
Simulation + 2D / 3D twin (unchanged core, generalised for any layout)
```

The compiler outputs the same config format as `config/warehouse.yaml`, so everything from Task 1 (allocation,
traffic, A/B comparison, what-if) works on drawn layouts with no special cases.

## 4. Layout file (v1)

```json
{
  "schema": "tuscore.layout/1",
  "name": "My DC", "width_m": 60, "height_m": 40, "grid_m": 1,
  "background": null,
  "elements": [
    {"id": "e1", "type": "rack", "x": 6, "y": 5, "w": 30, "h": 2, "name": "A1", "levels": 4},
    {"id": "e2", "type": "packing", "x": 4, "y": 32, "w": 6, "h": 4},
    {"id": "e3", "type": "charging", "x": 30, "y": 33, "w": 14, "h": 5, "bays": 6},
    {"id": "e4", "type": "wall", "x": 0, "y": 20, "w": 12, "h": 1},
    {"id": "e5", "type": "nogo", "x": 40, "y": 20, "w": 3, "h": 3}
  ],
  "robots": {"tote_amr": 6, "scissor_lift": 3, "pallet_stacker": 2, "drone": 1},
  "orders": {"box_pick": 170, "high_pick": 45, "pallet_move": 24, "inventory_scan": 6}
}
```

Element types: `wall`, `rack`, `nogo` (block robots) - `packing`, `dock`, `pallet_area`, `charging` (floor zones robots
work in). Racks are horizontal (long side along x) or vertical; picking happens from the long faces.

## 5. Problems the user can face, and what the system does

| Problem | What we do |
|---|---|
| Floor plan scale wrong -> every distance wrong | Two-point scale tool, live scale bar and dimensions, warning if the site is implausibly large or small |
| Huge / multi-page PDF, rotated scan | Render one page, downscale to <= 4096 px, rotate 90 deg, adjust opacity, lock background (Part 2) |
| Aisle too narrow for a robot type | Per-type corridor check (robot width + 0.15 m safety each side); rack faces a TL-65 cannot reach are reported and no high picks are generated there |
| Rack with no reachable face | Error per rack; partly blocked racks only get orders at reachable bays |
| Station enclosed by walls / unreachable | Reachability from the charging area per robot type; error with the station highlighted |
| Missing essentials (no packing, no charger, stackers without dock) | Clear error naming what to add |
| More robots than charger bays / parking | Warning with the numbers |
| Elements overlap or leave the floor | Error, element highlighted |
| 1 m grid rounds a 1.5 m aisle | Everything snaps to the 1 m grid in Step 1 and the editor shows exactly what the simulation sees; Step 2's waypoint map removes this limit |
| Lost work | Undo / redo, unsaved-changes guard, every save is a version, draft kept in the browser |
| Switching layout during a running shift | Confirmation; the running shift is replaced, results already saved stay |

## 6. Assumptions (stated in the report)

* Step 1 simulation map: 1 m occupancy grid, axis-aligned rectangles, 90 deg rotations. Step 2 adds the waypoint / lane map.
* Walls and no-go zones also block the drone; racks do not (it flies over them).
* Robot footprints: tote AMR 0.6 x 0.8 m, TL-65 0.9 x 1.3 m, pallet stacker 1.0 x 1.8 m (catalog values, editable).
* All results come from the simulated twin.

## 7. Step 1 work breakdown

**Part 1 - engine + designer v1**
* `layout.py`: model, compiler, validator, reachability and aisle-width checks; example layouts converted from Task 1.
* Design store with versions + REST API (`/api/designs`, `/validate`, `/apply`, `/api/catalog`).
* Simulation generalised: any number of packing stations / docks / pallet areas, walls and no-go zones,
  vertical racks, robots spawn safely.
* Layout page: palette, draw, move, resize, rotate, duplicate, delete, undo / redo, zoom / pan, properties,
  robot counts, live problems panel, save + versions, Run shift.
* Tests: compile + run each example, every validation rule, Task 1 results unchanged.

**Part 2 - floor plan + polish**
* Background image / PDF, two-point scale, opacity, lock, rotate.
* Custom robot type in the catalog.
* 3D twin details for walls, no-go zones and vertical racks; user-guide page.

Step 2 (week 4) and Step 3 (week 6) follow the brief: waypoint / lane map, traffic zones, missions, WMS API + mock,
database + replay, login + alarms; then robot-count recommendation, cost / payback, ROS 2 Nav2 adapter, installer, docs.
