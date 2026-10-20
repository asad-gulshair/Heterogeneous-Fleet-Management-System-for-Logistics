# TusCore Fleet Manager (Phase 1)

Digital twin of a 60x40 warehouse with 12 robots (4 types / 4 vendors). Phase 1: grid sim, A* planning, live browser view.

## Run (Windows PowerShell)
```powershell
cd D:\robotics\tuscore\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://localhost:8000 (API docs: http://localhost:8000/docs).

## Layout
- `config/warehouse.yaml` - map, zones, robot types (edit this, no code changes)
- `backend/app/grid.py` - map + blocked cells
- `backend/app/planner.py` - A*
- `backend/app/robots.py` - common robot model + movement
- `backend/app/sim.py` - simulation loop, goto, snapshot
- `backend/app/main.py` - FastAPI REST + WebSocket
- `frontend/index.html` - TusCore Fleet Console (canvas)

## Docs
- `docs/STEPS.md` - step-by-step build log with the reasoning behind each choice
