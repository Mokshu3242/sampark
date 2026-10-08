import json
import logging
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape
from skimage.morphology import remove_small_objects

log = logging.getLogger("flood-watch.vectorize")

MIN_AREA_HA = 0.5


def clean(mask: np.ndarray, pixel_m: float, min_area_ha: float = MIN_AREA_HA) -> np.ndarray:
    """Drop specks below min area. Raster, polygons, and stats all use this result."""
    min_px = math.ceil(min_area_ha * 10_000 / (pixel_m ** 2))
    return remove_small_objects(mask.astype(bool), min_size=min_px)


def write_mask(mask: np.ndarray, valid: np.ndarray, profile: dict, path: Path) -> None:
    """0 = no, 1 = yes, 255 = no data."""
    out = np.where(valid, mask.astype(np.uint8), 255).astype(np.uint8)
    p = profile.copy()
    p.update(count=1, dtype="uint8", nodata=255, compress="deflate")
    with rasterio.open(path, "w", **p) as ds:
        ds.write(out, 1)


def to_geojson(mask: np.ndarray, profile: dict, path: Path, simplify_m: float = 10.0) -> int:
    """Polygons in lat/lon with area_ha computed in meters."""
    geoms = [shape(g) for g, v in shapes(mask.astype(np.uint8), mask=mask, transform=profile["transform"]) if v == 1]
    if not geoms:
        path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        return 0

    gdf = gpd.GeoDataFrame(geometry=geoms, crs=profile["crs"])
    gdf["geometry"] = gdf.geometry.simplify(simplify_m, preserve_topology=True)
    gdf["area_ha"] = (gdf.geometry.area / 10_000).round(2)
    gdf["id"] = range(1, len(gdf) + 1)
    gdf.to_crs(4326).to_file(path, driver="GeoJSON")
    log.info(f"{path.name}: {len(gdf)} polygons")
    return len(gdf)