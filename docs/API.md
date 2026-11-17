# TusCore API

Every endpoint is listed with its request and response schema at **http://localhost:8000/docs**, an
interactive page where you can try each call. This document covers what that page does not:
* how to log in;
* how a WMS connects;
* what the order statuses mean;
* the request and response formats for missions, alarms and replay.

## 1. Authentication

| Who | How | Can reach |
|---|---|---|
| Console users | `POST /api/auth/login` `{"username", "password"}` sets an HttpOnly cookie. Scripts can send the returned `token` as `Authorization: Bearer <token>` instead. | Everything their role allows |
| WMS | Header `X-API-Key: <key>`. An admin finds the key on the API page and can make a new one there. | `/api/wms/orders*` and `/api/wms/events` only |

**Roles**
* **Operator:** read everything, acknowledge alarms, submit orders and missions, pause the fleet, close aisles,
  send robots to a cell, to charge or to repair, change the simulation speed.
* **Admin:** everything the operator can do, plus layouts and running shifts, users and the audit log, fault
  injection, vendor outages, WMS settings and experiments.

**Responses**
* `401`: not logged in, or a missing or wrong API key.
* `403`: the action needs the admin role.

**Security details**
* Passwords are stored as PBKDF2-SHA256 hashes with 200 000 rounds.
* After 5 wrong passwords, that user name is locked for 60 s.
* Sessions last 12 h and are renewed while in use.
* Every change (any POST, PUT, PATCH or DELETE) is written to the audit log (`GET /api/audit`, admin) with the user's name.

## 2. WMS integration

A WMS sends **order lines** with its own reference. TusCore turns each line into a fleet order, assigns
robots, runs it and reports every status change back.

### Send an order line
```http
POST /api/wms/orders
X-API-Key: wms_...
Content-Type: application/json

{"ref": "SO-1001-1", "type": "box_pick", "location": "A3-05", "destination": "packing_2", "priority": "high"}
```

| Field | Values |
|---|---|
| `ref` | Your unique reference, 1–64 characters: letters, digits, `_ . : - /` |
| `type` | `box_pick`, `high_pick` (scissor lift and tote robot), `pallet_move`, `inventory_scan`, `transport` (A to B) |
| `location` | Rack bay `A3-05` for picks. Rack `A3` for a scan (a bay is accepted too). The place a pallet is taken from. The from-place for a transport. |
| `destination` | Optional. Packing station (`packing_2`), dock, waypoint (`Wait A`) or `x,y`. Required for a transport. |
| `priority` | `low`, `normal` (default), `high`, `urgent`. One level is worth 5 minutes of waiting in the queue. |
| `load` | Transport only: `tote` (default) or `pallet` |

**Answers**
* **`201`:** a new order.
* **`200`:** the same `ref` was sent before, and you get that first order back. TusCore is **idempotent**: a WMS
  that retries after a timeout never creates two robot jobs.
* **`422`:** the line cannot be done, with a reason. Examples: an unknown rack bay; a bay whose aisle the
  scissor lift does not fit; no robot for that job on this site. The line is stored with status `rejected`,
  so it appears in the status feed too.

### Order statuses
```
queued -> in_progress -> done
   ^           |
   +-----------+   robot fault: the order goes back to queued and gets another robot (reason given)
queued / in_progress -> cancelled      (DELETE /api/wms/orders/{ref}, or the shift was restarted)
rejected                               (could not be accepted, see reason)
```

**One status record** (`GET /api/wms/orders/{ref}`):
```json
{"ref": "SO-1001-1", "type": "box_pick", "status": "done", "priority": 2, "task_id": 412,
 "robots": {"carry_tote": "tote_amr_3"}, "received": 3601.2, "started": 3603.0, "finished": 3712.4,
 "cycle": 111.2, "reason": null, "history": [[3601.2, "queued"], [3603.0, "in_progress"], [3712.4, "done"]]}
```
Times are seconds since the start of the shift.

### Getting status changes back

**1. Poll the event feed.** No setup needed.
```http
GET /api/wms/events?after=0
-> {"events": [{"seq": 1, "ref": "SO-1001-1", "status": "queued", "t": 3601.2, "robots": {}, "reason": null, "cycle": null}, ...],
    "last": 57}
```
Call it again with `after=<last>`. Events come in order and none are skipped. Save `last` on your side, so a
WMS restart continues where it stopped.

**2. Webhooks** (push). An admin adds a URL on the API page or with `POST /api/wms/webhooks {"url": "https://..."}`.
* Each event above is POSTed to it as JSON.
* A failed delivery is tried 3 times. Counts of delivered and failed events are shown on the API page.
* If your endpoint was down, catch up with the event feed: webhooks are the fast path, the feed is the guarantee.

### Mock WMS
* **Built in:** API page > WMS connection > **Mock WMS** (admin). It releases order waves every 2, 5 or 15
  minutes through the same gateway a real WMS uses:
  * about 20 % of the bays get about 80 % of the picks (ABC profile);
  * 10 % of lines are high priority and 2 % urgent;
  * inventory scans are low priority.
  
  While it is on, the built-in order generator is off.
* **Stand-alone, over HTTP:** `python backend/scripts/mock_wms.py --key <key> --lines-per-min 6 --minutes 10`.
  It sends lines, retries them with the same reference, follows the event feed and prints a summary.

## 3. Missions

Each run of a mission is one fleet order. That means allocation, traffic rules, charging and re-assignment after
a fault all work exactly as for every other order.

```http
POST /api/missions
{"name": "Replenish packing 1", "robot": {"type": "tote_amr"}, "priority": "high", "repeat": 0, "every_min": 10,
 "steps": [{"to": "A3-05", "do": "load"}, {"to": "Wait A", "do": "wait", "wait_s": 20}, {"to": "packing_1", "do": "unload"}]}
```

**`robot`:** one of
* `{"role": "carry_tote" | "high_pick" | "move_pallet" | "scan"}`: any robot that does the job;
* `{"type": "tote_amr"}`: any robot of that type;
* `{"robot": "tote_amr_3"}`: that one robot.

**`do`:** `go`, `load`, `unload`, `lift` (pallet), `set` (pallet down), `scan`, `wait` (`wait_s`).

**`to`:** a waypoint, a zone (`packing_1`, `packing`, `dock`, `pallets`, `charging`), a rack bay (`A3-05`) or `x,y`.

**Repeating:** `repeat` is the number of runs; `0` means until stopped. `every_min` is the minutes between run
starts; a run never starts while the previous one is still open.

**Control:** `POST /api/missions/{id}/pause | resume | stop | run_now` and `DELETE /api/missions/{id}`. Stop and
delete cancel an open run. `GET /api/missions/targets` lists the waypoints, zones and pickable bays of the current site.

## 4. Alarms
* **Read:** `GET /api/alarms` returns open alarms, most urgent first, plus a summary
  `{"open", "unacked", "worst"}`. The live WebSocket carries the same data as `alarms` and `alarm_summary`.
* **Acknowledge:** `POST /api/alarms/{id}/ack` or `POST /api/alarms/ack-all`.
* **History:** `GET /api/alarms/history` covers all shifts, with who acknowledged each alarm and when.

**Levels:** `critical`, `major`, `minor`, `info`.

**Codes:** `robot_fault`, `battery_critical`, `robot_stuck` (60 s or more in traffic), `vendor_offline`,
`fleet_paused`, `aisle_closed`, `orders_late` (past SLA), `backlog`, `charger_shortage`, `mission_invalid`,
`collision`, `deadlock`.

**States:**

| State | Meaning |
|---|---|
| `active_unacked` | The condition is there and nobody has acknowledged it yet. |
| `active_acked` | Acknowledged; the alarm closes when the condition clears. |
| `cleared_unacked` | The condition went away before anyone acknowledged it. It stays on the list until acknowledged, so nothing is missed. |

## 5. Shift history and replay
* `GET /api/shifts`: recorded shifts with their headline KPIs.
* `GET /api/shifts/{id}`: map, robot list, final KPIs, KPI history per minute, events, orders and alarms.
* `GET /api/shifts/{id}/frames?t0=0&t1=300`: one frame per simulated second (at most 900 s per call):
  `[t, [[x, y, state, battery, load, blocked_s, order_id], ...]]`, with robots in the order of the shift's robot list.

Data is kept in `data/tuscore.db` (SQLite). Replay frames are kept for the last 30 shifts; KPIs, events,
orders and alarms are kept for all shifts.
