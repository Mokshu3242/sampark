import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import openeo

from app.core.config import settings
from pipeline import cancel

log = logging.getLogger("flood-watch.openeo")

BACKEND_URL = "openeo.dataspace.copernicus.eu"
RESOLUTION_M = 20
S1_COEFFICIENT = "sigma0-ellipsoid"   # terrain-flattened, best for mountains
DEM_BAND = "DEM"                     # confirm with probe_cdse.py
S2_CLOUD_CLASSES = (3, 8, 9, 10)     # SCL: shadow, cloud medium, cloud high, cirrus
POLL_S = 10                          # how often we check job status and the Stop button
MAX_WAIT_S = 60 * 60                 # give up on a single job after 1 hour


def connect() -> openeo.Connection:
    """Server: CDSE client credentials. Your PC without them: the saved token, as before."""
    conn = openeo.connect(BACKEND_URL)
    cid = settings.cdse_client_id or os.getenv("CDSE_CLIENT_ID", "")
    secret = settings.cdse_client_secret or os.getenv("CDSE_CLIENT_SECRET", "")
    if cid and secret:
        log.info("openEO: logging in with client credentials")
        return conn.authenticate_oidc_client_credentials(client_id=cid, client_secret=secret)
    if os.getenv("RENDER") or not sys.stdin.isatty():
        # On a server nobody can click a login link: fail fast instead of hanging
        return conn.authenticate_oidc(max_poll_time=20)
    return conn.authenticate_oidc()


def utm_epsg(bbox: list[float]) -> int:
    lon = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _extent(bbox: list[float]) -> dict:
    return {"west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3]}


def _to_grid(cube, bbox: list[float]):
    return cube.resample_spatial(resolution=RESOLUTION_M, projection=utm_epsg(bbox), method="bilinear")


def s1_single_date(conn, bbox: list[float], time_window: list[str], direction: str, tag: str):
    """One exact acquisition. Returns bands VV_<tag>, VH_<tag>."""
    cube = conn.load_collection(
        "SENTINEL1_GRD",
        spatial_extent=_extent(bbox),
        temporal_extent=time_window,
        bands=["VV", "VH"],
        properties={"sat:orbit_state": lambda v: v == direction},
    )
    cube = cube.sar_backscatter(coefficient=S1_COEFFICIENT)
    cube = cube.reduce_dimension(dimension="t", reducer="mean")
    cube = _to_grid(cube, bbox)
    return cube.rename_labels("bands", target=[f"VV_{tag}", f"VH_{tag}"])


def s1_pair(conn, bbox: list[float], pair: dict):
    before = s1_single_date(conn, bbox, pair["before_time"], pair["direction"], "before")
    after = s1_single_date(conn, bbox, pair["after_time"], pair["direction"], "after")
    return before.merge_cubes(after)


def s2_composite(conn, bbox: list[float], start: date, end: date, tag: str):
    """Cloud-masked median of B03, B08, B11 over a date range."""
    cube = conn.load_collection(
        "SENTINEL2_L2A",
        spatial_extent=_extent(bbox),
        temporal_extent=[start.isoformat(), end.isoformat()],
        bands=["B03", "B08", "B11", "SCL"],
        max_cloud_cover=80,
    )
    scl = cube.band("SCL")
    cloudy = None
    for c in S2_CLOUD_CLASSES:
        cloudy = (scl == c) if cloudy is None else (cloudy | (scl == c))
    cube = cube.filter_bands(["B03", "B08", "B11"]).mask(cloudy)
    cube = cube.reduce_dimension(dimension="t", reducer="median")
    cube = _to_grid(cube, bbox)
    return cube.rename_labels("bands", target=[f"B03_{tag}", f"B08_{tag}", f"B11_{tag}"])


def dem(conn, bbox: list[float]):
    cube = conn.load_collection("COPERNICUS_30", spatial_extent=_extent(bbox), bands=[DEM_BAND])
    cube = cube.reduce_dimension(dimension="t", reducer="max")
    return _to_grid(cube, bbox)


def _stop_job(job) -> None:
    try:
        job.stop()
        log.info(f"Stopped CDSE job {job.job_id}")
    except Exception as e:
        log.warning(f"Could not stop CDSE job {job.job_id}: {e}")


def _wait(job, run_dir: Path) -> None:
    """Like start_and_wait, but stops the CDSE job if the user presses Stop."""
    job.start()
    t0, last = time.time(), None
    while True:
        if cancel.requested(run_dir):
            _stop_job(job)
            raise cancel.Cancelled("Stopped by user")

        try:
            status = job.status()
        except Exception as e:                    # a network blip should not kill the run
            log.warning(f"Status check failed for {job.job_id}: {e}")
            status = last

        if status != last:
            log.info(f"Job {job.job_id}: {status} ({int(time.time() - t0)} s)")
            last = status
        if status == "finished":
            return
        if status in ("error", "canceled"):
            try:
                logs = job.logs(level="error")
                detail = "; ".join(str(getattr(l, "message", l))[:150] for l in logs[:3])
            except Exception:
                detail = ""
            raise RuntimeError(f"CDSE job {job.job_id} {status}. {detail}".strip())
        if time.time() - t0 > MAX_WAIT_S:
            _stop_job(job)
            raise RuntimeError(f"CDSE job {job.job_id} took over {MAX_WAIT_S // 60} minutes")
        time.sleep(POLL_S)


def run_job(cube, out_path: Path, title: str) -> Path:
    """Batch job: slower to start, but no timeouts. Skips if the file already exists.
    Can be stopped from the UI: checks the run's CANCEL marker while waiting."""
    out_path = Path(out_path)
    run_dir = out_path.parent

    if out_path.exists() and out_path.stat().st_size > 0:
        log.info(f"Skipping '{title}', {out_path.name} already exists")
        return out_path
    cancel.check(run_dir)                         # don't send a new job after Stop

    run_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Batch job '{title}' -> {out_path.name}")
    job = cube.create_job(title=title, out_format="GTiff")
    _wait(job, run_dir)

    cancel.check(run_dir)
    part = out_path.with_suffix(out_path.suffix + ".part")
    try:
        job.get_results().download_file(str(part))
        part.replace(out_path)                    # only a complete file gets the real name
    finally:
        if part.exists():
            part.unlink(missing_ok=True)
    return out_path