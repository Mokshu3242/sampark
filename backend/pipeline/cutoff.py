import logging
import math

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from pipeline import damage
from pipeline.road_graph import Snapper

log = logging.getLogger("flood-watch.cutoff")

LONGER_ROUTE_RATIO = 1.5
LONGER_ROUTE_EXTRA_M = 3000.0
SHORT_EDGE_CUT_FRAC = 0.6       # a short edge mostly under impact counts as cut
REMOVED = {"likely_cut", "bridge_lost"}


def classify_edges(edges: gpd.GeoDataFrame, impact: np.ndarray, profile: dict,
                   lost_bridge_ways: set[int]) -> gpd.GeoDataFrame:
    """Per-edge status. Lost bridges come from Phase 3 so both phases agree."""
    g = edges.to_crs(profile["crs"]).reset_index(drop=True)
    m = damage.line_impact(g.geometry, impact, profile["transform"])
    g = pd.concat([g.drop(columns=["length_m"]), m], axis=1)

    cut = (g["longest_hit_m"] >= damage.ROAD_CUT_RUN_M) | \
          ((g["hit_m"] > 0) & (g["hit_m"] >= SHORT_EDGE_CUT_FRAC * g["length_m"]))
    g["status"] = "ok"
    g.loc[g["hit_m"] > 0, "status"] = "affected"
    g.loc[cut, "status"] = "likely_cut"
    g.loc[g["is_bridge"] & g["way_id"].isin(lost_bridge_ways), "status"] = "bridge_lost"
    return gpd.GeoDataFrame(g, geometry="geometry", crs=profile["crs"]).to_crs(4326)


def _dist(G: nx.Graph, sources: set[int]) -> dict[int, float]:
    sources = [s for s in sources if s in G]
    return nx.multi_source_dijkstra_path_length(G, sources, weight="length_m") if sources else {}


def _status(before: float, after: float) -> str:
    if not math.isfinite(before):
        return "pre_isolated"
    if not math.isfinite(after):
        return "cut_off"
    if after > before * LONGER_ROUTE_RATIO and after - before > LONGER_ROUTE_EXTRA_M:
        return "longer_route"
    return "ok"


def _km(series: pd.Series) -> list:
    """Meters -> km, inf -> None so the JSON stays valid."""
    return [round(v / 1000, 2) if math.isfinite(v) else None for v in series]


def analyze(G: nx.Graph, edges: gpd.GeoDataFrame, settlements: gpd.GeoDataFrame,
            help_nodes: set[int], hospital_nodes: set[int], snapper: Snapper) -> gpd.GeoDataFrame:
    s = settlements.to_crs(4326).reset_index(drop=True).copy()
    nodes, snap_d = snapper.snap(s.geometry.x.values, s.geometry.y.values)
    s["node"], s["road_snap_m"] = nodes, np.round(snap_d, 0)

    G_post = G.copy()
    gone = edges[edges["status"].isin(REMOVED)]
    G_post.remove_edges_from([(u, v) for u, v in zip(gone["u"], gone["v"]) if G_post.has_edge(u, v)])
    log.info(f"Removed {G.number_of_edges() - G_post.number_of_edges()} edges after the flood")

    pre, post = _dist(G, help_nodes), _dist(G_post, help_nodes)
    pre_h, post_h = _dist(G, hospital_nodes), _dist(G_post, hospital_nodes)

    get = lambda d: [d.get(n, math.inf) if n is not None else math.inf for n in s["node"]]
    before, after = pd.Series(get(pre)), pd.Series(get(post))
    s["status"] = [_status(b, a) if n is not None else "pre_isolated"
                   for b, a, n in zip(before, after, s["node"])]
    s["dist_before_km"], s["dist_after_km"] = _km(before), _km(after)
    s["dist_increase_km"] = [round((a - b) / 1000, 2) if math.isfinite(a) and math.isfinite(b) else None
                             for b, a in zip(before, after)]
    s["hospital_before_km"] = _km(pd.Series(get(pre_h)))
    s["hospital_after_km"] = _km(pd.Series(get(post_h)))

    if "buildings_total" not in s:
        s["buildings_total"] = 0
    rank = {"cut_off": 0, "longer_route": 1, "ok": 2, "pre_isolated": 3}
    s["_r"] = s["status"].map(rank)
    return s.sort_values(["_r", "buildings_total"], ascending=[True, False]).drop(columns=["_r", "node"]).reset_index(drop=True)