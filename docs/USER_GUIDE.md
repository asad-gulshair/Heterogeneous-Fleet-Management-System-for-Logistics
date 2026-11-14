# TusCore user guide: Layout Designer

You can draw your warehouse, check it, and run a simulated shift on it without editing any code or config.

## Start

* **Windows:** double-click `run.bat`, then open http://localhost:8000.
* **Docker:** run `docker compose up`, then open http://localhost:8000.

Click **Layout** at the top of the left menu.

## 1. Quick start (5 minutes)

1. Click **New** to get an empty floor.
2. Click **Upload floor plan** and choose a PDF, PNG or JPG. You can also drag the file onto the floor.
3. Set the scale (section 2): click two points you know the distance between and type the real distance.
4. Trace the plan. Pick **Rack**, **Wall**, **Packing station**, **Charging area** and so on, then drag on top of the plan.
5. Set the number of robots and the orders per hour on the left.
6. Fix anything in red in **Problems**.
7. Click **Save** (Ctrl+S), then **Run shift**. The live 3D twin opens on your layout.

To try the designer without your own plan, use `config/plans/sample-floor-plan.pdf`. The dimension line along its top is 80.00 m.

## 2. Floor plan and scale

A drawing has no real-world size until you set its scale. Set it once per plan.

| Control | What it does |
|---|---|
| **Set scale** | Click the first point, then the second point. Hold Shift to keep the line straight. Type the real distance in metres and press Enter. Pick the longest dimension you trust, such as the overall building length, for the best accuracy. |
| **Floor = plan** | Makes the floor the same size as the plan. This happens automatically after the first scale on an empty floor. |
| **Move plan** | Drag the plan so it lines up with the floor. Arrow keys move it 10 cm, or 1 m with Shift. |
| **Turn 90°** / **Angle** | Rotates a plan that is sideways or a scan that is slightly tilted. |
| **Fade** | Sets how much the plan shows through your drawing. |
| **Dark** | Shows a white paper plan as light lines on dark, which is easier to read in the console. |
| **Lock** | Stops the plan from being moved or rescaled by accident. |
| **Page** | Picks a page of a multi-page PDF. |
| **Replace** / **Remove** | Swaps in another file or removes the plan. Your drawing stays. |

**Supported files:**
* PDF: one page is rendered at 150 dpi.
* Images: PNG, JPG, WEBP, BMP, TIFF and GIF, up to 40 MB.
* CAD files (DWG/DXF): export the plan to PDF from your CAD tool first.

The plan is only a tracing guide. Robots use what you draw, not the image.

## 3. Drawing

| Element | Meaning for robots |
|---|---|
| Rack | Shelving that is picked from its two long sides. The drone flies over it. Set **Levels** in Properties. |
| Wall | Blocks every robot, including the drone. |
| No-go zone | A people-only or unsafe area that no robot enters. You can give it a name, such as "Office". |
| Packing station | Where tote carriers deliver picked totes. |
| Pallet area / Loading dock | Pallet movers take pallets from a pallet area to a dock. |
| Charging area | Where robots park and charge. Every robot starts here. **Charger bays** sets how many chargers it has. |

**Editing:**
* Everything snaps to the 1 m grid, which is what the simulation uses.
* Drag an element to move it, or drag its handles to resize it.
* **R** rotates, **Ctrl+D** duplicates, **Del** deletes, and the arrow keys move by 1 m.
* **Ctrl+Z** / **Ctrl+Y** undo and redo, including changes to the plan and the robots.
* Scroll the mouse wheel to zoom, and drag on empty floor to pan.
* **F** fits the floor to the screen.
* Hold Shift while drawing to keep the same tool.

## 4. Robots

* The four built-in robots are the Tote AMR, the TL-65 scissor lift, the pallet stacker and the inventory drone. Use **−** / **+** to set how many of each.
* To add a robot from another vendor, click **+ Robot type**. Fill in:
  * **Job:** tote carrier, high-shelf picker, pallet mover or flying inventory scanner. TusCore plans the robot exactly like the built-in robot that does the same job.
  * **Width / length:** these decide which aisles it fits (see section 5).
  * **Speed, battery and price:** speed and battery are used in the simulation, and price is used in the cost results.
  * **Protocol:** VDA 5050, REST, TCP/JSON or MAVLink-style. The robot's commands go through that vendor adapter, and you can see them in **Live → Protocol**.
* Leave **Save to my robot library** ticked to reuse the type in other layouts with **Add a saved robot type…**.
* Every saved layout keeps its own copy of each robot type it uses, so old versions always run the same way.

## 5. Problems

* **Red (error)** blocks Run shift.
* **Orange (warning)** explains a risk.
* **Grey (info)** is for information only.

Click a problem to select the element and centre the view on it.

| Problem | Fix |
|---|---|
| Station cannot be reached from the charging area | Open a path. A wall, rack or no-go zone is closing it off. |
| Rack cannot be picked from | Leave an aisle along at least one long side. |
| Aisle too narrow for a robot | Aisle width needed = robot width + 0.15 m on each side, rounded up to whole metres. A 0.6 m tote AMR needs 1 m, and a 0.9 m TL-65 needs 2 m. Racks where a high-shelf picker does not fit get no high picks. |
| Orders with no robot to do them | Add a robot for that job or set that order rate to 0. |
| Order type switched off | That order type needs a place you have not drawn, such as a pallet area and a dock for pallet moves. |
| Robots with nothing to do | Their job has no orders in this layout. |
| Few chargers / little parking | Make the charging area bigger, add another one, or set **Charger bays**. |
| Overlap / outside the floor | Move the element. |

## 6. Save, versions and running

* **Save** creates a new version every time: v1, v2, and so on. Nothing is overwritten. Pick an older version from the version list to open it.
* The examples are read-only. Saving one makes your own copy.
* If you close the browser by mistake, your unsaved drawing comes back the next time you open the Layout page.
* **Run shift** saves the layout, turns it into the simulation map and replaces the running shift. Robots start in the charging area. The **Live** page shows the twin in 3D or 2D.
* The **Compare**, **What-if** and **Analytics** pages work on your layout the same way they work on the examples.

## 7. Limits in this version

* Shapes are rectangles on a 1 m grid, rotated in 90° steps. Step 2 adds the waypoint and lane map with one-way lanes and traffic zones.
* All results come from the simulated twin, not from real robots.
