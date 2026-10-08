import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rasterio

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import fetch_s1, openeo_helper as oe

log = logging.getLogger("flood-watch.fetch")


def _valid_fraction(path: Path) -> float:
    """Share of pixels with real data (not cloud-masked or empty). Works for int and float rasters."""
    with rasterio.open(path) as ds:
        arr = ds.read(1, masked=True)
        valid = ~np.ma.getmaskarray(arr)
        if np.issubdtype(arr.dtype, np.floating):
            valid &= np.isfinite(arr.data)
        else:
            valid &= arr.data != 0   # 0 means no data for S2 reflectance
        return float(valid.mean())


def fetch_all(run_id: str, bbox: list[float], flood_date: date, window_days: int = 30) -> dict:
    registry = RunRegistry(settings.runs_dir)
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Find the pair (metadata only, costs no credits)
    registry.set_status(run_id, "searching_s1")
    pair = fetch_s1.find_pair(bbox, flood_date, window_days)
    if not pair:
        registry.set_status(run_id, "failed", "no Sentinel-1 pair found")
        raise RuntimeError("No Sentinel-1 pair found. Try a wider window or a different area.")
    (run_dir / "s1_pair.json").write_text(json.dumps(pair, indent=2))
    log.info(f"Pair: orbit {pair['orbit']} {pair['direction']}, "
             f"{pair['before_date']} -> {pair['after_date']}")

    registry.set_status(run_id, "connecting_openeo")
    conn = oe.connect()

    # 2. Sentinel-1 (required)
    registry.set_status(run_id, "processing_s1")
    oe.run_job(oe.s1_pair(conn, bbox, pair), run_dir / "s1_pair.tif", f"fw-{run_id}-s1")

    # 3. DEM (required)
    registry.set_status(run_id, "processing_dem")
    oe.run_job(oe.dem(conn, bbox), run_dir / "dem.tif", f"fw-{run_id}-dem")

    # 4. Sentinel-2 (optional, monsoon clouds are common)
    s2 = {}
    windows = {
        "before": (flood_date - timedelta(days=window_days), flood_date - timedelta(days=1)),
        "after": (flood_date + timedelta(days=1), flood_date + timedelta(days=window_days)),
    }
    for tag, (start, end) in windows.items():
        registry.set_status(run_id, f"processing_s2_{tag}")
        path = run_dir / f"s2_{tag}.tif"
        try:
            oe.run_job(oe.s2_composite(conn, bbox, start, end, tag), path, f"fw-{run_id}-s2-{tag}")
            frac = _valid_fraction(path)
            s2[tag] = {"status": "ok" if frac >= 0.2 else "too_cloudy", "clear_fraction": round(frac, 3)}
        except Exception as e:
            log.warning(f"S2 {tag} failed: {e}")
            s2[tag] = {"status": "failed", "error": str(e)[:200]}

    quality = _quality_report(pair, s2, bbox)
    (run_dir / "quality.json").write_text(json.dumps(quality, indent=2))
    registry.set_status(run_id, "fetched", f"data ready ({quality['badge']})")
    return quality


def _quality_report(pair: dict, s2: dict, bbox: list[float]) -> dict:
    worst_gap = max(pair["gap_before_days"], pair["gap_after_days"])
    badge = "good" if worst_gap <= 6 else "fair" if worst_gap <= 12 else "poor"

    notes = []
    if worst_gap > 12:
        notes.append("Large gap between image and flood date. Short floods may be missed, or recovery included.")
    if pair["window_days"] > 30:
        notes.append("No pair in the standard window; a wider search was used.")
    if all(v.get("status") != "ok" for v in s2.values()):
        notes.append("No usable Sentinel-2. Mapping relies on radar only.")

    return {
        "s1": {k: pair[k] for k in ("orbit", "direction", "polarizations", "before_date", "after_date",
                                    "gap_before_days", "gap_after_days", "coverage_before",
                                    "coverage_after", "window_days")},
        "s2": s2,
        "dem": "ok",
        "grid": {"resolution_m": oe.RESOLUTION_M, "epsg": oe.utm_epsg(bbox)},
        "badge": badge,
        "notes": notes,
    }