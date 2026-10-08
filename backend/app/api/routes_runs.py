import json
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.config import settings
from app.core.run_registry import RunRegistry
from app.schemas.run import RunRequest
from pipeline.fetch import fetch_all
from pipeline.map_flood import map_flood
from pipeline.map_damage import map_damage
from pipeline.map_cutoff import map_cutoff

router = APIRouter(prefix="/runs", tags=["runs"])
registry = RunRegistry(settings.runs_dir)


def _run_fetch(run_id: str, req: RunRequest) -> None:
    try:
        fetch_all(run_id, req.bbox, req.flood_date, req.window_days)
    except Exception as e:
        registry.set_status(run_id, "failed", str(e)[:300])


@router.post("")
def create_run(req: RunRequest, bg: BackgroundTasks):
    run_id = registry.new_run(req.model_dump(mode="json"))
    bg.add_task(_run_fetch, run_id, req)
    return {"run_id": run_id, "status": "created"}


@router.get("/{run_id}")
def get_run(run_id: str):
    status = registry.get_status(run_id)
    if status["status"] == "unknown":
        raise HTTPException(404, "run not found")
    quality_path = settings.runs_dir / run_id / "quality.json"
    quality = json.loads(quality_path.read_text()) if quality_path.exists() else None
    return {"run_id": run_id, **status, "quality": quality}

@router.post("/{run_id}/map")
def map_run(run_id: str, bg: BackgroundTasks):
    req_path = settings.runs_dir / run_id / "request.json"
    if not req_path.exists():
        raise HTTPException(404, "run not found")
    req = json.loads(req_path.read_text())
    bg.add_task(map_flood, run_id, req["bbox"])
    return {"run_id": run_id, "status": "mapping_started"}




@router.post("/{run_id}/damage")
def damage_run(run_id: str, bg: BackgroundTasks):
    req_path = settings.runs_dir / run_id / "request.json"
    if not req_path.exists():
        raise HTTPException(404, "run not found")
    req = json.loads(req_path.read_text())
    bg.add_task(map_damage, run_id, req["bbox"])
    return {"run_id": run_id, "status": "damage_started"}

@router.post("/{run_id}/cutoff")
def cutoff_run(run_id: str, bg: BackgroundTasks):
    req_path = settings.runs_dir / run_id / "request.json"
    if not req_path.exists():
        raise HTTPException(404, "run not found")
    req = json.loads(req_path.read_text())
    bg.add_task(map_cutoff, run_id, req["bbox"])
    return {"run_id": run_id, "status": "cutoff_started"}