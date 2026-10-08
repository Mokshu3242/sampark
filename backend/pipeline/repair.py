import logging

import geopandas as gpd
import networkx as nx
import numpy as np
from shapely.ops import unary_union

from pipeline.cutoff import REMOVED
from pipeline.road_graph import Snapper

log = logging.getLogger("flood-watch.repair")

MAX_STEPS = 20


def _stretches(edges: gpd.GeoDataFrame) -> list[gpd.GeoDataFrame]:
    """Group removed edges that share nodes into one repair job each."""
    gone = edges[edges["status"].isin(REMOVED)]
    H = nx.Graph()
    H.add_edges_from(zip(gone["u"], gone["v"]))
    return [gone[gone["u"].isin(c) & gone["v"].isin(c)] for c in nx.connected_components(H)]


def _islands(G: nx.Graph, help_nodes: set[int]) -> tuple[dict[int, int], set[int]]:
    """Label every node with its connected piece; note which pieces can reach help."""
    comp_of, reach = {}, set()
    for cid, comp in enumerate(nx.connected_components(G)):
        for n in comp:
            comp_of[n] = cid
        if comp & help_nodes:
            reach.add(cid)
    return comp_of, reach


def plan(G: nx.Graph, edges: gpd.GeoDataFrame, help_nodes: set[int],
         villages: gpd.GeoDataFrame, snapper: Snapper, epsg: int) -> gpd.GeoDataFrame:
    """Greedy repair order: each step reopens the stretch that reconnects the most buildings."""
    v = villages[villages["status"] == "cut_off"].copy()
    if v.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    nodes, _ = snapper.snap(v.geometry.x.values, v.geometry.y.values)
    v["node"] = nodes
    v = v[v["node"].notna()]

    stretches = _stretches(edges)
    G_cur = G.copy()
    G_cur.remove_edges_from([(u, w) for st in stretches for u, w in zip(st["u"], st["v"]) if G_cur.has_edge(u, w)])

    # For naming each stretch by its nearest village
    all_v = villages.to_crs(epsg)
    remaining = list(range(len(stretches)))
    done, steps, cumulative = set(), [], 0

    for _ in range(MAX_STEPS):
        comp_of, reach = _islands(G_cur, help_nodes)

        # Buildings and names per stranded island
        bld, names = {}, {}
        for _, r in v.iterrows():
            c = comp_of.get(r["node"])
            if r["name"] in done or c is None or c in reach:
                continue
            bld[c] = bld.get(c, 0) + int(r.get("buildings_total", 0) or 0)
            names.setdefault(c, []).append(r["name"])
        if not bld:
            break

        best = None
        for i in remaining:
            st = stretches[i]
            touched = {comp_of[n] for n in set(st["u"]) | set(st["v"]) if n in comp_of}
            stranded = [c for c in touched if c in bld]
            if not stranded:
                continue
            opens = bool(touched & reach)
            key = (opens, sum(bld[c] for c in stranded), -float(st["length_m"].sum()))
            if best is None or key > best[0]:
                best = (key, i, stranded, opens)
        if best is None:
            break

        (opens, gain, _), i, stranded, _ = best
        st = stretches[i]
        G_cur.add_edges_from((u, w, {"length_m": L}) for u, w, L in zip(st["u"], st["v"], st["length_m"]))
        remaining.remove(i)

        newly = [n for c in stranded for n in names[c]] if opens else []
        done |= set(newly)
        bld_now = gain if opens else 0
        cumulative += bld_now

        geom = unary_union(list(st.geometry))
        centroid = gpd.GeoSeries([geom], crs=4326).to_crs(epsg).iloc[0].centroid
        near = all_v.iloc[int(np.argmin(all_v.distance(centroid)))]["name"] if len(all_v) else None
        road = st["name"].dropna().mode()
        steps.append({
            "rank": len(steps) + 1,
            "near": near,
            "road": road.iloc[0] if len(road) else None,
            "length_m": round(float(st["length_m"].sum()), 0),
            "has_bridge": bool(st["is_bridge"].any()),
            "action": "reconnects villages" if opens else "links isolated areas, needed before a later step",
            "villages_reconnected": newly,
            "villages_count": len(newly),
            "buildings_reconnected": bld_now,
            "cumulative_buildings": cumulative,
            "geometry": geom,
        })

    log.info(f"Repair plan: {len(steps)} steps, {cumulative} buildings reconnected")
    if not steps:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    return merge_packages(gpd.GeoDataFrame(steps, geometry="geometry", crs=4326))

def merge_packages(plan: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Fold 'link only' steps into the next step that reconnects villages, as one repair package."""
    if plan.empty:
        return plan
    out, pending = [], []
    for _, r in plan.iterrows():
        pending.append(r)
        if r["villages_count"] == 0:
            continue
        geoms = [p.geometry for p in pending]
        out.append({
            "rank": len(out) + 1,
            "near": r["near"],
            "road": next((p["road"] for p in pending if p["road"]), None),
            "stretches": len(pending),
            "length_m": round(sum(p["length_m"] for p in pending), 0),
            "has_bridge": any(p["has_bridge"] for p in pending),
            "villages_reconnected": r["villages_reconnected"],
            "villages_count": r["villages_count"],
            "buildings_reconnected": r["buildings_reconnected"],
            "cumulative_buildings": r["cumulative_buildings"],
            "geometry": unary_union(geoms),
        })
        pending = []
    return gpd.GeoDataFrame(out, geometry="geometry", crs=4326)