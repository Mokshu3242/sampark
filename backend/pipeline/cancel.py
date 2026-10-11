from pathlib import Path

MARKER = "CANCEL"
ACTIVE: set[str] = set()     # run IDs this server process is running right now


class Cancelled(Exception):
    pass


def request(run_dir: Path) -> None:
    (Path(run_dir) / MARKER).write_text("stop", encoding="utf-8")


def requested(run_dir: Path) -> bool:
    return (Path(run_dir) / MARKER).exists()


def check(run_dir: Path) -> None:
    """Call between stages. Raises Cancelled if the user pressed Stop."""
    if requested(run_dir):
        raise Cancelled("Stopped by user")