"""Experiments: scenario comparison (separate vendor managers vs TusCore) and what-if runs.

Every run is a full headless shift with its own random seed. The same seeds are used for both
sides of a comparison, so both see exactly the same orders and the same robot faults - the only
difference is how the fleet is managed. Runs execute in parallel on all CPU cores.
"""
from __future__ import annotations
import concurrent.futures as cf
import json
import os
import statistics
import threading
import time
import uuid
from pathlib import Path

from .sim import Simulation

KEYS = ["completed", "created", "completed_in_shift", "orders_per_hour", "avg_wait", "avg_cycle",
        "utilization", "energy_kwh", "collisions", "deadlocks", "permanent_deadlocks",
        "operator_interventions", "traffic_delay_min", "faults", "reassigned"]
# for these a LOWER value is better (used for colouring deltas in the UI)
LOWER_IS_BETTER = {"avg_wait", "avg_cycle", "energy_kwh", "collisions", "deadlocks", "permanent_deadlocks",
                   "operator_interventions", "traffic_delay_min"}


def run_one(args: tuple) -> dict:
    """One full shift. Top-level function so it can run in a worker process."""
    config_path, label, mode, seed, overrides = args
    sim = Simulation(config_path, seed=seed, mode=mode, overrides=overrides)
    k = sim.run_headless()
    row = {"label": label, "mode": mode, "seed": seed, **{key: k[key] for key in KEYS}}
    row["util_by_type"] = k["util_by_type"]
    return row


def summarize(rows: list) -> dict:
    out = {}
    for key in KEYS:
        vals = [r[key] for r in rows]
        out[key] = {"mean": round(statistics.fmean(vals), 2) if vals else 0.0,
                    "std": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0}
    return out


def deltas(a: dict, b: dict) -> dict:
    """Relative change from A to B in percent (positive = B is higher)."""
    out = {}
    for key in KEYS:
        av, bv = a[key]["mean"], b[key]["mean"]
        out[key] = None if av == 0 else round(100.0 * (bv - av) / abs(av), 1)
    return out


class JobManager:
    def __init__(self, results_dir: Path):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict = {}
        self.lock = threading.Lock()
        self.workers = max(1, (os.cpu_count() or 2))
        self._pool = None

    def _executor(self):
        if self._pool is None:
            try:
                self._pool = cf.ProcessPoolExecutor(max_workers=self.workers)
            except (OSError, NotImplementedError):          # restricted environments: fall back to threads
                self._pool = cf.ThreadPoolExecutor(max_workers=self.workers)
        return self._pool

    # ---------------------------------------------------------------- public
    def start(self, kind: str, runs: list, meta: dict) -> str:
        job_id = uuid.uuid4().hex[:8]
        job = {"id": job_id, "kind": kind, "status": "running", "done": 0, "total": len(runs),
               "started": time.time(), "meta": meta, "rows": [], "result": None, "error": None}
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, runs), daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return None if job is None else {k: v for k, v in job.items() if k != "rows"}

    def latest(self, kind: str) -> dict | None:
        p = self.results_dir / f"{kind}_latest.json"
        return json.loads(p.read_text()) if p.exists() else None

    # ---------------------------------------------------------------- internals
    def _run(self, job: dict, runs: list):
        try:
            futures = [self._executor().submit(run_one, a) for a in runs]
            for f in cf.as_completed(futures):
                row = f.result()
                with self.lock:
                    job["rows"].append(row)
                    job["done"] += 1
            result = self._build(job)
            (self.results_dir / f"{job['kind']}_latest.json").write_text(json.dumps(result, indent=1))
            with self.lock:
                job["result"], job["status"] = result, "done"
        except Exception as e:                                # surface the error to the UI instead of hanging
            with self.lock:
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"

    def _build(self, job: dict) -> dict:
        labels = job["meta"]["labels"]                        # e.g. ["A", "B"] or ["baseline", "scenario"]
        groups = {lab: sorted((r for r in job["rows"] if r["label"] == lab), key=lambda r: r["seed"]) for lab in labels}
        summary = {lab: summarize(rows) for lab, rows in groups.items()}
        util = {lab: {t: round(statistics.fmean(r["util_by_type"].get(t, 0) for r in rows), 1)
                      for t in (rows[0]["util_by_type"] if rows else {})} for lab, rows in groups.items()}
        return {"kind": job["kind"], "meta": job["meta"], "finished": time.strftime("%Y-%m-%d %H:%M"),
                "seconds": round(time.time() - job["started"], 1), "summary": summary,
                "delta_pct": deltas(summary[labels[0]], summary[labels[1]]), "util_by_type": util,
                "runs": groups, "lower_is_better": sorted(LOWER_IS_BETTER)}
