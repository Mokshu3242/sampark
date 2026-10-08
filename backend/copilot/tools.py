import json
import logging
from pathlib import Path

import geopandas as gpd

from app.core.jsonsafe import clean

log = logging.getLogger("flood-watch.tools")


class ToolError(RuntimeError):
    pass


class ToolBox:
    """Read-only access to one run's outputs. Every result is JSON-safe."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self._json: dict[str, dict] = {}
        self._villages: gpd.GeoDataFrame | None = None

    def _load(self, name: str) -> dict:
        if name not in self._json:
            p = self.run_dir / name
            if not p.exists():
                raise ToolError(f"{name} not found. That pipeline step hasn't run yet.")
            self._json[name] = json.loads(p.read_text(encoding="utf-8"))
        return self._json[name]

    def _village_table(self) -> gpd.GeoDataFrame:
        if self._villages is None:
            p = self.run_dir / "cutoff.geojson"
            if not p.exists():
                raise ToolError("cutoff.geojson not found.")
            self._villages = gpd.read_file(p)
        return self._villages

    # ---- Tools ----

    def get_run_info(self) -> dict:
        req = self._load("request.json")
        q = self._load("quality.json")
        return {
            "area_bbox": req["bbox"],
            "flood_date": req["flood_date"],
            "radar_before_date": q["s1"]["before_date"],
            "radar_after_date": q["s1"]["after_date"],
            "radar_orbit": q["s1"]["orbit"],
            "radar_direction": q["s1"]["direction"],
            "data_quality": q["badge"],
            "quality_notes": q.get("notes", []),
            "optical_clear_fraction": {k: v.get("clear_fraction") for k, v in q.get("s2", {}).items()},
        }

    def get_flood_summary(self) -> dict:
        s = self._load("flood_stats.json")
        keys = ["flood_area_ha", "debris_area_ha", "flood_polygon_count", "debris_polygon_count",
                "permanent_water_excluded_ha", "valid_fraction", "uncertainty_ha", "pixel_size_m"]
        out = {k: s.get(k) for k in keys}
        out["note"] = "In this steep valley, most impact shows as debris and channel change, not open water."
        return out

    def get_damaged_infrastructure(self) -> dict:
        s = self._load("damage_stats.json")
        keys = ["osm_snapshot", "buildings_total", "buildings_affected", "roads_total", "roads_affected",
                "roads_cut", "road_km_impacted", "bridges_total", "bridges_lost", "facilities_total",
                "facilities_affected", "settlements_total", "settlements_with_damage"]
        out = {k: s.get(k) for k in keys}
        out["top_damaged_villages"] = s.get("per_settlement", [])[:10]
        return out

    def get_cutoff_summary(self) -> dict:
        s = self._load("cutoff_stats.json")
        keys = ["settlements_total", "cut_off", "longer_route", "ok", "pre_isolated",
                "road_edges_removed", "road_km_removed", "bridges_lost_used"]
        out = {k: s.get(k) for k in keys}
        out["network_note"] = "Vehicle roads only. Footpaths are not modeled."
        return out

    def get_cutoff_villages(self, limit: int = 30) -> dict:
        v = self._village_table()
        v = v[v["status"].isin(["cut_off", "longer_route"])].head(int(limit))
        cols = ["name", "status", "buildings_total", "dist_before_km", "dist_after_km",
                "hospital_before_km", "hospital_after_km", "road_snap_m"]
        rows = v[[c for c in cols if c in v]].to_dict(orient="records")
        return clean({"count": len(rows), "villages": rows})

    def get_repair_plan(self) -> dict:
        plan = self._load("cutoff_stats.json").get("repair_plan", [])
        packages = []
        for p in plan:
            p = dict(p)
            names = p.get("villages_reconnected", [])
            p["villages_reconnected"] = names[:8]
            p["more_villages_not_listed"] = max(0, len(names) - 8)
            packages.append(p)
        return {"total_packages": len(plan),
                "total_buildings_reconnected": plan[-1]["cumulative_buildings"] if plan else 0,
                "packages": packages}

    def get_village_detail(self, name: str) -> dict:
        v = self._village_table()
        names = v["name"].fillna("")
        hit = v[names.str.lower() == name.lower()]
        if hit.empty:
            hit = v[names.str.lower().str.contains(name.lower(), regex=False)]
        if hit.empty:
            raise ToolError(f"No village named '{name}' in this area.")
        if len(hit) > 1:
            return {"matches": hit["name"].head(5).tolist(), "note": "Several villages match. Ask about one."}
        row = hit.drop(columns="geometry").iloc[0].to_dict()
        row["field_meanings"] = {
            "dist_before_km": "road distance to nearest help (hospital or road out of the area) BEFORE the flood",
            "dist_after_km": "same AFTER the flood; null means no road route at all",
            "hospital_before_km": "road distance to nearest hospital before the flood",
            "hospital_after_km": "same after; null means hospital unreachable by road",
            "road_snap_m": "how far the village point is from the nearest vehicle road",
            "damage_score": "weighted score: buildings + 5 per cut road + 10 per lost bridge + 3 per facility",
        }
        return clean(row)

    def get_validation_metrics(self) -> dict:
        p = self.run_dir / "eval.json"
        if not p.exists():
            return {"status": "not computed yet", "note": "Comparison with Copernicus EMS EMSR927 comes in Phase 7."}
        return json.loads(p.read_text(encoding="utf-8"))

    # ---- Dispatch ----

    def dispatch(self, tool: str, args: dict) -> dict:
        if tool not in TOOL_NAMES:
            raise ToolError(f"Unknown tool: {tool}")
        try:
            return clean(getattr(self, tool)(**(args or {})))
        except TypeError as e:
            raise ToolError(f"Bad arguments for {tool}: {e}") from e


TOOL_NAMES = [
    "get_run_info", "get_flood_summary", "get_damaged_infrastructure", "get_cutoff_summary",
    "get_cutoff_villages", "get_repair_plan", "get_village_detail", "get_validation_metrics",
]