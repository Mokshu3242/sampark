import logging

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

log = logging.getLogger("flood-watch.settle")

MAX_ASSIGN_M = 3000.0
WEIGHTS = {"buildings_affected": 1, "roads_cut": 5, "bridges_lost": 10, "facilities_affected": 3}


def _name(row) -> str:
    for k in ("name:en", "name", "name:ne"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return f"unnamed {row.get('place', 'place')} ({row.get('osm_id')})"


def aggregate(settlements: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame], crs) -> gpd.GeoDataFrame:
    """Tie each feature to the nearest village within 3 km and count damage."""
    cols = ["buildings_total", "buildings_affected", "roads_affected", "roads_cut",
            "bridges_lost", "facilities_affected", "damage_score"]
    if settlements.empty:
        return settlements.assign(name=[], **{c: [] for c in cols})

    s = settlements.to_crs(crs).reset_index(drop=True)
    s["name"] = s.apply(_name, axis=1)
    tree = cKDTree(np.column_stack([s.geometry.x, s.geometry.y]))
    counts = {c: np.zeros(len(s), int) for c in cols[:-1]}

    def assign(gdf, key, statuses=None):
        if gdf is None or gdf.empty:
            return
        g = gdf if statuses is None else gdf[gdf["status"].isin(statuses)]
        if g.empty:
            return
        p = g.to_crs(crs).geometry.representative_point()
        d, i = tree.query(np.column_stack([p.x, p.y]), k=1)
        np.add.at(counts[key], i[d <= MAX_ASSIGN_M], 1)

    assign(layers["buildings"], "buildings_total")
    assign(layers["buildings"], "buildings_affected", {"affected"})
    assign(layers["roads"], "roads_affected", {"affected", "likely_cut"})
    assign(layers["roads"], "roads_cut", {"likely_cut"})
    assign(layers["bridges"], "bridges_lost", {"likely_lost"})
    assign(layers["facilities"], "facilities_affected", {"affected"})

    for k, v in counts.items():
        s[k] = v
    s["damage_score"] = sum(s[k] * w for k, w in WEIGHTS.items())
    return s.sort_values("damage_score", ascending=False).reset_index(drop=True).to_crs(4326)