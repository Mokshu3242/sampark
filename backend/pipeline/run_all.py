import logging
from datetime import date

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import cancel, fetch, map_cutoff, map_damage, map_flood

log = logging.getLogger("flood-watch.run_all")


def _stopped(registry: RunRegistry, run_id: str, where: str) -> dict:
    log.info(f"[{run_id}] stopped by user before {where}")
    registry.set_status(run_id, "cancelled", f"Stopped by user before {where}")
    return {"cancelled_at": where}


def build_caches(run_id: str) -> None:
    """Pre-make everything the web app would otherwise build on first view.
    Runs on the GitHub worker, so the 512 MB server only sends finished files."""
    run_dir = settings.runs_dir / run_id
    try:
        from pipeline.hydrology import damaged_roads_path
        damaged_roads_path(run_dir)
    except Exception as e:
        log.warning(f"Damaged-roads cache not built: {e}")
    try:
        from app.api.routes_runs import _roads_web
        _roads_web(run_dir)
    except Exception as e:
        log.warning(f"Web roads cache not built: {e}")
    try:
        from app.api.routes_images import LAYERS, PRERENDER, cached_image
        for name, widths in PRERENDER.items():
            if (run_dir / LAYERS[name]["file"]).exists():
                for w in widths:
                    cached_image(run_id, name, w)
    except Exception as e:
        log.warning(f"Image cache not built: {e}")
    try:
        from pipeline.prebuild import build_all
        build_all(run_id, run_dir)              # reports, story flow, chip colours
    except Exception as e:
        log.warning(f"Prebuild not done: {e}")


def run_pipeline(run_id: str, bbox: list[float], flood_date: date, window_days: int = 30) -> dict:
    """Entry point. Marks the run as live in this process, so the API knows it's really running."""
    cancel.ACTIVE.add(run_id)
    try:
        return _run_pipeline(run_id, bbox, flood_date, window_days)
    finally:
        cancel.ACTIVE.discard(run_id)


def _run_pipeline(run_id: str, bbox: list[float], flood_date: date, window_days: int = 30) -> dict:
    """Runs Phase 1 to 4 in order. Each phase writes its own files to the run folder.
    Checks for a Stop request between stages."""
    registry = RunRegistry(settings.runs_dir)
    run_dir = settings.runs_dir / run_id

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
        if cancel.requested(run_dir):
            return _stopped(registry, run_id, stage)

        registry.set_status(run_id, stage, label)
        log.info(f"[{run_id}] {stage}: {label}")
        try:
            summary[stage] = fn()
        except cancel.Cancelled:
            return _stopped(registry, run_id, stage)
        except Exception as e:
            if cancel.requested(run_dir):       # a stop during a stage can surface as an error
                return _stopped(registry, run_id, stage)
            log.exception(f"[{run_id}] {stage} failed")
            registry.set_status(run_id, "failed", f"{stage}: {str(e)[:200]}")
            return {"failed_at": stage, "error": str(e)[:200]}

    if cancel.requested(run_dir):
        return _stopped(registry, run_id, "flow cache")

    registry.set_status(run_id, "cutoff", "Building flow and map caches")
    try:
        from pipeline import hydrology
        hydrology.build_hydro(run_dir)
    except Exception as e:                      # bonus feature: never fail the run for it
        log.warning(f"Flow cache not built: {e}")
    build_caches(run_id)

    if cancel.requested(run_dir):
        return _stopped(registry, run_id, "saving")

    registry.set_status(run_id, "done", "Pipeline complete")

    try:
        from app.core import storage
        storage.upload_run(run_id)              # free hosts wipe the disk; keep a copy
    except Exception as e:                      # never fail a finished run over storage
        log.warning(f"Could not store run {run_id}: {e}")
    return summary

_build_caches = build_caches                     # old name, kept for other imports