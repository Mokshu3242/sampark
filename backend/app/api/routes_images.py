"""Serve run rasters as PNG overlays for MapLibre (uncertainty, hillshade, Sentinel-2).
Every raster is warped to Web Mercator first, so the 4 corners line up exactly on the map."""
import logging
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

from app.core.config import settings
from functools import lru_cache
from rasterio.windows import from_bounds

log = logging.getLogger("flood-watch.images")
router = APIRouter(prefix="/runs", tags=["images"])

RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
WEB = "EPSG:3857"

LAYERS = {
    "uncertainty": {
        "file": "uncertainty.tif", "mode": "class",
        # 0 = low, 1 = medium, 2 = high uncertainty. 255 = nothing detected (transparent).
        "palette": {0: (39, 174, 96, 190), 1: (243, 156, 18, 210), 2: (231, 76, 60, 230)},
    },
    "dem": {"file": "dem.tif", "mode": "hillshade"},
    "s2_before": {"file": "s2_before.tif", "mode": "rgb", "bands": [3, 2, 1], "stretch": (2, 98), "gamma": 0.9},
    "s2_after": {"file": "s2_after.tif", "mode": "rgb", "bands": [3, 2, 1], "stretch": (2, 98), "gamma": 0.9},
}
FALLBACK_RGB = [3, 2, 1]   # used only if band names don't say B04/B03/B02


def _path(run_id: str, name: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(400, "bad run id")
    if name not in LAYERS:
        raise HTTPException(404, "unknown image layer")
    p = settings.runs_dir / run_id / LAYERS[name]["file"]
    if not p.exists():
        raise HTTPException(404, "layer not produced for this run")
    return p


def _rgb_bands(ds) -> list[int]:
    """Find red, green, blue by band name; fall back to a fixed order."""
    names = [(d or "").upper() for d in ds.descriptions]
    want = []
    for keys in (("B04", "RED"), ("B03", "GREEN"), ("B02", "BLUE")):
        idx = next((i + 1 for i, n in enumerate(names) if any(k in n for k in keys)), None)
        want.append(idx)
    if all(want):
        return want
    return FALLBACK_RGB if ds.count >= 3 else [1, 1, 1]


def _warp(path: Path, bands: list[int] | None, resampling: Resampling, width: int):
    """Warp bands to EPSG:3857 at about `width` pixels wide. Returns (array, mercator bounds)."""
    with rasterio.open(path) as ds:
        bands = bands or [1]
        tr, w, h = calculate_default_transform(ds.crs, WEB, ds.width, ds.height, *ds.bounds)
        scale = min(1.0, width / w)               # never upsample
        ow, oh = max(1, int(w * scale)), max(1, int(h * scale))
        tr = tr * tr.scale(w / ow, h / oh)
        out = np.full((len(bands), oh, ow), np.nan, dtype="float32")
        for i, b in enumerate(bands):
            src = ds.read(b).astype("float32")
            if ds.nodata is not None:
                src[src == ds.nodata] = np.nan
            reproject(src, out[i], src_transform=ds.transform, src_crs=ds.crs,
                      src_nodata=np.nan, dst_transform=tr, dst_crs=WEB, dst_nodata=np.nan,
                      resampling=resampling)
        left, top = tr.c, tr.f
        right, bottom = left + tr.a * ow, top + tr.e * oh
    return out, (left, bottom, right, top)


def _corners(mb) -> list[list[float]]:
    left, bottom, right, top = mb
    t = Transformer.from_crs(WEB, 4326, always_xy=True)
    w, s = t.transform(left, bottom)
    e, n = t.transform(right, top)
    return [[w, n], [e, n], [e, s], [w, s]]


def _class_png(a: np.ndarray, palette: dict) -> Image.Image:
    rgba = np.zeros(a.shape + (4,), dtype=np.uint8)
    ok = np.isfinite(a)
    ai = np.where(ok, a, -9999).astype(np.int32)
    for v, color in palette.items():
        rgba[ai == v] = color
    return Image.fromarray(rgba, "RGBA")


def _rgb_png(arr: np.ndarray, stretch, gamma) -> Image.Image:
    h, w = arr.shape[1:]
    valid = np.all(np.isfinite(arr), axis=0) & np.all(arr != 0, axis=0)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    if not valid.any():
        return Image.fromarray(out, "RGBA")
    # one stretch for all bands keeps the colours natural
    lo, hi = np.percentile(arr[:, valid], stretch)
    hi = hi if hi > lo else lo + 1
    rgb = np.clip((arr - lo) / (hi - lo), 0, 1) ** gamma
    out[..., :3] = (np.nan_to_num(rgb).transpose(1, 2, 0) * 255).astype(np.uint8)
    out[..., 3] = np.where(valid, 255, 0)
    return Image.fromarray(out, "RGBA")


def _hillshade_png(dem: np.ndarray, px_m: float) -> Image.Image:
    nod = ~np.isfinite(dem)
    if nod.all():
        raise HTTPException(500, "DEM has no valid data")
    d = np.where(nod, np.nanmin(dem), dem)
    dy, dx = np.gradient(d, px_m, px_m)
    az, alt = np.radians(315.0), np.radians(45.0)
    slope = np.pi / 2 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    s = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    g = np.clip(s * 255, 0, 255).astype(np.uint8)
    out = np.dstack([g, g, g, np.where(nod, 0, 255).astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def _render(path: Path, name: str, width: int):
    spec = LAYERS[name]
    if spec["mode"] == "class":
        a, mb = _warp(path, [1], Resampling.nearest, width)
        return _class_png(a[0], spec["palette"]), mb
    if spec["mode"] == "rgb":
        with rasterio.open(path) as ds:
            bands = spec["bands"] or _rgb_bands(ds)
        a, mb = _warp(path, bands, Resampling.bilinear, width)
        return _rgb_png(a, spec["stretch"], spec["gamma"]), mb
    if spec["mode"] == "hillshade":
        a, mb = _warp(path, [1], Resampling.bilinear, width)
        px_m = (mb[2] - mb[0]) / a.shape[2]
        lat = np.radians(_corners(mb)[0][1])
        return _hillshade_png(a[0], px_m * np.cos(lat)), mb   # true metres on the ground
    raise HTTPException(500, "render mode not implemented")


def _cached(run_id: str, name: str, width: int):
    path = _path(run_id, name)
    stamp = int(path.stat().st_mtime)
    png = path.parent / f"cache_{name}_{width}_{stamp}.png"
    meta = png.with_suffix(".bounds")
    if png.exists() and meta.exists():
        return png.read_bytes(), [float(x) for x in meta.read_text().split(",")]
    img, mb = _render(path, name, width)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    for old in path.parent.glob(f"cache_{name}_{width}_*"):   # drop stale versions
        old.unlink(missing_ok=True)
    png.write_bytes(buf.getvalue())
    meta.write_text(",".join(str(x) for x in mb))
    return buf.getvalue(), list(mb)


@router.get("/{run_id}/image/{name}.png")
def get_image(run_id: str, name: str, width: int = Query(1400, ge=200, le=2500)):
    data, _ = _cached(run_id, name, width)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/{run_id}/image/{name}/bounds")
def get_image_bounds(run_id: str, name: str, width: int = Query(1400, ge=200, le=2500)):
    _, mb = _cached(run_id, name, width)
    return {"coordinates": _corners(mb)}

@lru_cache(maxsize=16)
def _global_stretch(path_str: str, mtime: float, bands: tuple, stretch: tuple) -> tuple[float, float]:
    """One colour stretch for the whole image, so every chip matches the big overlay."""
    with rasterio.open(path_str) as ds:
        arr = ds.read(list(bands), out_shape=(len(bands), max(1, ds.height // 4), max(1, ds.width // 4))).astype("float32")
        nod = ds.nodata
    valid = np.all(arr != 0, axis=0)
    if nod is not None:
        valid &= np.all(arr != nod, axis=0)
    if not valid.any():
        return 0.0, 1.0
    lo, hi = np.percentile(arr[:, valid], stretch)
    return float(lo), float(hi if hi > lo else lo + 1)


@router.get("/{run_id}/chip/{name}.png")
def get_chip(run_id: str, name: str, lon: float, lat: float,
             size_m: float = Query(2000, ge=500, le=5000), px: int = Query(240, ge=64, le=512)):
    """Small square image around a point (village card). Sentinel-2 only."""
    if name not in ("s2_before", "s2_after"):
        raise HTTPException(404, "chips are only available for Sentinel-2")
    path = _path(run_id, name)
    spec = LAYERS[name]
    bands = spec["bands"]
    with rasterio.open(path) as ds:
        x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(lon, lat)
        h = size_m / 2
        win = from_bounds(x - h, y - h, x + h, y + h, ds.transform)
        fill = ds.nodata if ds.nodata is not None else 0
        arr = ds.read(bands, window=win, out_shape=(len(bands), px, px), boundless=True,
                      fill_value=fill, resampling=Resampling.nearest).astype("float32")
        nod = ds.nodata
    valid = np.all(arr != 0, axis=0)
    if nod is not None:
        valid &= np.all(arr != nod, axis=0)
    lo, hi = _global_stretch(str(path), path.stat().st_mtime, tuple(bands), tuple(spec["stretch"]))
    rgb = np.clip((arr - lo) / (hi - lo), 0, 1) ** spec["gamma"]
    out = np.zeros((px, px, 4), dtype=np.uint8)
    out[..., :3] = (rgb.transpose(1, 2, 0) * 255).astype(np.uint8)
    out[..., 3] = np.where(valid, 255, 0)
    buf = BytesIO()
    Image.fromarray(out, "RGBA").save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})