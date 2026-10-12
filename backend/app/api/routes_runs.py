import json
import shutil
import zipfile
from datetime import date
from io import BytesIO

import geopandas as gpd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core import remote, storage
from app.core.auth import require_key
from app.core.config import settings
from app.core.heavy import HEAVY
from app.core.run_registry import RunRegistry
from app.schemas.run import RunRequest
from copilot.agent import ask as copilot_ask
from copilot.report import generate as generate_report, report_path
from pipeline import cancel
from pipeline.fetch import fetch_all
from pipeline.hydrology import corridor_events, trace as trace_flow, villages_along
from pipeline.map_cutoff import map_cutoff
from pipeline.map_damage import map_damage
from pipeline.map_flood import map_flood
from pipeline.prebuild import cached_flow
from pipeline.run_all import run_pipeline

router = APIRouter(prefix="/runs", tags=["runs"])
registry = RunRegistry(settings.runs_dir)
LOCKED = [Depends(require_key)]

LAYER_FILES = {
    "flood": "flood_polygons.geojson",
    "debris": "debris_polygons.geojson",
    "roads": "road_edges.geojson",
    "villages": "cutoff.geojson",
    "repair": "repair_plan.geojson",
}
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary",
               "motorway_link", "trunk_link", "primary_link", "secondary_link"}
FINISHED = {"done", "failed", "cancelled"}


class ChatRequest(BaseModel):
    question: str
    language: str = "auto"
    history: list[dict] = []


class FlowRequest(BaseModel):
    lon: float
    lat: float
    buffer_m: float = 500.0
    snap_px: int = 5


def _run_dir(run_id: str):
    d = settings.runs_dir / run_id
    if not (d / "request.json").exists() and remote.github_mode():
        try:
            remote.ensure_local(run_id, registry)     # cloud run, server restarted meanwhile
        except Exception:
            pass
    if not d.exists() or not (d / "request.json").exists():
        raise HTTPException(404, "run not found")
    return d


def _load_request(run_id: str) -> dict:
    return json.loads((_run_dir(run_id) / "request.json").read_text(encoding="utf-8"))


def _status_of(run_id: str) -> str | None:
    try:
        return (registry.get_status(run_id) or {}).get("status")
    except Exception:
        return None


def _roads_web(run_dir):
    """Browser-friendly roads: damaged edges + main highways only. Cached."""
    src, out = run_dir / "road_edges.geojson", run_dir / "road_edges_web.geojson"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        g = gpd.read_file(src)
        keep = (g["status"] != "ok") | g["highway"].isin(MAJOR_ROADS)
        cols = [c for c in ["status", "highway", "name", "is_bridge", "geometry"] if c in g]
        g.loc[keep, cols].to_file(out, driver="GeoJSON")
    return out


# ---------- Create, list, status ----------

@router.post("", dependencies=LOCKED)
def create_run(req: RunRequest, bg: BackgroundTasks):
    body = req.model_dump(mode="json")
    run_id = registry.new_run(body)
    if remote.github_mode():
        try:
            remote.push_status(run_id, registry.get_status(run_id), request=body)
            remote.dispatch(run_id, body)
            registry.set_status(run_id, "created", "Queued on the cloud worker. Starting in about a minute…")
        except Exception as e:
            registry.set_status(run_id, "failed", f"Could not start the cloud worker: {str(e)[:150]}")
            raise HTTPException(502, f"Could not start the cloud worker: {str(e)[:150]}")
        return {"run_id": run_id, "status": "queued"}
    bg.add_task(run_pipeline, run_id, req.bbox, req.flood_date, req.window_days)
    return {"run_id": run_id, "status": "created"}


@router.get("")
def list_runs(limit: int = 30):
    return {"runs": registry.list_runs(limit)}


@router.get("/{run_id}")
def get_run(run_id: str):
    run_dir = _run_dir(run_id)
    if remote.github_mode() and run_id not in cancel.ACTIVE:
        try:
            remote.sync(run_id, registry)
        except Exception:
            pass                                       # a Mongo blip should not break polling
    q = run_dir / "quality.json"
    return {
        "run_id": run_id,
        **registry.get_status(run_id),
        "ready": registry.is_ready(run_id),
        "request": _load_request(run_id),
        "quality": json.loads(q.read_text(encoding="utf-8")) if q.exists() else None,
    }


# ---------- Stop & delete ----------

@router.post("/{run_id}/cancel", dependencies=LOCKED)
def cancel_run(run_id: str):
    d = _run_dir(run_id)
    if run_id in cancel.ACTIVE:                        # running in this process
        cancel.request(d)
        return {"ok": True, "status": "cancelling"}
    status = _status_of(run_id)
    if status in FINISHED:
        return {"ok": True, "status": status}
    if remote.github_mode() and remote.is_live(run_id):
        remote.request_cancel(run_id)
        registry.set_status(run_id, status or "created", "Stopping… (the cloud worker checks every 10 s)")
        return {"ok": True, "status": "cancelling"}
    # Not running anywhere (stuck after a restart): mark it so the UI stops waiting
    registry.set_status(run_id, "cancelled", "Stopped (was not running)")
    return {"ok": True, "status": "cancelled"}


@router.delete("/{run_id}", dependencies=LOCKED)
def delete_run(run_id: str):
    if run_id in storage.PINNED:
        raise HTTPException(403, "Demo runs cannot be deleted")
    if run_id in cancel.ACTIVE:
        raise HTTPException(409, "Run is still going. Stop it first.")
    if remote.github_mode():
        try:
            if remote.is_live(run_id):
                raise HTTPException(409, "Run is still going on the cloud worker. Stop it first.")
            remote.forget(run_id)
        except HTTPException:
            raise
        except Exception:
            pass
    d = settings.runs_dir / run_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    removed = storage.delete_run(run_id)
    return {"ok": True, "local": not d.exists(), **removed}


# ---------- Per-phase debug endpoints ----------

@router.post("/{run_id}/fetch", dependencies=LOCKED)
def fetch_run(run_id: str, bg: BackgroundTasks):
    req = _load_request(run_id)
    bg.add_task(fetch_all, run_id, req["bbox"], date.fromisoformat(req["flood_date"]), req.get("window_days", 30))
    return {"run_id": run_id, "status": "fetch_started"}


@router.post("/{run_id}/map", dependencies=LOCKED)
def map_run(run_id: str, bg: BackgroundTasks):
    bg.add_task(map_flood, run_id, _load_request(run_id)["bbox"])
    return {"run_id": run_id, "status": "mapping_started"}


@router.post("/{run_id}/damage", dependencies=LOCKED)
def damage_run(run_id: str, bg: BackgroundTasks):
    bg.add_task(map_damage, run_id, _load_request(run_id)["bbox"])
    return {"run_id": run_id, "status": "damage_started"}


@router.post("/{run_id}/cutoff", dependencies=LOCKED)
def cutoff_run(run_id: str, bg: BackgroundTasks):
    bg.add_task(map_cutoff, run_id, _load_request(run_id)["bbox"])
    return {"run_id": run_id, "status": "cutoff_started"}


# ---------- Layers & stats ----------

@router.get("/{run_id}/layers/{name}")
def get_layer(run_id: str, name: str):
    if name not in LAYER_FILES:
        raise HTTPException(404, "unknown layer")
    run_dir = _run_dir(run_id)
    path = run_dir / LAYER_FILES[name]
    if not path.exists():
        raise HTTPException(404, "layer not produced yet")
    if name == "roads":
        path = _roads_web(run_dir)
    return FileResponse(str(path), media_type="application/geo+json")


@router.get("/{run_id}/stats")
def get_stats(run_id: str):
    run_dir = _run_dir(run_id)
    out = {}
    for key, fname in [("flood", "flood_stats.json"), ("damage", "damage_stats.json"),
                       ("cutoff", "cutoff_stats.json"), ("eval", "eval.json")]:
        p = run_dir / fname
        out[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


# ---------- Chat & report ----------

@router.post("/{run_id}/chat", dependencies=LOCKED)
def chat(run_id: str, body: ChatRequest):
    run_dir = _run_dir(run_id)
    if not body.question.strip():
        raise HTTPException(400, "question required")
    return copilot_ask(body.question.strip(), run_dir, language=body.language, history=body.history)


@router.get("/{run_id}/report")
def report(run_id: str, lang: str = "en"):
    run_dir = _run_dir(run_id)
    if lang not in ("en", "ne"):
        raise HTTPException(400, "lang must be en or ne")
    if not registry.is_ready(run_id):
        raise HTTPException(409, "run not finished yet")
    path = report_path(run_dir, lang)
    if not path.exists():
        # Old run without a ready-made report: build it once, one at a time, then keep it.
        from app.core.memtrim import trim
        with HEAVY:
            try:
                if not path.exists():
                    generate_report(run_id, run_dir, lang)
            finally:
                trim("report")
    return FileResponse(str(path), media_type="text/html; charset=utf-8")   # opens in the tab


# ---------- Export ----------

@router.get("/{run_id}/export")
def export_run(run_id: str):
    run_dir = _run_dir(run_id)
    names = list(LAYER_FILES.values()) + ["flood_stats.json", "damage_stats.json", "cutoff_stats.json",
                                          "quality.json", "request.json", "s1_pair.json",
                                          "report_en.html", "report_ne.html"]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in names:
            if (run_dir / f).exists():
                z.write(run_dir / f, arcname=f)
        z.writestr("ATTRIBUTION.txt",
                   "Contains modified Copernicus Sentinel data 2026.\n"
                   "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH "
                   "2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.\n"
                   "© OpenStreetMap contributors. OSM-derived data is licensed under ODbL.\n")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})


@router.get("/{run_id}/eval")
def get_eval(run_id: str):
    p = settings.runs_dir / run_id / "eval.json"
    if not p.exists():
        raise HTTPException(404, "No validation for this run. Run scripts.eval_emsr first.")
    return json.loads(p.read_text(encoding="utf-8"))


@router.post("/{run_id}/flowpath")
def flowpath(run_id: str, body: FlowRequest):
    from app.core.memtrim import trim
    run_dir = _run_dir(run_id)
    if not (1 <= body.snap_px <= 50):
        raise HTTPException(400, "snap_px must be 1 to 50")
    hit = cached_flow(run_dir, body.lon, body.lat, body.buffer_m, body.snap_px)
    if hit is not None:                                # story mode's trace, made by the worker
        return hit
    with HEAVY:
        try:
            try:
                feature = trace_flow(run_dir, body.lon, body.lat, body.snap_px)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except RuntimeError as e:
                raise HTTPException(409, str(e))
            return {
                "path": feature,
                "villages": villages_along(feature, run_dir, body.buffer_m),
                "buffer_m": body.buffer_m,
                "corridor": corridor_events(feature, run_dir, village_buffer_m=body.buffer_m),
            }
        finally:
            trim("flowpath")