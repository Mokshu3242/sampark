"""Downstream flow tracing on the Copernicus DEM (D8). Bonus feature, not part of the damage maps."""
import json
import logging
import math
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString

from app.core.jsonsafe import clean

log = logging.getLogger("flood-watch.hydro")

# pysheds dirmap order is (N, NE, E, SE, S, SW, W, NW)
DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
STEP = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
        4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}   # (d_row, d_col)
MAX_STEPS = 50_000     # safety cap
SNAP_PX = 5            # snap a click to the main channel within 5 px (100 m at 20 m)
SIMPLIFY_DEG = 0.0001  # about 10 m
GEOD = Geod(ellps="WGS84")
ROAD_KIND = {"likely_cut": "road_cut", "bridge_lost": "bridge_lost", "affected": "road_affected"}


if not hasattr(np, "in1d"):
    np.in1d = lambda a, b, *args, **kw: np.isin(np.ravel(a), b, *args, **kw)

def _fill_nodata(dem: np.ndarray) -> np.ndarray:
    """Fill holes with the nearest valid height (no fake ridges)."""
    bad = ~np.isfinite(dem)
    if bad.all():
        raise RuntimeError("DEM has no valid pixels")
    if not bad.any():
        return dem
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    return dem[tuple(idx)]


def build_hydro(run_dir: Path, force: bool = False) -> tuple[Path, Path]:
    """Flow direction + accumulation, computed once per run and cached."""
    fdir_path, facc_path = run_dir / "flow_dir.tif", run_dir / "flow_acc.tif"
    if fdir_path.exists() and facc_path.exists() and not force:
        return fdir_path, facc_path

    dem_path = run_dir / "dem.tif"
    if not dem_path.exists():
        raise RuntimeError("dem.tif missing for this run")

    from pysheds.grid import Grid
    from pysheds.sview import Raster

    with rasterio.open(dem_path) as src:
        arr = src.read(1).astype("float64")
        profile = src.profile.copy()
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
    arr[arr < -500] = np.nan               # sea-level junk / -32768 leftovers
    arr = _fill_nodata(arr)

    log.info("Computing flow direction (first time only, about 30 s)")
    grid = Grid.from_raster(str(dem_path))
    base = grid.read_raster(str(dem_path))
    dem = Raster(arr, viewfinder=base.viewfinder)

    dem = grid.fill_pits(dem)
    dem = grid.fill_depressions(dem)
    dem = grid.resolve_flats(dem)
    fdir = grid.flowdir(dem, dirmap=DIRMAP)
    facc = grid.accumulation(fdir, dirmap=DIRMAP)

    for k in ("nodata", "blockxsize", "blockysize", "tiled"):
        profile.pop(k, None)
    profile.update(count=1, compress="deflate")
    with rasterio.open(fdir_path, "w", **{**profile, "dtype": "int32", "nodata": 0}) as ds:
        ds.write(np.asarray(fdir).astype("int32"), 1)
    with rasterio.open(facc_path, "w", **{**profile, "dtype": "float32", "nodata": -1}) as ds:
        ds.write(np.asarray(facc).astype("float32"), 1)

    log.info("Wrote flow_dir.tif and flow_acc.tif")
    return fdir_path, facc_path

def _pixel_area_m2(T, crs, lat: float) -> float:
    """Pixel area in m², for projected (metres) or geographic (degrees) DEMs."""
    if crs.is_projected:
        return abs(T.a * T.e)
    m_per_deg_lat = 110_574.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(lat))
    return abs(T.a) * m_per_deg_lon * abs(T.e) * m_per_deg_lat


def trace(run_dir: Path, lon: float, lat: float, snap_px: int = SNAP_PX) -> dict:
    """Walk downstream from a click. Returns a GeoJSON Feature (LineString in lon/lat)."""
    fdir_path, facc_path = build_hydro(run_dir)
    with rasterio.open(fdir_path) as ds:
        fdir, T, crs = ds.read(1), ds.transform, ds.crs
        H, W = ds.height, ds.width
    with rasterio.open(facc_path) as ds:
        facc = ds.read(1)

    x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(lon, lat)
    cf, rf = ~T * (x, y)
    r, c = int(np.floor(rf)), int(np.floor(cf))
    if not (0 <= r < H and 0 <= c < W):
        raise ValueError("That point is outside this run's area")

    # Snap to the strongest channel nearby, so a click near the river follows the river
    r0, r1 = max(0, r - snap_px), min(H, r + snap_px + 1)
    c0, c1 = max(0, c - snap_px), min(W, c + snap_px + 1)
    sub = facc[r0:r1, c0:c1]
    dr, dc = np.unravel_index(int(np.argmax(sub)), sub.shape)
    snapped = (r0 + dr, c0 + dc) != (r, c)
    r, c = r0 + dr, c0 + dc

    rows, cols, seen = [r], [c], {(r, c)}
    for _ in range(MAX_STEPS):
        step = STEP.get(int(fdir[r, c]))
        if step is None:                       # pit, flat or nodata
            break
        nr, nc = r + step[0], c + step[1]
        if not (0 <= nr < H and 0 <= nc < W) or (nr, nc) in seen:
            break
        seen.add((nr, nc))
        rows.append(nr); cols.append(nc)
        r, c = nr, nc

    if len(rows) < 2:
        raise ValueError("Water from that point does not flow anywhere on this DEM. Click closer to a river.")

    xs, ys = rasterio.transform.xy(T, rows, cols)
    lons, lats = Transformer.from_crs(crs, 4326, always_xy=True).transform(xs, ys)
    line = LineString(zip(lons, lats))
    length_km = GEOD.geometry_length(line) / 1000
    simple = line.simplify(SIMPLIFY_DEG, preserve_topology=False)
    if len(simple.coords) < 2:
        simple = line

    end_lon, end_lat = lons[-1], lats[-1]
    left_map = rows[-1] in (0, H - 1) or cols[-1] in (0, W - 1)
    return clean({
        "type": "Feature",
        "properties": {
            "click_lon": lon, "click_lat": lat,
            "start_lon": round(lons[0], 6), "start_lat": round(lats[0], 6),
            "end_lon": round(end_lon, 6), "end_lat": round(end_lat, 6),
            "length_km": round(length_km, 2),
            "snapped_to_channel": bool(snapped),
            "ends": "leaves the map" if left_map else "stops in a basin",
            "upstream_area_km2": round(float(facc[rows[0], cols[0]]) * _pixel_area_m2(T, crs, lats[0]) / 1e6, 1),
        },
        "geometry": {"type": "LineString",
                     "coordinates": [[round(a, 6), round(b, 6)] for a, b in simple.coords]},
    })


def villages_along(feature: dict, run_dir: Path, buffer_m: float = 500.0) -> list[dict]:
    """Settlements within buffer_m of the path, in downstream order."""
    import geopandas as gpd
    from shapely.geometry import shape

    p = run_dir / "cutoff.geojson"
    if not p.exists():
        return []
    gdf = gpd.read_file(p)
    if gdf.empty:
        return []
    gdf = gdf.to_crs(4326)

    line = gpd.GeoSeries([shape(feature["geometry"])], crs=4326)
    utm = line.estimate_utm_crs()                 # true metres at this latitude
    line_m = line.to_crs(utm).iloc[0]
    pts = gdf.to_crs(utm)

    d = pts.distance(line_m)
    hits = pts[d <= buffer_m].copy()
    if hits.empty:
        return []
    hits["_t"] = hits.geometry.apply(line_m.project)
    hits["_d"] = d[hits.index]
    hits = hits.sort_values("_t")

    out = []
    for _, row in hits.iterrows():
        out.append({
            "name": row.get("name"),
            "status": row.get("status"),
            "buildings_total": int(row.get("buildings_total") or 0),
            "km_along_path": round(float(row["_t"]) / 1000, 2),
            "distance_to_path_m": int(round(float(row["_d"]))),
        })
    return clean(out)

def corridor_events(feature: dict, run_dir: Path, buffer_m: float = 300.0,
                    village_buffer_m: float = 500.0) -> dict:
    """Observed damage along the traced path, ordered by distance downstream.
    Everything here comes from this run's satellite outputs. Only the playback timing is assumed."""
    import geopandas as gpd
    from shapely.geometry import shape

    line = gpd.GeoSeries([shape(feature["geometry"])], crs=4326)
    utm = line.estimate_utm_crs()
    lm = line.to_crs(utm).iloc[0]
    coords = list(lm.coords)
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    zone = lm.buffer(buffer_m)
    vzone = lm.buffer(village_buffer_m)

    events: list[dict] = []
    geoms = []

    def add(geom, **props):
        events.append(props)
        geoms.append(geom.representative_point())

    # Roads: cut, bridge lost, affected
    p = run_dir / "road_edges.geojson"
    if p.exists():
        r = gpd.read_file(p, where="status IN ('likely_cut', 'bridge_lost', 'affected')")
        r = r[r["status"].isin(list(ROAD_KIND))]
        if not r.empty:
            r = r.to_crs(utm)
            r = r[r.intersects(zone)]
            for _, row in r.iterrows():
                add(row.geometry, kind=ROAD_KIND[row["status"]],
                    name=row.get("name"), length_m=round(float(row.geometry.length)))

    # Debris and flood water: only the part inside the corridor counts
    for fname, kind in (("debris_polygons.geojson", "debris"), ("flood_polygons.geojson", "flood")):
        p = run_dir / fname
        if not p.exists():
            continue
        g = gpd.read_file(p)
        if g.empty:
            continue
        g = g.to_crs(utm)
        g = g[g.intersects(zone)]
        for geom in g.geometry:
            part = geom.intersection(zone)
            if part.is_empty:
                continue
            add(part, kind=kind, area_ha=round(part.area / 10_000, 2))

    # Villages, with damaged building counts from damage_stats
    dmg = {}
    ds = run_dir / "damage_stats.json"
    if ds.exists():
        for s in json.loads(ds.read_text(encoding="utf-8")).get("per_settlement", []):
            dmg[s.get("name")] = s
    p = run_dir / "cutoff.geojson"
    if p.exists():
        v = gpd.read_file(p)
        if not v.empty:
            v = v.to_crs(utm)
            v = v[v.intersects(vzone)]
            for _, row in v.iterrows():
                d = dmg.get(row.get("name"), {})
                add(row.geometry, kind="village", name=row.get("name"), status=row.get("status"),
                    buildings_total=int(row.get("buildings_total") or 0),
                    buildings_affected=int(d.get("buildings_affected") or 0))

    if events:
        pts = gpd.GeoSeries(geoms, crs=utm)
        ll = pts.to_crs(4326)
        for e, pt, g in zip(events, pts, ll):
            e["km"] = round(lm.project(pt) / 1000, 3)
            e["lon"], e["lat"] = round(g.x, 6), round(g.y, 6)
        events.sort(key=lambda e: e["km"])

    def n(k):
        return sum(1 for e in events if e["kind"] == k)

    totals = {
        "villages": n("village"),
        "villages_cut_off": sum(1 for e in events if e["kind"] == "village" and e.get("status") == "cut_off"),
        "buildings_affected": sum(e.get("buildings_affected", 0) for e in events if e["kind"] == "village"),
        "roads_cut": n("road_cut"),
        "bridges_lost": n("bridge_lost"),
        "roads_affected": n("road_affected"),
        "debris_ha": round(sum(e.get("area_ha", 0) for e in events if e["kind"] == "debris"), 1),
        "flood_ha": round(sum(e.get("area_ha", 0) for e in events if e["kind"] == "flood"), 1),
    }
    return clean({"cum_km": [round(c / 1000, 3) for c in cum], "events": events,
                  "totals": totals, "buffer_m": buffer_m, "village_buffer_m": village_buffer_m})