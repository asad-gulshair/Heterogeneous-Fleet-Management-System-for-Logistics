"""Database (SQLite, one file: data/tuscore.db). No server to install; WAL mode so reads never block the recorder.

    users, sessions      login (operator / admin)
    settings             small key/value settings (WMS API key, webhooks)
    shifts               one row per shift: site, mode, map, final KPIs, KPI history
    frames               robot positions once per simulated second, zlib-compressed (shift replay)
    events               event log per shift
    orders               every finished / cancelled order per shift
    alarms               alarm history (raised, acknowledged by whom, cleared)
    audit                who did what (logins, pauses, layout changes ...)
    missions             mission templates
    wms_orders           final state of every WMS order
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL,
    role TEXT NOT NULL, created REAL, active INTEGER DEFAULT 1, must_change INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL, expires REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY, started REAL, ended REAL, site TEXT, layout TEXT, mode TEXT,
    allocator TEXT, seed INTEGER, robots TEXT, map TEXT, kpis TEXT, history TEXT, sim_end REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS frames(shift_id INTEGER, t REAL, data BLOB);
CREATE INDEX IF NOT EXISTS frames_st ON frames(shift_id, t);
CREATE TABLE IF NOT EXISTS events(shift_id INTEGER, t REAL, sev TEXT, kind TEXT, msg TEXT);
CREATE INDEX IF NOT EXISTS events_st ON events(shift_id, t);
CREATE TABLE IF NOT EXISTS orders(shift_id INTEGER, task_id INTEGER, ref TEXT, kind TEXT, title TEXT, source TEXT,
    priority INTEGER, created REAL, started REAL, finished REAL, status TEXT, robots TEXT);
CREATE INDEX IF NOT EXISTS orders_s ON orders(shift_id);
CREATE TABLE IF NOT EXISTS alarms(id INTEGER PRIMARY KEY, shift_id INTEGER, code TEXT, subject TEXT, level TEXT,
    title TEXT, detail TEXT, raised REAL, raised_wall REAL, acked_by TEXT, acked_wall REAL, cleared REAL, state TEXT);
CREATE INDEX IF NOT EXISTS alarms_s ON alarms(shift_id);
CREATE TABLE IF NOT EXISTS audit(wall REAL, user TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS missions(id INTEGER PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS wms_orders(ref TEXT PRIMARY KEY, shift_id INTEGER, data TEXT);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
        self.shift_id: int | None = None

    def q(self, sql: str, args=()) -> list:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.db.execute(sql, args).lastrowid

    def many(self, sql: str, rows: list):
        if rows:
            with self.lock:
                self.db.execute("BEGIN")
                self.db.executemany(sql, rows)
                self.db.execute("COMMIT")

    # ------------------------------------------------------------ settings
    def get(self, key: str, default=None):
        r = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set(self, key: str, value: str):
        self.x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_json(self, key: str, default=None):
        v = self.get(key)
        return json.loads(v) if v else default

    def set_json(self, key: str, value):
        self.set(key, json.dumps(value))

    # ------------------------------------------------------------ audit
    def audit(self, user: str, action: str, detail: str = ""):
        self.x("INSERT INTO audit VALUES(?,?,?,?)", (time.time(), user or "-", action, detail[:500]))

    def audit_log(self, limit: int = 200) -> list:
        return self.q("SELECT * FROM audit ORDER BY wall DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ shifts + replay
    def start_shift(self, info: dict, robots: list, map_: dict) -> int:
        self.shift_id = self.x(
            "INSERT INTO shifts(started, site, layout, mode, allocator, seed, robots, map, kpis, history) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), info.get("site"), info.get("layout"), info.get("mode"), info.get("allocator"), info.get("seed"),
             json.dumps(robots), json.dumps(map_), "{}", "[]"))
        return self.shift_id

    def update_shift(self, sid: int, kpis: dict, history: list, sim_end: float, ended: bool = False):
        self.x("UPDATE shifts SET kpis=?, history=?, sim_end=?, ended=COALESCE(ended, ?) WHERE id=?",
               (json.dumps(kpis), json.dumps(history), sim_end, time.time() if ended else None, sid))

    def shifts(self, limit: int = 50) -> list:
        rows = self.q("SELECT id, started, ended, site, layout, mode, allocator, seed, kpis, sim_end FROM shifts "
                      "ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            k = json.loads(r.pop("kpis") or "{}")
            r["kpis"] = {x: k.get(x) for x in ("completed", "created", "avg_cycle", "collisions", "permanent_deadlocks", "orders_per_hour")}
        return rows

    def shift(self, sid: int) -> dict:
        r = self.q("SELECT * FROM shifts WHERE id=?", (sid,))
        if not r:
            raise KeyError(sid)
        r = r[0]
        for k in ("robots", "map", "kpis", "history"):
            r[k] = json.loads(r[k] or "null")
        r["alarms"] = self.q("SELECT * FROM alarms WHERE shift_id=? ORDER BY raised", (sid,))
        r["events"] = self.q("SELECT t, sev, kind, msg FROM events WHERE shift_id=? ORDER BY t, rowid", (sid,))
        r["orders"] = self.q("SELECT task_id, ref, kind, title, source, priority, created, started, finished, status, robots "
                             "FROM orders WHERE shift_id=? ORDER BY finished", (sid,))
        return r

    def add_frames(self, sid: int, frames: list):
        self.many("INSERT INTO frames VALUES(?,?,?)",
                  [(sid, t, zlib.compress(json.dumps(d, separators=(",", ":")).encode(), 6)) for t, d in frames])

    def frames(self, sid: int, t0: float, t1: float) -> list:
        rows = self.q("SELECT t, data FROM frames WHERE shift_id=? AND t>=? AND t<=? ORDER BY t", (sid, t0, t1))
        return [[r["t"], json.loads(zlib.decompress(r["data"]))] for r in rows]

    def add_events(self, sid: int, events: list):
        self.many("INSERT INTO events VALUES(?,?,?,?,?)", [(sid, e["t"], e["sev"], e["kind"], e["msg"]) for e in events])

    def add_orders(self, sid: int, rows: list):
        self.many("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [(sid,) + tuple(r) for r in rows])

    def delete_shift(self, sid: int):
        for t in ("frames", "events", "orders", "alarms"):
            self.x(f"DELETE FROM {t} WHERE shift_id=?", (sid,))
        self.x("DELETE FROM shifts WHERE id=?", (sid,))

    def prune(self, keep: int = 30):
        """Keep the replay data of the last `keep` shifts (frames are the big part)."""
        old = self.q("SELECT id FROM shifts ORDER BY id DESC LIMIT -1 OFFSET ?", (keep,))
        for r in old:
            self.x("DELETE FROM frames WHERE shift_id=?", (r["id"],))

    # ------------------------------------------------------------ alarms
    def alarm_raise(self, a: dict) -> int:
        return self.x("INSERT INTO alarms(shift_id, code, subject, level, title, detail, raised, raised_wall, state) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (self.shift_id, a["code"], a["subject"], a["level"], a["title"],
                                                    a["detail"], a["raised"], time.time(), a["state"]))

    def alarm_update(self, a: dict):
        self.x("UPDATE alarms SET state=?, detail=?, acked_by=?, acked_wall=?, cleared=? WHERE id=?",
               (a["state"], a["detail"], a.get("acked_by"), a.get("acked_wall"), a.get("cleared"), a["id"]))

    def alarm_history(self, limit: int = 200) -> list:
        return self.q("SELECT a.*, s.site FROM alarms a LEFT JOIN shifts s ON s.id=a.shift_id ORDER BY a.id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ missions + WMS
    def load_missions(self) -> list:
        return [json.loads(r["data"]) for r in self.q("SELECT data FROM missions ORDER BY id")]

    def save_mission(self, m: dict):
        keep = {k: v for k, v in m.items() if k not in ("open_task",)}
        self.x("INSERT INTO missions(id, data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
               (m["id"], json.dumps(keep)))

    def delete_mission(self, mid: int):
        self.x("DELETE FROM missions WHERE id=?", (mid,))

    def save_wms(self, o: dict):
        self.x("INSERT INTO wms_orders(ref, shift_id, data) VALUES(?,?,?) ON CONFLICT(ref) DO UPDATE SET data=excluded.data, "
               "shift_id=excluded.shift_id", (o["ref"], self.shift_id, json.dumps(o)))

    def wms_lookup(self, ref: str) -> dict | None:
        r = self.q("SELECT data FROM wms_orders WHERE ref=?", (ref,))
        return json.loads(r[0]["data"]) if r else None
