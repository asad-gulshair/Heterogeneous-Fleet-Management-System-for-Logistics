"""Stand-alone mock WMS: talks to TusCore over HTTP exactly like a real warehouse management system would.

    python backend/scripts/mock_wms.py --key <WMS API key> [--url http://localhost:8000] [--lines-per-min 6] [--minutes 10]

It sends order lines (POST /api/wms/orders), retries a failed send with the SAME ref (safe: TusCore is
idempotent), follows every status change through the event feed (GET /api/wms/events) and prints a summary.
The WMS API key is on the API page of the console (admin). Uses only the Python standard library.
"""
import argparse
import json
import random
import time
import urllib.error
import urllib.request


def call(url, key, method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--key", required=True, help="WMS API key (console > API > WMS connection)")
    ap.add_argument("--lines-per-min", type=float, default=6.0, help="order lines per real minute")
    ap.add_argument("--minutes", type=float, default=10.0, help="how long to run")
    ap.add_argument("--racks", default="A1,A2,A3,A4,A5,A6", help="rack names on the site")
    ap.add_argument("--bays", type=int, default=20)
    a = ap.parse_args()
    rng = random.Random(7)
    racks = a.racks.split(",")
    base = a.url.rstrip("/")
    st, ev = call(base + "/api/wms/events?after=0", a.key)
    if st != 200:
        raise SystemExit(f"TusCore answered {st}: {ev}. Check --url and --key.")
    last, sent, status, n = ev["last"], 0, {}, 0
    gap, end, next_send = 60.0 / a.lines_per_min, time.time() + a.minutes * 60, time.time()
    print(f"Mock WMS -> {base}: {a.lines_per_min} lines/min for {a.minutes} min. Ctrl+C to stop.")
    try:
        while time.time() < end or any(s not in ("done", "cancelled", "rejected") for s in status.values()):
            if time.time() >= next_send and time.time() < end:
                n += 1
                typ = rng.choices(["box_pick", "high_pick", "pallet_move", "inventory_scan"], [70, 15, 10, 5])[0]
                body = {"ref": f"SO-{int(time.time()) % 100000:05d}-{n}", "type": typ,
                        "priority": rng.choices(["normal", "high", "urgent", "low"], [85, 10, 2, 3])[0]}
                if typ in ("box_pick", "high_pick"):
                    body["location"] = f"{rng.choice(racks)}-{rng.randint(1, a.bays):02d}"
                for attempt in range(3):                 # retry with the same ref: never a duplicate order
                    try:
                        st, o = call(base + "/api/wms/orders", a.key, "POST", body)
                        break
                    except OSError:
                        time.sleep(1)
                else:
                    continue
                sent += 1
                status[body["ref"]] = o.get("status", "rejected") if isinstance(o, dict) else "rejected"
                print(f"  sent {body['ref']:<16} {typ:<15} {body.get('location', ''):<7} {body['priority']:<7} -> {st} {status[body['ref']]}"
                      + (f" ({o.get('detail')})" if st >= 400 else ""))
                next_send += gap
            st, ev = call(base + f"/api/wms/events?after={last}", a.key)
            for e in ev["events"]:
                if e["ref"] in status:
                    status[e["ref"]] = e["status"]
                    if e["status"] in ("done", "cancelled"):
                        print(f"  {e['ref']:<16} {e['status']}" + (f" in {e['cycle']:.0f} s (sim)" if e.get("cycle") else "")
                              + (f" by {', '.join(e['robots'].values())}" if e.get("robots") else ""))
            last = ev["last"]
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    by = {}
    for s in status.values():
        by[s] = by.get(s, 0) + 1
    print(f"\nSent {sent} lines: " + ", ".join(f"{v} {k}" for k, v in sorted(by.items())))


if __name__ == "__main__":
    main()
