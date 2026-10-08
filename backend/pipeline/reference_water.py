import logging

import geopandas as gpd
import requests
import pandas as pd

log = logging.getLogger("flood-watch.refwater")

OHSOME = "https://api.ohsome.org/v1/elements/geometry"
SNAPSHOT = "2026-07-27"   # last snapshot before the event
FILTER = "waterway in (river, stream, canal) or natural=water or landuse=reservoir"
LINE_BUFFER_M = {"river": 25, "canal": 10, "stream": 6}
HEADERS = {"User-Agent": "flood-watch/0.1 (Multimodal AI Hackathon, educational prototype)"}
OVERPASS = "https://overpass-api.de/api/interpreter"


def fetch_overpass(bbox: list[float]) -> gpd.GeoDataFrame:
    """Pre-event OSM snapshot via Overpass 'date' setting. Backup for ohsome."""
    w, s, e, n = bbox
    q = f"""
    [out:json][timeout:120][date:"{SNAPSHOT}T00:00:00Z"];
    (
      way["waterway"~"^(river|stream|canal)$"]({s},{w},{n},{e});
      way["natural"="water"]({s},{w},{n},{e});
      way["landuse"="reservoir"]({s},{w},{n},{e});
    );
    out geom;
    """
    r = requests.post(OVERPASS, data={"data": q}, headers=HEADERS, timeout=180)
    r.raise_for_status()

    from shapely.geometry import LineString, Polygon
    rows = []
    for el in r.json().get("elements", []):
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(pts) < 2:
            continue
        tags = el.get("tags", {})
        closed = pts[0] == pts[-1] and len(pts) >= 4
        is_area = closed and ("natural" in tags or "landuse" in tags)
        geom = Polygon(pts) if is_area else LineString(pts)
        rows.append({"waterway": tags.get("waterway"), "geometry": geom})
    log.info(f"Overpass water features: {len(rows)}")
    return gpd.GeoDataFrame(rows, crs=4326) if rows else gpd.GeoDataFrame(geometry=[], crs=4326)

def fetch(bbox: list[float]) -> gpd.GeoDataFrame:
    """GET works where POST is blocked on this server."""
    r = requests.get(OHSOME, params={
        "bboxes": ",".join(map(str, bbox)),
        "time": SNAPSHOT,
        "filter": FILTER,
        "properties": "tags",
    }, headers=HEADERS, timeout=180)
    r.raise_for_status()
    feats = r.json().get("features", [])
    log.info(f"OSM water features: {len(feats)}")
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs=4326)
    return gpd.GeoDataFrame.from_features(feats, crs=4326)


def water_polygons(bbox: list[float], target_crs) -> tuple[gpd.GeoSeries, gpd.GeoSeries, str]:
    """
    Returns (water_area, river_lines, status) in the target CRS.
    On failure, returns empty layers so mapping can still run.
    """
    empty = gpd.GeoSeries([], crs=target_crs)
    try:
        gdf, source = None, None
        for name, fn in (("ohsome", fetch), ("overpass", fetch_overpass)):
            try:
                gdf, source = fn(bbox), name
                break
            except Exception as e:
                log.warning(f"OSM via {name} failed: {e}")
        if gdf is None:
            return empty, empty, "failed: ohsome and overpass unavailable"
        if gdf.empty:
            return empty, empty, f"no features ({source})"
    except Exception as e:
        log.warning(f"OSM water fetch failed: {e}")
        return empty, empty, f"failed: {str(e)[:120]}"
    if gdf.empty:
        return empty, empty, "no features"

    gdf = gdf.to_crs(target_crs)
    is_line = gdf.geom_type.isin(["LineString", "MultiLineString"])
    lines, polys = gdf[is_line], gdf[~is_line]

    ww = lines["waterway"] if "waterway" in lines else pd.Series("stream", index=lines.index)
    buffered = lines.geometry.buffer(ww.map(LINE_BUFFER_M).fillna(6).values)

    all_water = gpd.GeoSeries(list(polys.geometry) + list(buffered), crs=target_crs)
    main_river = gpd.GeoSeries(list(buffered[(ww == "river").values]), crs=target_crs)
    return all_water, main_river, f"ok via {source} ({len(gdf)} features, {len(main_river)} river segments)"