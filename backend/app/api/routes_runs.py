import json
import zipfile
from datetime import date
from io import BytesIO

import geopandas as gpd
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from app.core.config import settings
from app.core.run_registry import RunRegistry
from app.schemas.run import RunRequest
from copilot.agent import ask as copilot_ask
from copilot.report import generate as generate_report
from pipeline.fetch import fetch_all
from pipeline.map_cutoff import map_cutoff
from pipeline.map_damage import map_damage
from pipeline.map_flood import map_flood
from pipeline.run_all import run_pipeline
from pipeline.hydrology import corridor_events, trace as trace_flow, villages_along

router = APIRouter(prefix="/runs", tags=["runs"])
registry = RunRegistry(settings.runs_dir)

LAYER_FILES = {
    "flood": "flood_polygons.geojson",
    "debris": "debris_polygons.geojson",
    "roads": "road_edges.geojson",
    "villages": "cutoff.geojson",
    "repair": "repair_plan.geojson",
}
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary",
               "motorway_link", "trunk_link", "primary_link", "secondary_link"}


class ChatRequest(BaseModel):
    question: str
    language: str = "auto"
    history: list[dict] = []

class FlowRequest(BaseModel):
    lon: float
    lat: float
    buffer_m: float = 500.0

def _run_dir(run_id: str):
    d = settings.runs_dir / run_id
    if not d.exists() or not (d / "request.json").exists():
        raise HTTPException(404, "run not found")
    return d


def _load_request(run_id: str) -> dict:
    return json.loads((_run_dir(run_id) / "request.json").read_text(encoding="utf-8"))


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

@router.post("")
def create_run(req: RunRequest, bg: BackgroundTasks):
    run_id = registry.new_run(req.model_dump(mode="json"))
    bg.add_task(run_pipeline, run_id, req.bbox, req.flood_date, req.window_days)
    return {"run_id": run_id, "status": "created"}


@router.get("")
def list_runs(limit: int = 30):
    return {"runs": registry.list_runs(limit)}


@router.get("/{run_id}")
def get_run(run_id: str):
    run_dir = _run_dir(run_id)
    q = run_dir / "quality.json"
    return {
        "run_id": run_id,
        **registry.get_status(run_id),
        "ready": registry.is_ready(run_id),
        "request": _load_request(run_id),
        "quality": json.loads(q.read_text(encoding="utf-8")) if q.exists() else None,
    }


# ---------- Per-phase debug endpoints ----------

@router.post("/{run_id}/fetch")
def fetch_run(run_id: str, bg: BackgroundTasks):
    req = _load_request(run_id)
    bg.add_task(fetch_all, run_id, req["bbox"], date.fromisoformat(req["flood_date"]), req.get("window_days", 30))
    return {"run_id": run_id, "status": "fetch_started"}


@router.post("/{run_id}/map")
def map_run(run_id: str, bg: BackgroundTasks):
    bg.add_task(map_flood, run_id, _load_request(run_id)["bbox"])
    return {"run_id": run_id, "status": "mapping_started"}


@router.post("/{run_id}/damage")
def damage_run(run_id: str, bg: BackgroundTasks):
    bg.add_task(map_damage, run_id, _load_request(run_id)["bbox"])
    return {"run_id": run_id, "status": "damage_started"}


@router.post("/{run_id}/cutoff")
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
    for key, fname in [("flood", "flood_stats.json"), ("damage", "damage_stats.json"), ("cutoff", "cutoff_stats.json"), ("eval", "eval.json")]:
        p = run_dir / fname
        out[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


# ---------- Chat & report ----------

@router.post("/{run_id}/chat")
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
    out = generate_report(run_id, run_dir, lang)
    if out["pdf"]:
        return FileResponse(str(out["pdf"]), media_type="application/pdf")
    return HTMLResponse(out["html"].read_text(encoding="utf-8"))   # opens in the tab


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
    run_dir = _run_dir(run_id)
    if not (100 <= body.buffer_m <= 3000):
        raise HTTPException(400, "buffer_m must be 100 to 3000")
    try:
        feature = trace_flow(run_dir, body.lon, body.lat)
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