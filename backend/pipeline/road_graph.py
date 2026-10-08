import logging

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import LineString

log = logging.getLogger("flood-watch.roadgraph")

# Roads a jeep or truck can use. "track" is included: in rural Nepal it's usually a jeep road.
VEHICLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "service", "track", "living_street", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
SNAP_MAX_M = 3000.0


def build(osm_data: dict, bbox: list[float], epsg: int) -> tuple[nx.Graph, gpd.GeoDataFrame, set[int]]:
    """
    Vehicle road graph split at every OSM node.
    Returns (graph, edges in EPSG:4326, exit_nodes). Exit nodes lie outside the bbox,
    meaning the road continues to the rest of the country.
    """
    to_m = Transformer.from_crs(4326, epsg, always_xy=True)
    w, s, e, n = bbox
    rows, coords = [], {}

    for el in osm_data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if tags.get("highway") not in VEHICLE:
            continue
        nodes, geom = el.get("nodes"), el.get("geometry")
        if not nodes or not geom or len(nodes) != len(geom):
            continue
        is_bridge = tags.get("bridge") not in (None, "no")
        lons = np.array([p["lon"] for p in geom])
        lats = np.array([p["lat"] for p in geom])
        xs, ys = to_m.transform(lons, lats)
        for nid, lo, la in zip(nodes, lons, lats):
            coords.setdefault(nid, (lo, la))
        for i in range(len(nodes) - 1):
            rows.append({
                "u": nodes[i], "v": nodes[i + 1], "way_id": el["id"],
                "length_m": round(float(np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i])), 2),
                "highway": tags["highway"], "is_bridge": is_bridge, "name": tags.get("name"),
                "geometry": LineString([(lons[i], lats[i]), (lons[i + 1], lats[i + 1])]),
            })

    if not rows:
        return nx.Graph(), gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326), set()

    edges = gpd.GeoDataFrame(rows, crs=4326)
    G = nx.Graph()
    for r in rows:
        u, v = r["u"], r["v"]
        if not G.has_edge(u, v) or r["length_m"] < G[u][v]["length_m"]:
            G.add_edge(u, v, length_m=r["length_m"], way_id=r["way_id"], is_bridge=r["is_bridge"])
    for nid in G.nodes:
        G.nodes[nid]["x"], G.nodes[nid]["y"] = coords[nid]

    exits = {nid for nid in G.nodes
             if not (w <= G.nodes[nid]["x"] <= e and s <= G.nodes[nid]["y"] <= n)}
    log.info(f"Vehicle graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(exits)} exit nodes")
    return G, edges, exits


class Snapper:
    """Nearest graph node in meters, via a KD-tree. Fast for thousands of points."""

    def __init__(self, G: nx.Graph, epsg: int):
        self.to_m = Transformer.from_crs(4326, epsg, always_xy=True)
        self.ids = np.array(list(G.nodes))
        lon = np.array([G.nodes[i]["x"] for i in self.ids])
        lat = np.array([G.nodes[i]["y"] for i in self.ids])
        self.tree = cKDTree(np.column_stack(self.to_m.transform(lon, lat)))

    def snap(self, lons, lats, max_m: float = SNAP_MAX_M) -> tuple[list, np.ndarray]:
        xs, ys = self.to_m.transform(np.asarray(lons), np.asarray(lats))
        d, i = self.tree.query(np.column_stack([xs, ys]))
        nodes = [int(self.ids[k]) if dist <= max_m else None for k, dist in zip(i, d)]
        return nodes, d