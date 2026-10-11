import logging
from datetime import date

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import fetch, map_cutoff, map_damage, map_flood

log = logging.getLogger("flood-watch.run_all")


def run_pipeline(run_id: str, bbox: list[float], flood_date: date, window_days: int = 30) -> dict:
    """Runs Phase 1 to 4 in order. Each phase writes its own files to the run folder."""
    registry = RunRegistry(settings.runs_dir)

    if isinstance(flood_date, str):
        flood_date = date.fromisoformat(flood_date)

    stages = [
        ("fetching", "Downloading satellite data",
         lambda: fetch.fetch_all(run_id, bbox, flood_date, window_days)),
        ("mapping", "Detecting flood and debris",
         lambda: map_flood.map_flood(run_id, bbox)),
        ("damage", "Checking buildings, roads and bridges",
         lambda: map_damage.map_damage(run_id, bbox)),
        ("cutoff", "Finding cut-off villages and repair priority",
         lambda: map_cutoff.map_cutoff(run_id, bbox)),
    ]

    summary = {}
    for stage, label, fn in stages:
        registry.set_status(run_id, stage, label)
        log.info(f"[{run_id}] {stage}: {label}")
        try:
            summary[stage] = fn()
        except Exception as e:
            log.exception(f"[{run_id}] {stage} failed")
            registry.set_status(run_id, "failed", f"{stage}: {str(e)[:200]}")
            return {"failed_at": stage, "error": str(e)[:200]}

    try:
        from pipeline import hydrology
        hydrology.build_hydro(settings.runs_dir / run_id)
    except Exception as e:                      # bonus feature: never fail the run for it
        log.warning(f"Flow cache not built: {e}")

    registry.set_status(run_id, "done", "Pipeline complete")

    try:
        from app.core import storage
        storage.upload_run(run_id)              # free hosts wipe the disk; keep a copy
    except Exception as e:                      # never fail a finished run over storage
        log.warning(f"Could not store run {run_id} in Mongo: {e}")
    return summary