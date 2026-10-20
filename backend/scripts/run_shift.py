"""Run a full simulated shift headlessly and print the KPIs.

    python scripts/run_shift.py --allocator greedy --seed 42
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.sim import Simulation

ap = argparse.ArgumentParser()
ap.add_argument("--allocator", default="hungarian", choices=["greedy", "hungarian"])
ap.add_argument("--seed", type=int, default=None)
ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "..", "..", "config", "warehouse.yaml"))
a = ap.parse_args()

sim = Simulation(a.config, seed=a.seed, allocator=a.allocator)
t0 = time.time()
k = sim.run_headless()
print(f"allocator={a.allocator} seed={sim.seed} sim_time={sim.sim_time/3600:.2f}h wall={time.time()-t0:.1f}s")
for key, v in k.items():
    print(f"  {key}: {v}")
