"""Run a full simulated shift headlessly and print the KPIs.

    python scripts/run_shift.py --mode separate --seed 3
    python scripts/run_shift.py --layout warehouse_large.yaml
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.sim import Simulation

ap = argparse.ArgumentParser()
ap.add_argument("--allocator", default="hungarian", choices=["greedy", "hungarian"])
ap.add_argument("--seed", type=int, default=None)
ap.add_argument("--mode", default="tuscore", choices=["tuscore", "separate"], help="one TusCore manager or one manager per vendor")
ap.add_argument("--layout", default="warehouse.yaml", help="file name in the config folder")
a = ap.parse_args()

sim = Simulation(os.path.join(os.path.dirname(__file__), "..", "..", "config", a.layout), seed=a.seed, allocator=a.allocator, mode=a.mode)
t0 = time.time()
k = sim.run_headless()
print(f"mode={a.mode} layout={a.layout} allocator={a.allocator} seed={sim.seed} sim_time={sim.sim_time/3600:.2f}h wall={time.time()-t0:.1f}s")
for key, v in k.items():
    print(f"  {key}: {v}")
