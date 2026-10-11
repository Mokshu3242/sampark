"""Run the heavy pipeline on GitHub Actions. Status and Stop requests travel through Mongo."""
import json
import logging
import os
import time

import requests

from app.core import storage
from app.core.config import settings

log = logging.getLogger("flood-watch.remote")

TERMINAL = {"done", "failed", "cancelled"}
STALE_S = 30 * 60          # no news from the worker for 30 min = failed
CANCEL_CHECK_S = 10

_col = None
_cancel_cache: dict[str, tuple[float, bool]] = {}


def github_mode() -> bool:
    return settings.pipeline_mode.strip().lower() == "github"


def is_worker() -> bool:
    return os.getenv("SAMPARK_WORKER") == "1"


def _status_col():
    global _col
    if _col is None:
        db = storage.mongo_db()
        if db is None:
            raise RuntimeError("Mongo is required for cloud runs")
        _col = db["run_status"]
    return _col


# ---------- shared status ----------

def push_status(run_id: str, status: dict, request: dict | None = None) -> None:
    doc = {"status_doc": status, "updated": time.time()}
    if request is not None:
        doc["request"] = request
    _status_col().update_one({"_id": run_id}, {"$set": doc}, upsert=True)


def get_doc(run_id: str) -> dict | None:
    return _status_col().find_one({"_id": run_id})


def is_live(run_id: str) -> bool:
    """Worker still running (not finished, and heard from recently)."""
    d = get_doc(run_id)
    if not d:
        return False
    st = (d.get("status_doc") or {}).get("status")
    return st not in TERMINAL and time.time() - d.get("updated", 0) < STALE_S


def request_cancel(run_id: str) -> None:
    _status_col().update_one({"_id": run_id}, {"$set": {"cancel": True}}, upsert=True)


def cancel_requested(run_id: str) -> bool:
    """Worker side. Asks Mongo at most every 10 s."""
    now = time.time()
    t, v = _cancel_cache.get(run_id, (0.0, False))
    if now - t < CANCEL_CHECK_S:
        return v
    try:
        d = _status_col().find_one({"_id": run_id}, {"cancel": 1})
        v = bool(d and d.get("cancel"))
    except Exception as e:
        log.warning(f"Cancel check failed: {e}")
        v = False
    _cancel_cache[run_id] = (now, v)
    return v


def forget(run_id: str) -> None:
    try:
        _status_col().delete_one({"_id": run_id})
    except Exception as e:
        log.warning(f"Could not remove status for {run_id}: {e}")


# ---------- start a cloud run ----------

def dispatch(run_id: str, request: dict) -> None:
    if not settings.gh_token:
        raise RuntimeError("GH_TOKEN is not set on the server")
    url = (f"https://api.github.com/repos/{settings.gh_repo}"
           f"/actions/workflows/{settings.gh_workflow}/dispatches")
    r = requests.post(url, timeout=20, headers={
        "Authorization": f"Bearer {settings.gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, json={"ref": settings.gh_ref,
             "inputs": {"run_id": run_id, "request": json.dumps(request, separators=(",", ":"))}})
    if r.status_code != 204:
        raise RuntimeError(f"GitHub did not start the job ({r.status_code}): {r.text[:200]}")
    log.info(f"Dispatched {run_id} to GitHub Actions")


# ---------- server side ----------

def ensure_local(run_id: str, registry) -> bool:
    """After a server restart, rebuild the folder of a cloud run from Mongo."""
    d = get_doc(run_id)
    if not d or not d.get("request"):
        return False
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "request.json").write_text(json.dumps(d["request"], indent=2, ensure_ascii=False), encoding="utf-8")
    registry.write_status(run_id, d.get("status_doc") or {"status": "created"})
    return True


def sync(run_id: str, registry) -> None:
    """Copy the worker's status here. When the run is done, pull its results once."""
    local = registry.get_status(run_id).get("status")
    if local in ("failed", "cancelled") or (local == "done" and registry.is_ready(run_id)):
        return
    d = get_doc(run_id)
    if not d:
        return
    st = dict(d.get("status_doc") or {})

    if st.get("status") not in TERMINAL and time.time() - d.get("updated", 0) > STALE_S:
        st.update(status="failed", stage="failed", stage_index=-1,
                  detail="The cloud worker stopped responding. Please start the run again.")

    if st.get("status") == "done" and not registry.is_ready(run_id):
        from app.core.heavy import HEAVY
        from app.core.memtrim import trim
        with HEAVY:
            ok = registry.is_ready(run_id) or storage.restore_run(run_id)
        if ok and registry.is_ready(run_id):
            try:
                from pipeline.hydrology import warm_cache
                warm_cache(settings.runs_dir / run_id)
            finally:
                trim("pull run")
            return                                   # the zip brought its own status.json
        st.update(status="saving", detail="Saving results…")   # zip not uploaded yet, try next poll

    registry.write_status(run_id, st)