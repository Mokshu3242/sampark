import json
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.config import settings
from app.core.run_registry import RunRegistry
from app.schemas.run import RunRequest
from pipeline.fetch import fetch_all

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