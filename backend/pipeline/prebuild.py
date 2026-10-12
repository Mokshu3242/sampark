"""Things the GitHub worker builds once per run, so the 512 MB server never has to.

- story_flow.json   the flow trace story mode asks for (same point, same settings)
- report_en/ne.html both reports, with the map already drawn
- stretch_*.json    colour stretch for Sentinel-2 village chips
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("flood-watch.prebuild")

STORY_FILE = "story_flow.json"
STORY_BUFFER_M = 500.0      # must match App.tsx: traceFlow(runId, lon, lat, 500, 25)
STORY_SNAP_PX = 25
TOL = 1e-7


def story_point(run_dir: Path):
    """Same choice as App.tsx: the northernmost cut-off village (or any village)."""
    p = run_dir / "cutoff.geojson"
    if not p.exists():
        return None
    gj = json.loads(p.read_text(encoding="utf-8"))
    pts = []
    for f in gj.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "Point":
            lon, lat = g["coordinates"][:2]
            pts.append((lon, lat, (f.get("properties") or {}).get("status")))
    cut = [p for p in pts if p[2] == "cut_off"]
    pool = cut or pts
    if not pool:
        return None
    lon, lat, _ = max(pool, key=lambda p: p[1])
    return lon, lat


def flow_result(run_dir: Path, lon: float, lat: float, buffer_m: float, snap_px: int) -> dict:
    from pipeline.hydrology import corridor_events, trace, villages_along
    feature = trace(run_dir, lon, lat, snap_px)
    return {
        "path": feature,
        "villages": villages_along(feature, run_dir, buffer_m),
        "buffer_m": buffer_m,
        "corridor": corridor_events(feature, run_dir, village_buffer_m=buffer_m),
    }


def build_story_flow(run_dir: Path) -> bool:
    pt = story_point(run_dir)
    if pt is None:
        return False
    try:
        result = flow_result(run_dir, pt[0], pt[1], STORY_BUFFER_M, STORY_SNAP_PX)
    except Exception as e:
        log.warning(f"Story flow not built: {e}")
        return False
    (run_dir / STORY_FILE).write_text(json.dumps({
        "lon": pt[0], "lat": pt[1], "buffer_m": STORY_BUFFER_M, "snap_px": STORY_SNAP_PX, "result": result,
    }), encoding="utf-8")
    return True


def cached_flow(run_dir: Path, lon: float, lat: float, buffer_m: float, snap_px: int):
    """Return the saved story trace if this request is the story's own request."""
    p = run_dir / STORY_FILE
    if not p.exists():
        return None
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if (abs(c["lon"] - lon) < TOL and abs(c["lat"] - lat) < TOL
            and abs(c["buffer_m"] - buffer_m) < 1e-6 and int(c["snap_px"]) == int(snap_px)):
        return c["result"]
    return None


def build_chip_stretch(run_id: str, run_dir: Path) -> None:
    from app.api.routes_images import LAYERS, stretch_file, _compute_stretch
    for name in ("s2_before", "s2_after"):
        path = run_dir / LAYERS[name]["file"]
        if path.exists():
            spec = LAYERS[name]
            lo, hi = _compute_stretch(str(path), tuple(spec["bands"]), tuple(spec["stretch"]))
            stretch_file(path, name).write_text(json.dumps([lo, hi]), encoding="utf-8")


def build_all(run_id: str, run_dir: Path) -> None:
    """Everything above, each step on its own so one failure never blocks the rest."""
    try:
        from copilot.report import build_all as build_reports
        build_reports(run_id, run_dir)
    except Exception as e:
        log.warning(f"Reports not built: {e}")
    try:
        build_story_flow(run_dir)
    except Exception as e:
        log.warning(f"Story flow not built: {e}")
    try:
        build_chip_stretch(run_id, run_dir)
    except Exception as e:
        log.warning(f"Chip stretch not built: {e}")