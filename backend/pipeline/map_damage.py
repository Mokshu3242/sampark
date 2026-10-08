import json
import logging

import geopandas as gpd

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import damage, osm_fetch, settlement_agg

log = logging.getLogger("flood-watch.map_damage")

OUT_COLS = {
    "buildings": ["osm_id", "name", "building", "hit_flood", "hit_debris", "status", "geometry"],
    "roads": ["osm_id", "name", "highway", "is_bridge", "length_m", "hit_m", "longest_hit_m", "status", "geometry"],
    "bridges": ["osm_id", "name", "highway", "length_m", "hit_m", "longest_hit_m", "status", "geometry"],
    "facilities": ["osm_id", "name", "amenity", "healthcare", "hit_flood", "hit_debris", "status", "geometry"],
}


def _slim(gdf: gpd.GeoDataFrame, cols: list[str]) -> gpd.GeoDataFrame:
    return gdf[[c for c in cols if c in gdf.columns]]


def map_damage(run_id: str, bbox: list[float]) -> dict:
    registry = RunRegistry(settings.runs_dir)
    run_dir = settings.runs_dir / run_id
    fp, dp = run_dir / "flood_mask.tif", run_dir / "debris_mask.tif"
    if not fp.exists() or not dp.exists():
        registry.set_status(run_id, "failed", "missing flood/debris masks")
        raise RuntimeError("Run Phase 2 first.")

    registry.set_status(run_id, "loading_masks")
    flood, profile = damage.load_mask(fp)
    debris, _ = damage.load_mask(dp)
    impact = (flood == 1) | (debris == 1)

    registry.set_status(run_id, "loading_osm")
    layers = osm_fetch.load_layers(run_dir, bbox)

    registry.set_status(run_id, "classifying")
    out = {
        "buildings": damage.classify_points(layers["buildings"], flood, debris, profile),
        "facilities": damage.classify_points(layers["facilities"], flood, debris, profile),
        "roads": damage.classify_roads(layers["roads"], impact, profile),
        "bridges": damage.classify_bridges(layers["bridges"], impact, profile),
    }

    registry.set_status(run_id, "aggregating")
    settlements = settlement_agg.aggregate(layers["settlements"], out, profile["crs"])

    for k, gdf in out.items():
        _slim(gdf, OUT_COLS[k]).to_file(run_dir / f"damage_{k}.geojson", driver="GeoJSON")
    settlements.to_file(run_dir / "settlements.geojson", driver="GeoJSON")

    def n(gdf, status):
        return int((gdf["status"] == status).sum()) if "status" in gdf and len(gdf) else 0

    damaged = settlements[settlements["damage_score"] > 0] if len(settlements) else settlements
    km = lambda gdf, st: round(float(gdf.loc[gdf["status"] == st, "hit_m"].sum()) / 1000, 2) if len(gdf) else 0.0

    stats = {
        "osm_snapshot": osm_fetch.SNAPSHOT,
        "buildings_total": len(out["buildings"]),
        "buildings_affected": n(out["buildings"], "affected"),
        "roads_total": len(out["roads"]),
        "roads_affected": n(out["roads"], "affected"),
        "roads_cut": n(out["roads"], "likely_cut"),
        "road_km_impacted": round(km(out["roads"], "affected") + km(out["roads"], "likely_cut"), 2),
        "bridges_total": len(out["bridges"]),
        "bridges_lost": n(out["bridges"], "likely_lost"),
        "facilities_total": len(out["facilities"]),
        "facilities_affected": n(out["facilities"], "affected"),
        "settlements_total": len(settlements),
        "settlements_with_damage": len(damaged),
        "per_settlement": damaged[["name", "buildings_total", "buildings_affected", "roads_affected",
                                   "roads_cut", "bridges_lost", "facilities_affected", "damage_score"]]
                          .head(30).to_dict(orient="records") if len(damaged) else [],
        "rules": {
            "building": "center point within 1 pixel (20 m) of flood or debris",
            "road_affected_m": damage.ROAD_AFFECTED_M,
            "road_cut_continuous_m": damage.ROAD_CUT_RUN_M,
            "bridge_lost_continuous_m": damage.BRIDGE_LOST_RUN_M,
            "settlement_assign_max_m": settlement_agg.MAX_ASSIGN_M,
            "damage_score_weights": settlement_agg.WEIGHTS,
        },
    }
    (run_dir / "damage_stats.json").write_text(json.dumps(stats, indent=2, default=int))
    registry.set_status(run_id, "damaged",
                        f"{stats['buildings_affected']} bldg, {stats['roads_cut']} roads cut, {stats['bridges_lost']} bridges")
    return stats