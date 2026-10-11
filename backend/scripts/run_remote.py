"""Entry point for the GitHub Actions worker. Reads RUN_ID and RUN_REQUEST from the environment."""
import json
import os
import re
import sys

os.environ["SAMPARK_WORKER"] = "1"

from app.core import storage                       # noqa: E402
from app.core.config import settings               # noqa: E402
from app.core.logging import setup_logging         # noqa: E402
from app.core.run_registry import RunRegistry      # noqa: E402
from pipeline.run_all import run_pipeline          # noqa: E402

RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")


def main() -> int:
    setup_logging()
    run_id = os.environ.get("RUN_ID", "")
    if not RUN_ID_RE.match(run_id):
        print(f"Bad RUN_ID: {run_id!r}")
        return 2
    req = json.loads(os.environ["RUN_REQUEST"])

    reg = RunRegistry(settings.runs_dir)
    reg.create(run_id, req)
    reg.set_status(run_id, "created", "Cloud worker started")

    run_pipeline(run_id, req["bbox"], req["flood_date"], int(req.get("window_days", 30)))

    status = reg.get_status(run_id).get("status")
    if status == "done" and not storage.has_run(run_id):
        reg.set_status(run_id, "failed", "Finished, but saving the results failed.")
        return 1
    print(f"{run_id}: {status}")
    return 0 if status in ("done", "cancelled") else 1


if __name__ == "__main__":
    sys.exit(main())