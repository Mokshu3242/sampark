import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.transform import rowcol
from scipy.ndimage import binary_dilation

log = logging.getLogger("flood-watch.damage")

STEP_M = 10.0            # sample spacing along lines
ROAD_AFFECTED_M = 20.0   # total impacted length to count as affected
ROAD_CUT_RUN_M = 40.0    # one continuous impacted stretch this long = likely cut
BRIDGE_LOST_RUN_M = 20.0
BUILDING_TOLERANCE_PX = 1   # 0 = exact pixel, 1 = within 20 m


def load_mask(path) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as ds:
        return ds.read(1), ds.profile.copy()


def _lookup(xs, ys, hit: np.ndarray, transform) -> np.ndarray:
    if len(xs) == 0:
        return np.zeros(0, bool)
    r, c = rowcol(transform, xs, ys)
    r = np.asarray(r, dtype=np.int64)
    c = np.asarray(c, dtype=np.int64)
    inside = (r >= 0) & (r < hit.shape[0]) & (c >= 0) & (c < hit.shape[1])
    out = np.zeros(len(r), bool)
    out[inside] = hit[r[inside], c[inside]]
    return out


def classify_points(gdf: gpd.GeoDataFrame, flood: np.ndarray, debris: np.ndarray, profile: dict) -> gpd.GeoDataFrame:
    """Buildings and facilities: center point, with 1 pixel tolerance for houses smaller than a pixel."""
    if gdf.empty:
        return gdf.assign(hit_flood=[], hit_debris=[], status=[])
    g = gdf.to_crs(profile["crs"]).reset_index(drop=True)
    pts = g.geometry.representative_point()
    grow = lambda m: binary_dilation(m, iterations=BUILDING_TOLERANCE_PX) if BUILDING_TOLERANCE_PX else m
    fl = _lookup(pts.x.values, pts.y.values, grow(flood == 1), profile["transform"])
    db = _lookup(pts.x.values, pts.y.values, grow(debris == 1), profile["transform"])
    g["hit_flood"], g["hit_debris"] = fl, db
    g["status"] = np.where(fl | db, "affected", "ok")
    return g.to_crs(4326)


def line_impact(geoms: gpd.GeoSeries, impact: np.ndarray, transform) -> pd.DataFrame:
    """
    For each line: impacted length and the longest continuous impacted stretch, in meters.
    Geoms must be in the raster CRS. Phase 4 reuses this on split road edges.
    """
    rows = []
    for geom in geoms:
        length = geom.length
        n = max(2, math.ceil(length / STEP_M) + 1)
        pts = shapely.line_interpolate_point(geom, np.linspace(0, length, n))
        xy = shapely.get_coordinates(pts)
        hit = _lookup(xy[:, 0], xy[:, 1], impact, transform)
        step = length / (n - 1) if n > 1 else 0
        longest = run = 0
        for h in hit:
            run = run + 1 if h else 0
            longest = max(longest, run)
        rows.append({"length_m": round(length, 1),
                     "hit_m": round(hit.sum() * step, 1),
                     "longest_hit_m": round(longest * step, 1)})
    return pd.DataFrame(rows)


def classify_roads(roads: gpd.GeoDataFrame, impact: np.ndarray, profile: dict) -> gpd.GeoDataFrame:
    if roads.empty:
        return roads.assign(length_m=[], hit_m=[], longest_hit_m=[], status=[])
    g = roads.to_crs(profile["crs"]).reset_index(drop=True)
    m = line_impact(g.geometry, impact, profile["transform"])
    g = pd.concat([g, m], axis=1)
    g["status"] = "ok"
    g.loc[g["hit_m"] >= ROAD_AFFECTED_M, "status"] = "affected"
    g.loc[g["longest_hit_m"] >= ROAD_CUT_RUN_M, "status"] = "likely_cut"
    return gpd.GeoDataFrame(g, geometry="geometry", crs=profile["crs"]).to_crs(4326)


def classify_bridges(bridges: gpd.GeoDataFrame, impact: np.ndarray, profile: dict) -> gpd.GeoDataFrame:
    """Bridges get 1 pixel tolerance: they cross the channel, where small offsets matter."""
    if bridges.empty:
        return bridges.assign(length_m=[], hit_m=[], longest_hit_m=[], status=[])
    g = bridges.to_crs(profile["crs"]).reset_index(drop=True)
    lines = g.geometry.apply(lambda x: x.exterior if x.geom_type == "Polygon" else x)
    m = line_impact(lines, binary_dilation(impact), profile["transform"])
    g = pd.concat([g, m], axis=1)
    g["status"] = np.where(g["longest_hit_m"] >= BRIDGE_LOST_RUN_M, "likely_lost", "ok")
    return gpd.GeoDataFrame(g, geometry="geometry", crs=profile["crs"]).to_crs(4326)