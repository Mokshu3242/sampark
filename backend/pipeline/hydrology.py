"""Downstream flow tracing on the Copernicus DEM (D8). Bonus feature, not part of the damage maps."""
import json
import logging
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from rasterio.windows import Window
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
TILE = 256             # flow_dir is read in tiles this size along the path
MAX_TILES = 64         # tile cache cap (64 x 256 x 256 x int32 = 16 MB)
GEOD = Geod(ellps="WGS84")
ROAD_KIND = {"likely_cut": "road_cut", "bridge_lost": "bridge_lost", "affected": "road_affected"}
DAMAGED_STATUSES = ("likely_cut", "bridge_lost", "affected")

if not hasattr(np, "in1d"):
    np.in1d = lambda a, b, *args, **kw: np.isin(np.ravel(a), b, *args, **kw)


def rss_mb() -> float:
    """Current memory of this process in MB (Linux). -1 elsewhere."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return -1.0


def _fill_nodata(dem: np.ndarray) -> np.ndarray:
    """Fill holes with the nearest valid height (no fake ridges)."""
    bad = ~np.isfinite(dem)
    if bad.all():
        raise RuntimeError("DEM has no valid pixels")
    if not bad.any():
        return dem
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    return dem[tuple(idx)]


def hydro_ready(run_dir: Path) -> bool:
    return (run_dir / "flow_dir.tif").exists() and (run_dir / "flow_acc.tif").exists()


def build_hydro(run_dir: Path, force: bool = False) -> tuple[Path, Path]:
    """Flow direction + accumulation, computed once per run and cached. Heavy: pipeline only."""
    fdir_path, facc_path = run_dir / "flow_dir.tif", run_dir / "flow_acc.tif"
    if hydro_ready(run_dir) and not force:
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

    log.info(f"Computing flow direction (first time only, about 30 s). Memory {rss_mb()} MB")
    grid = Grid.from_raster(str(dem_path))
    base = grid.read_raster(str(dem_path))
    dem = Raster(arr, viewfinder=base.viewfinder)
    del arr

    dem = grid.fill_pits(dem)
    dem = grid.fill_depressions(dem)
    dem = grid.resolve_flats(dem)
    fdir = grid.flowdir(dem, dirmap=DIRMAP)
    del dem
    facc = grid.accumulation(fdir, dirmap=DIRMAP)

    for k in ("nodata", "blockxsize", "blockysize", "tiled"):
        profile.pop(k, None)
    profile.update(count=1, compress="deflate", tiled=True, blockxsize=TILE, blockysize=TILE)
    with rasterio.open(fdir_path, "w", **{**profile, "dtype": "int32", "nodata": 0}) as ds:
        ds.write(np.asarray(fdir).astype("int32"), 1)
    with rasterio.open(facc_path, "w", **{**profile, "dtype": "float32", "nodata": -1}) as ds:
        ds.write(np.asarray(facc).astype("float32"), 1)

    log.info(f"Wrote flow_dir.tif and flow_acc.tif. Memory {rss_mb()} MB")
    return fdir_path, facc_path


def _pixel_area_m2(T, crs, lat: float) -> float:
    """Pixel area in m², for projected (metres) or geographic (degrees) DEMs."""
    if crs.is_projected:
        return abs(T.a * T.e)
    m_per_deg_lat = 110_574.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(lat))
    return abs(T.a) * m_per_deg_lon * abs(T.e) * m_per_deg_lat


class _TileReader:
    """Reads one band in small tiles on demand, so a trace never loads the whole raster."""

    def __init__(self, ds):
        self.ds, self.H, self.W = ds, ds.height, ds.width
        self.cache: dict[tuple[int, int], np.ndarray] = {}

    def get(self, r: int, c: int) -> int:
        key = (r // TILE, c // TILE)
        tile = self.cache.get(key)
        if tile is None:
            if len(self.cache) >= MAX_TILES:
                self.cache.clear()
            r0, c0 = key[0] * TILE, key[1] * TILE
            win = Window(c0, r0, min(TILE, self.W - c0), min(TILE, self.H - r0))
            tile = self.cache[key] = self.ds.read(1, window=win)
        return int(tile[r - key[0] * TILE, c - key[1] * TILE])


def trace(run_dir: Path, lon: float, lat: float, snap_px: int = SNAP_PX) -> dict:
    """Walk downstream from a click. Returns a GeoJSON Feature (LineString in lon/lat).
    Reads only small windows of the flow rasters, so memory stays low on small servers."""
    if not hydro_ready(run_dir):
        raise RuntimeError("Flow data not built for this run. It is made when the pipeline finishes.")
    fdir_path, facc_path = run_dir / "flow_dir.tif", run_dir / "flow_acc.tif"
    mem0 = rss_mb()

    with rasterio.open(fdir_path) as fds, rasterio.open(facc_path) as ads:
        T, crs, H, W = fds.transform, fds.crs, fds.height, fds.width

        x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(lon, lat)
        cf, rf = ~T * (x, y)
        r, c = int(np.floor(rf)), int(np.floor(cf))
        if not (0 <= r < H and 0 <= c < W):
            raise ValueError("That point is outside this run's area")

        # Snap to the strongest channel nearby, so a click near the river follows the river
        r0, r1 = max(0, r - snap_px), min(H, r + snap_px + 1)
        c0, c1 = max(0, c - snap_px), min(W, c + snap_px + 1)
        sub = ads.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        dr, dc = np.unravel_index(int(np.argmax(sub)), sub.shape)
        snapped = (r0 + dr, c0 + dc) != (r, c)
        r, c = r0 + dr, c0 + dc
        start_acc = float(sub[dr, dc])

        fdir = _TileReader(fds)
        rows, cols, seen = [r], [c], {(r, c)}
        for _ in range(MAX_STEPS):
            step = STEP.get(fdir.get(r, c))
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

    log.info(f"Flow trace: {len(rows)} px, {length_km:.1f} km. Memory {mem0} -> {rss_mb()} MB")
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
            "upstream_area_km2": round(start_acc * _pixel_area_m2(T, crs, lats[0]) / 1e6, 1),
        },
        "geometry": {"type": "LineString",
                     "coordinates": [[round(a, 6), round(b, 6)] for a, b in simple.coords]},
    })


# ---------- Corridor helpers: small files + in-memory cache (fast on tiny CPUs) ----------

def damaged_roads_path(run_dir: Path) -> Path:
    """Only damaged road edges (a few hundred, not 120k). Built once, cached on disk."""
    import geopandas as gpd

    src, out = run_dir / "road_edges.geojson", run_dir / "road_edges_damaged.geojson"
    if src.exists() and (not out.exists() or out.stat().st_mtime < src.stat().st_mtime):
        where = "status IN (" + ", ".join(f"'{s}'" for s in DAMAGED_STATUSES) + ")"
        g = gpd.read_file(src, where=where)
        cols = [c for c in ("status", "highway", "name", "is_bridge", "geometry") if c in g]
        g[cols].to_file(out, driver="GeoJSON")
        log.info(f"Wrote {out.name}: {len(g)} damaged edges")
    return out


@lru_cache(maxsize=16)
def _load(path: str, mtime: float):
    import geopandas as gpd
    g = gpd.read_file(path)
    if g.crs is not None and g.crs.to_epsg() != 4326:
        g = g.to_crs(4326)
    return g


def _cached(p: Path):
    """Read a layer once per run and keep it in memory. Callers must not modify the result."""
    return _load(str(p), p.stat().st_mtime)


def _near(g, bbox):
    """Rows whose bounds touch bbox (lon/lat)."""
    if g.empty:
        return g
    minx, miny, maxx, maxy = bbox
    return g.cx[minx:maxx, miny:maxy]


def warm_cache(run_dir: Path) -> None:
    """Load the corridor layers for one run ahead of time (call after restore)."""
    try:
        if (run_dir / "road_edges.geojson").exists():
            _cached(damaged_roads_path(run_dir))
        for name in ("debris_polygons.geojson", "flood_polygons.geojson", "cutoff.geojson"):
            if (run_dir / name).exists():
                _cached(run_dir / name)
    except Exception as e:
        log.warning(f"Cache warm-up failed for {run_dir.name}: {e}")


def villages_along(feature: dict, run_dir: Path, buffer_m: float = 500.0) -> list[dict]:
    """Settlements within buffer_m of the path, in downstream order."""
    import geopandas as gpd
    from shapely.geometry import shape

    p = run_dir / "cutoff.geojson"
    if not p.exists():
        return []
    gdf = _cached(p)
    if gdf.empty:
        return []

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
    bbox = tuple(float(v) for v in gpd.GeoSeries([vzone], crs=utm).to_crs(4326).total_bounds)

    events: list[dict] = []
    geoms = []

    def add(geom, **props):
        events.append(props)
        geoms.append(geom.representative_point())

    # Roads: cut, bridge lost, affected (small pre-filtered file)
    if (run_dir / "road_edges.geojson").exists():
        r = _near(_cached(damaged_roads_path(run_dir)), bbox)
        if not r.empty:
            r = r[r["status"].isin(list(ROAD_KIND))].to_crs(utm)
            r = r[r.intersects(zone)]
            for _, row in r.iterrows():
                add(row.geometry, kind=ROAD_KIND[row["status"]],
                    name=row.get("name"), length_m=round(float(row.geometry.length)))

    # Debris and flood water: only the part inside the corridor counts
    for fname, kind in (("debris_polygons.geojson", "debris"), ("flood_polygons.geojson", "flood")):
        p = run_dir / fname
        if not p.exists():
            continue
        g = _near(_cached(p), bbox)
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
        v = _near(_cached(p), bbox)
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
    log.info(f"Corridor: {len(events)} events. Memory {rss_mb()} MB")
    return clean({"cum_km": [round(c / 1000, 3) for c in cum], "events": events,
                  "totals": totals, "buffer_m": buffer_m, "village_buffer_m": village_buffer_m})