import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pipeline import osm_fetch

log = logging.getLogger("flood-watch.refwater")

LINE_BUFFER_M = {"river": 25, "canal": 10, "stream": 6}


def water_polygons(bbox: list[float], target_crs, run_dir: Path) -> tuple[gpd.GeoSeries, gpd.GeoSeries, str]:
    """Returns (all_water, main_river, status) in target CRS. Never raises."""
    empty = gpd.GeoSeries([], crs=target_crs)
    try:
        gdf = osm_fetch.load_layers(run_dir, bbox)["water"]
    except Exception as e:
        log.warning(f"OSM water failed: {e}")
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
    return all_water, main_river, f"ok via overpass snapshot ({len(gdf)} features, {len(main_river)} river segments)"