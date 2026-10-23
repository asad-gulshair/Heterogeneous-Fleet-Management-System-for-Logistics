# From digital twin to a live site

How TusCore runs many robots from many vendors on one platform - and what changes when the
simulated robots are replaced by real ones.

## 1. Architecture on a real site

```
 WMS / ERP ──REST──▶ ┌──────────────── TusCore (edge server on site, Docker) ────────────────┐
 operators ──web──▶  │ order queue → allocation → route planning + cell reservation → execution │
                     │        ▲ live robot state                  │ commands              │
                     │        │                    vendor adapters (one per protocol)     │
                     └────────┼───────────────────────────────────┼───────────────────────┘
                              │                                   │
        VDA 5050 over MQTT ◀──┴──▶ Tote AMRs (Vendor A)      REST ◀──▶ Scissor lifts (Tusmec)
        TCP/JSON           ◀─────▶ Pallet stackers (Vendor C)  MAVLink ◀──▶ Inventory drones (Vendor D)
```

* **One core, many adapters.** The fleet manager only knows one robot model (position, state, battery,
  capabilities). Each vendor gets an adapter that translates TusCore commands to its protocol and its status
  messages back. In the twin those adapters talk to simulated endpoints; on site they talk to the real robot
  controllers - the core does not change.
* **Robots keep their own safety.** Each robot's onboard safety (scanners, e-stop) stays in charge of
  physical safety. TusCore coordinates at the traffic level: who drives where, which cell or aisle segment is
  reserved, who waits and who yields - so robots of different vendors stop meeting head-on.
* **The twin becomes the live view.** In live mode the 3D view is driven by telemetry from the adapters
  (positions, states, batteries) instead of the simulator.

## 2. What it does in real time

| Situation on the floor | What TusCore does |
|---|---|
| New order from the WMS | picks the best free robot with the right capability (any vendor), plans the route, reserves cells |
| Order needs two robot types | dispatches both and synchronises the hand-over (lift → tote) |
| Two robots block each other | detects the wait-for cycle, the robot with the lowest priority yields or detours |
| A robot breaks down mid-order | stops it, frees its partner, re-queues the order, another robot takes it |
| A vendor's controller goes offline | alerts, sends no new missions to that vendor, the rest of the fleet keeps working |
| Aisle closed for maintenance | routes around it, holds orders located there until it re-opens |
| Emergency | one click pauses every robot of every vendor |
| Batteries | staggered charging so a robot type never charges all at once |
| Congestion | heatmap shows where robots lose time - input for layout changes |

## 3. Scale

* Demo warehouse: 12 robots, 4 vendors. Regional DC: 19 robots. **Mega DC: 40 robots** - same code, only the
  layout YAML differs. On the 40-robot site a full 8 h shift completes ~4,100-4,250 orders with zero collisions
  and zero permanent deadlocks (headless runs in `results/comparison_xl_layout.json`).
* A shift of 40 robots simulates in well under a minute, so "what if we add 6 robots / close an aisle /
  get 30 % more orders" can be answered before buying hardware.

## 4. What is needed to go live (next steps)

1. Connect the adapters to the real controllers (VDA 5050 broker, vendor REST endpoints, TCP gateway, drone link)
   and feed live telemetry into the twin.
2. Import the real site map (CAD / robot maps) into the layout YAML; align coordinate frames per vendor.
3. Site acceptance: run the comparison and what-if with the site's real order profile.
4. Production hardening: user login and roles, audit log, high availability, TLS on the broker and API.
5. Optional: ROS 2 bridge, VDA 5050 conformance testing.

All figures in the console come from the simulation and should be validated on site.
