"""Add the ready-made files (reports, story flow, chip colours, image caches) to runs
that already exist, then save them back to MongoDB and B2.

Runs on GitHub Actions (.github/workflows/rebuild.yml). Locally:
    RUN_IDS=all python -m scripts.rebuild_caches
    RUN_IDS=20261008T063513-021f3e python -m scripts.rebuild_caches
"""
import logging
import os
import sys

from app.core import storage
from app.core.config import settings
from pipeline.run_all import build_caches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rebuild")

# Old copies are removed so they are made again with the new code.
REDO = ("report_en.html", "report_ne.html", "report_en.pdf", "report_ne.pdf",
        "report_map.png", "story_flow.json")


def rebuild(run_id: str) -> bool:
    run_dir = settings.runs_dir / run_id
    if not storage.restore_run(run_id):
        log.error(f"{run_id}: not found in MongoDB or B2")
        return False
    if not (run_dir / "cutoff_stats.json").exists():
        log.warning(f"{run_id}: not a finished run, skipped")
        return False
    for name in REDO:
        (run_dir / name).unlink(missing_ok=True)
    for f in run_dir.glob("stretch_*.json"):
        f.unlink(missing_ok=True)

    build_caches(run_id)
    if not (run_dir / "story_flow.json").exists():
        log.info(f"{run_id}: flow data missing, building it")
        try:
            from pipeline import hydrology
            from pipeline.prebuild import build_story_flow
            hydrology.build_hydro(run_dir)
            build_story_flow(run_dir)
        except Exception as e:
            log.warning(f"{run_id}: flow still not built: {e}")

    made = sorted(p.name for p in run_dir.iterdir()
                  if p.name.startswith(("report_", "story_flow", "stretch_", "cache_")))
    log.info(f"{run_id}: ready-made files: {made}")
    storage.upload_run(run_id)
    log.info(f"{run_id}: saved to storage")
    return True


def main() -> None:
    if not storage.enabled():
        sys.exit("No storage set up (MongoDB or B2 secrets missing).")
    want = os.environ.get("RUN_IDS", "all").strip()
    ids = storage.stored_runs() if want in ("", "all") else [x.strip() for x in want.split(",") if x.strip()]
    log.info(f"Rebuilding {len(ids)} run(s): {ids}")
    ok = sum(rebuild(r) for r in ids)
    log.info(f"Done: {ok} of {len(ids)} rebuilt")
    if ok < len(ids):
        sys.exit(1)


if __name__ == "__main__":
    main()