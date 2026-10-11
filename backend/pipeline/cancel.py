import os
from pathlib import Path

MARKER = "CANCEL"
ACTIVE: set[str] = set()     # run IDs this process is running right now


class Cancelled(Exception):
    pass


def request(run_dir: Path) -> None:
    (Path(run_dir) / MARKER).write_text("stop", encoding="utf-8")


def requested(run_dir: Path) -> bool:
    p = Path(run_dir) / MARKER
    if p.exists():
        return True
    if os.getenv("SAMPARK_WORKER") == "1":        # cloud worker: Stop arrives through Mongo
        from app.core import remote
        if remote.cancel_requested(Path(run_dir).name):
            request(run_dir)
            return True
    return False


def check(run_dir: Path) -> None:
    """Call between stages. Raises Cancelled if the user pressed Stop."""
    if requested(run_dir):
        raise Cancelled("Stopped by user")