# TusCore user guide

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
   Click **Suggest traffic** for one-way lanes in narrow aisles and one-robot zones in doors.
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

## 4. Traffic rules

Traffic rules are drawn like everything else, from the **Traffic rules** group on the left. They are floor
markings: robots drive over them, but they must follow the rule. The drone flies above all of them.

| Rule | What robots do | Use it for |
|---|---|---|
| **One-way lane** | Drive along it only in the arrow's direction. They may still cross it. Change the direction in Properties; **R** rotates the lane and its arrow. | 1 m aisles where two robots cannot pass, and main routes you want to run as a loop |
| **One-robot zone** | Only one robot is inside at a time. Others wait at the edge until it is free. | Intersections, doors, narrow passages, the area in front of a lift or conveyor |
| **Slow zone** | Drive at no more than the set speed, e.g. 0.4 m/s. Routes avoid it when there is a reasonable way around. | Areas where people walk, dock doors, blind corners |
| **Waypoint** | A named point, e.g. "Wait A". Missions will use waypoints as stops. | Wait spots, parking, hand-over points |

**Suggest traffic** (top bar) does what a fleet engineer would do:
* It turns every 1 m aisle that is open at both ends into a one-way lane. Neighbouring aisles go in opposite
  directions, so robots drive the racks in a snake and never meet head-on.
* It puts a one-robot zone on every door gap in a wall.

It only keeps rules that cause no new problems. Ctrl+Z removes them again.

The traffic problems the designer catches:

| Problem | Fix |
|---|---|
| Lane leads into a dead end | Turn it around, shorten it, or open its far end. |
| Lanes stop robots reaching a station and coming back | Turn a lane around or leave a two-way path. |
| Two overlapping lanes point opposite ways | Make them go the same way. |
| One-robot zone over chargers, a station or rack bays | A robot that works or charges there holds the zone. Use these zones for crossings and doors. |
| Waypoint on a rack or wall, or that robots cannot reach | Move it onto open floor. |

Every rule costs some speed. Run a shift with and without a rule and compare the cycle time and the
**time lost to traffic**.

## 5. Robots

* The four built-in robots are the Tote AMR, the TL-65 scissor lift, the pallet stacker and the inventory drone. Use **−** / **+** to set how many of each.
* To add a robot from another vendor, click **+ Robot type**. Fill in:
  * **Job:** tote carrier, high-shelf picker, pallet mover or flying inventory scanner. TusCore plans the robot exactly like the built-in robot that does the same job.
  * **Width / length:** these decide which aisles it fits (see section 5).
  * **Speed, battery and price:** speed and battery are used in the simulation, and price is used in the cost results.
  * **Protocol:** VDA 5050, REST, TCP/JSON or MAVLink-style. The robot's commands go through that vendor adapter, and you can see them in **Live → Protocol**.
* Leave **Save to my robot library** ticked to reuse the type in other layouts with **Add a saved robot type…**.
* Every saved layout keeps its own copy of each robot type it uses, so old versions always run the same way.

## 6. Problems

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

## 7. Save, versions and running

* **Save** creates a new version every time: v1, v2, and so on. Nothing is overwritten. Pick an older version from the version list to open it.
* The examples are read-only. Saving one makes your own copy.
* If you close the browser by mistake, your unsaved drawing comes back the next time you open the Layout page.
* **Run shift** saves the layout, turns it into the simulation map and replaces the running shift. Robots start in the charging area. The **Live** page shows the twin in 3D or 2D.
* The **Compare**, **What-if** and **Analytics** pages work on your layout the same way they work on the examples.

## 8. Logging in and users

* **First start:** log in as `admin` / `admin` or `operator` / `operator`. You must choose a new password straight away.
* **Operator:** watches the fleet, acknowledges alarms, sends orders and missions, pauses the fleet, closes aisles,
  and sends robots to charge or repair.
* **Admin:** everything an operator does, plus layouts, starting shifts, users, WMS settings and fault tests. Buttons
  an operator cannot use are hidden.
* **Your menu** (your name, top right): change your password, log out. Admins also get **Users and audit log**:
  * add a user;
  * change a role;
  * switch an account off;
  * reset a password;
  * see who did what and when.

## 9. Alarms

The bell in the top bar shows how many alarms nobody has acknowledged yet:
* red and pulsing: something critical;
* amber: something major;
* plain amber outline: everything is acknowledged but still open.

Click the bell to see the list, then **Acknowledge** one or **Acknowledge all**.

| Alarm | Level |
|---|---|
| Robot out of service, vendor offline, collision, permanent deadlock | Critical |
| Battery critical, orders past the SLA, fleet paused | Major |
| Robot stuck in traffic for 60 s, order backlog growing, robots waiting for a charger, mission cannot run | Minor |
| Aisle closed for maintenance | Info |

An alarm that clears by itself before anyone acknowledges it stays on the list, crossed through, until someone
acknowledges it, so nothing is missed. **History > Alarms** shows the alarms of any shift and who acknowledged each one.

## 10. Missions

A mission is a list of steps one robot does in order. For example: load a tote at A3-05, wait 20 s at
"Wait A", unload at packing 1. Open **Missions**:

1. Pick **Start from…** for a ready-made example, or fill in the form.
2. Choose the **Robot**: any robot that does the job, a robot type, or one named robot.
3. Set the **Priority**. Urgent goes before everything else waiting.
4. Set **Runs** (0 = until stopped) and the **minutes between run starts**.
5. Add **Steps**. **Where** can be:
   * a waypoint you drew;
   * a zone (`packing_1`, `packing`, `dock`, `pallets`, `charging`);
   * a rack bay (`A3-05`);
   * `x,y` in metres.

   The field suggests every place on the current site.
6. Click **Create and start**.

Each run is a normal fleet order: it waits for a free robot, follows the traffic rules, and gets another robot if
its robot breaks down. Each mission card shows:
* its progress through the steps;
* the robot doing it;
* runs done;
* the last run time;
* when the next run starts.

**Pause**, **Run now**, **Stop** (cancels an open run) and **Delete** are on the card. If you change the layout,
missions are checked against the new site; one that no longer fits says why.

## 11. WMS connection (API page)

A warehouse management system (WMS) sends order lines to TusCore and gets the status back. Details are in `docs/API.md`.

* **Mock WMS** (admin): switch it on to have a built-in WMS send order waves every few minutes. While it is on, the
  built-in order generator is off. Use it to show how a real WMS would work with the fleet.
* **API key** (admin): **Show** displays the key the WMS sends in the `X-API-Key` header. **New key** replaces it, and
  the old key stops working.
* **Webhooks** (admin): add the WMS address that should receive every status change.
* The table lists the latest WMS order lines with their priority, status and cycle time. A rejected line shows its
  reason when you point at its status.
* On the **Live** page, orders from the WMS carry a WMS badge and missions a MISSION badge. High and urgent orders
  are marked too.

## 12. History and replay

Every shift is recorded in the database (`data/tuscore.db`): robot positions every simulated second, events, orders,
alarms and KPIs. Open **History** and click a shift:

* **Play / Pause**, choose a speed (1× to 300×), or drag the timeline.
* **Events**, **Alarms** and **Orders** on the right follow the timeline. Click a line to jump to that moment.
* A shift that is still running is marked with a green dot and can be replayed up to the current moment.

## 13. Limits in this version

* Shapes are rectangles on a 1 m grid, rotated in 90° steps. One-way lanes and zones follow the same grid.
* All results come from the simulated twin, not from real robots.
