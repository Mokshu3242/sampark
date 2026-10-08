import json
import logging
import math

import geopandas as gpd

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import cutoff, damage, osm_fetch, repair, road_graph

log = logging.getLogger("flood-watch.map_cutoff")


def _lost_bridge_ways(run_dir) -> set[int]:
    """Lost bridges from Phase 3, so both phases agree."""
    p = run_dir / "damage_bridges.geojson"
    if not p.exists():
        return set()
    b = gpd.read_file(p)
    if b.empty or "status" not in b:
        return set()
    ids = b.loc[b["status"] == "likely_lost", "osm_id"].astype(str)
    return {int(x.split("/")[1]) for x in ids if x.startswith("way/")}


def _points(gdf: gpd.GeoDataFrame, mask) -> gpd.GeoDataFrame:
    return gdf[mask] if len(gdf) else gdf


def _clean(obj):
    """Recursively turn NaN, inf, and numpy numbers into JSON-safe values."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item"):          # numpy scalar -> Python scalar
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def map_cutoff(run_id: str, bbox: list[float]) -> dict:
    registry = RunRegistry(settings.runs_dir)
    run_dir = settings.runs_dir / run_id
    fp, dp = run_dir / "flood_mask.tif", run_dir / "debris_mask.tif"
    if not fp.exists() or not dp.exists():
        registry.set_status(run_id, "failed", "missing flood/debris masks")
        raise RuntimeError("Run Phase 2 first.")

    # 1. Masks and road graph
    registry.set_status(run_id, "building_graph")
    flood, profile = damage.load_mask(fp)
    debris, _ = damage.load_mask(dp)
    impact = (flood == 1) | (debris == 1)
    epsg = profile["crs"].to_epsg()

    G, edges, exits = road_graph.build(osm_fetch.fetch(run_dir, bbox), bbox, epsg)
    if G.number_of_edges() == 0:
        registry.set_status(run_id, "failed", "no vehicle roads in OSM")
        raise RuntimeError("No vehicle roads found in this area.")
    snapper = road_graph.Snapper(G, epsg)

    # 2. Edge damage
    registry.set_status(run_id, "classifying_edges")
    lost = _lost_bridge_ways(run_dir)
    edges = cutoff.classify_edges(edges, impact, profile, lost)

    # 3. Villages and help targets
    registry.set_status(run_id, "targets")
    layers = osm_fetch.load_layers(run_dir, bbox)
    sp = run_dir / "settlements.geojson"
    settlements = gpd.read_file(sp) if sp.exists() else layers["settlements"]

    fac = layers["facilities"]
    if len(fac):
        hosp_mask = (fac.get("amenity") == "hospital") | (fac.get("healthcare") == "hospital")
        hospitals = _points(fac, hosp_mask)
    else:
        hospitals = fac
    st = layers["settlements"]
    towns = _points(st, st["place"].isin(["town", "city"])) if len(st) else st

    def snap_set(gdf):
        if not len(gdf):
            return set()
        nodes, _ = snapper.snap(gdf.geometry.x.values, gdf.geometry.y.values)
        return {n for n in nodes if n is not None}

    hospital_nodes = snap_set(hospitals)
    town_nodes = snap_set(towns)
    help_nodes = hospital_nodes | town_nodes | exits
    if not help_nodes:
        raise RuntimeError("No hospitals, towns, or exits reachable on the road network.")

    # 4. Routing before and after
    registry.set_status(run_id, "routing")
    s = cutoff.analyze(G, edges, settlements, help_nodes, hospital_nodes, snapper)

    # 5. Write outputs
    registry.set_status(run_id, "writing")
    keep_s = ["name", "place", "buildings_total", "buildings_affected", "damage_score", "status",
              "dist_before_km", "dist_after_km", "dist_increase_km",
              "hospital_before_km", "hospital_after_km", "road_snap_m", "geometry"]
    s[[c for c in keep_s if c in s]].to_file(run_dir / "cutoff.geojson", driver="GeoJSON")

    keep_e = ["u", "v", "way_id", "highway", "name", "is_bridge", "length_m", "hit_m",
              "longest_hit_m", "status", "geometry"]
    edges[[c for c in keep_e if c in edges]].to_file(run_dir / "road_edges.geojson", driver="GeoJSON")
    registry.set_status(run_id, "repair_plan")
    plan = repair.plan(G, edges, help_nodes, s, snapper, epsg)
    if len(plan):
        out = plan.copy()
        out["villages_reconnected"] = out["villages_reconnected"].apply(", ".join)  # GeoJSON can't store lists
        out.to_file(run_dir / "repair_plan.geojson", driver="GeoJSON")
    plan_records = plan.drop(columns="geometry").to_dict(orient="records") if len(plan) else []

    def count(status: str) -> int:
        return int((s["status"] == status).sum())

    gone = edges["status"].isin(cutoff.REMOVED)
    ranked = s[s["status"].isin(["cut_off", "longer_route"])]
    ranked_cols = [c for c in ["name", "status", "buildings_total", "damage_score",
                               "dist_before_km", "dist_after_km", "dist_increase_km", "road_snap_m"]
                   if c in ranked]

    stats = {
        "network": "vehicle roads (incl. tracks)",
        "settlements_total": len(s),
        "cut_off": count("cut_off"),
        "longer_route": count("longer_route"),
        "ok": count("ok"),
        "pre_isolated": count("pre_isolated"),
        "targets": {"hospitals": len(hospital_nodes), "towns": len(town_nodes), "map_exits": len(exits)},
        "road_edges_total": len(edges),
        "road_edges_removed": int(gone.sum()),
        "road_km_removed": round(float(edges.loc[gone, "length_m"].sum()) / 1000, 2),
        "bridges_lost_used": len(lost),
        "repair_steps": len(plan_records),
        "repair_plan": plan_records,
        "ranked": ranked[ranked_cols].head(50).to_dict(orient="records"),
        "rules": {
            "help": "hospital, town/city, or road leaving the map area",
            "longer_route": f"after > {cutoff.LONGER_ROUTE_RATIO}x before and +{cutoff.LONGER_ROUTE_EXTRA_M / 1000:.0f} km",
            "edge_cut": f"{damage.ROAD_CUT_RUN_M} m continuous, or {int(cutoff.SHORT_EDGE_CUT_FRAC * 100)}% of a short edge",
            "bridges": "lost bridges taken from Phase 3",
            "snap_max_m": road_graph.SNAP_MAX_M,
        },
    }

    stats = _clean(stats)   # NaN / inf -> None, numpy -> Python
    (run_dir / "cutoff_stats.json").write_text(
        json.dumps(stats, indent=2, allow_nan=False, ensure_ascii=False), encoding="utf-8")
    registry.set_status(run_id, "cutoff_done", f"{stats['cut_off']} cut off, {stats['longer_route']} longer route")
    log.info(f"Cut off {stats['cut_off']}, longer route {stats['longer_route']}, "
             f"{stats['road_km_removed']} km of road removed")
    return stats