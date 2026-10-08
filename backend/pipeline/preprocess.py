import logging
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy import ndimage

log = logging.getLogger("flood-watch.preprocess")


def read_ref(path: Path) -> tuple[np.ndarray, dict]:
    """Read the reference raster (S1). All bands as float32, nodata -> NaN."""
    with rasterio.open(path) as ds:
        arr = ds.read().astype(np.float32)
        profile = ds.profile.copy()
        if ds.nodata is not None and not np.isnan(ds.nodata):
            arr[arr == ds.nodata] = np.nan
    return arr, profile


def read_aligned(path: Path, ref: dict, resampling=Resampling.bilinear,
                 zero_is_nodata: bool = False) -> np.ndarray:
    """Read any raster snapped onto the reference grid. File nodata (and optionally 0) -> NaN."""
    with rasterio.open(path) as src:
        out = np.full((src.count, ref["height"], ref["width"]), np.nan, dtype=np.float32)
        for i in range(src.count):
            band = src.read(i + 1).astype(np.float32)
            if src.nodata is not None and not np.isnan(src.nodata):
                band[band == src.nodata] = np.nan
            if zero_is_nodata:
                band[band == 0] = np.nan
            reproject(
                source=band, destination=out[i],
                src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                dst_transform=ref["transform"], dst_crs=ref["crs"], dst_nodata=np.nan,
                resampling=resampling,
            )
    return out


def speckle_filter(arr: np.ndarray, size: int = 5) -> np.ndarray:
    """Median filter per band, NaN-safe."""
    out = np.empty_like(arr)
    for i in range(arr.shape[0]):
        band = arr[i]
        nan = ~np.isfinite(band)
        if nan.all():
            out[i] = band
            continue
        filled = np.where(nan, np.nanmedian(band), band)
        f = ndimage.median_filter(filled, size=size)
        f[nan] = np.nan
        out[i] = f
    return out


def slope_deg(dem: np.ndarray, pixel_m: float) -> np.ndarray:
    dy, dx = np.gradient(dem, pixel_m)
    return np.degrees(np.arctan(np.hypot(dx, dy)))