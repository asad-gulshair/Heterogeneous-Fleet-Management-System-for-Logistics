# TusCore Fleet Manager (Phase 3)

Digital twin of a 60x40 warehouse with 12 robots (4 types / 4 vendors). Phases 1-2: grid sim, A* planning, orders, capability-based allocation (greedy / Hungarian), live console.

## Quick start (Windows)
Double-click `run.bat` (or run it in a terminal), then open http://localhost:8000.

## Run manually (Windows PowerShell)
```powershell
cd D:\robotics\tuscore\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://localhost:8000 (API docs: http://localhost:8000/docs).

## Headless shift run (no browser)
```powershell
cd D:\robotics\tuscore\backend
python scripts/run_shift.py --allocator greedy
python scripts/run_shift.py --allocator hungarian
```

## Layout
- `config/warehouse.yaml` - map, zones, robot types (edit this, no code changes)
- `backend/app/grid.py` - map + blocked cells
- `backend/app/planner.py` - A*
- `backend/app/robots.py` - common robot model + movement
- `backend/app/tasks.py`, `orders.py` - order model, generator, Poisson arrivals
- `backend/app/allocator.py` - greedy and Hungarian allocation
- `backend/app/traffic.py` + `sim.py` - cell reservation, deadlock handling, faults, charging
- `backend/app/adapters/` - one adapter per vendor protocol (VDA 5050, REST, TCP/JSON, MAVLink-style)
- `backend/tests/` - `python -m pytest -q`
- `backend/app/sim.py` - simulation loop, goto, snapshot
- `backend/app/main.py` - FastAPI REST + WebSocket
- `frontend/index.html` - TusCore Fleet Console (canvas)

## Docs
- `docs/STEPS.md` - step-by-step build log with the reasoning behind each choice
