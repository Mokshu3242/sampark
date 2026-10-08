import json
import logging

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline import change_detect as cd
from pipeline import optical, preprocess, reference_water, terrain_mask, uncertainty, vectorize

log = logging.getLogger("flood-watch.map")


def map_flood(run_id: str, bbox: list[float]) -> dict:
    registry = RunRegistry(settings.runs_dir)
    run_dir = settings.runs_dir / run_id
    s1_path, dem_path = run_dir / "s1_pair.tif", run_dir / "dem.tif"
    if not s1_path.exists() or not dem_path.exists():
        registry.set_status(run_id, "failed", "missing s1_pair.tif or dem.tif")
        raise RuntimeError("Run Phase 1 first: s1_pair.tif and dem.tif are required.")

    # 1. Load S1 (the reference grid) and filter speckle
    registry.set_status(run_id, "loading")
    s1, ref = preprocess.read_ref(s1_path)
    if s1.shape[0] != 4:
        raise RuntimeError(f"Expected 4 bands in s1_pair.tif, got {s1.shape[0]}")
    pixel_m = abs(ref["transform"].a)
    shape_ = (ref["height"], ref["width"])

    registry.set_status(run_id, "filtering")
    vv_b, vh_b, vv_a, vh_a = (cd.to_db(b) for b in preprocess.speckle_filter(s1))
    lr = np.nanmean(np.stack([vv_a - vv_b, vh_a - vh_b]), axis=0)

    # 2. Terrain: DEM, slope, rivers, HAND
    registry.set_status(run_id, "terrain")
    dem = preprocess.read_aligned(dem_path, ref, Resampling.bilinear)[0]
    slope = preprocess.slope_deg(dem, pixel_m)

    water_area, river_area, osm_status = reference_water.water_polygons(bbox, ref["crs"], run_dir)

    def burn(geoms) -> np.ndarray:
        if len(geoms) == 0:
            return np.zeros(shape_, bool)
        return rasterize(((g, 1) for g in geoms), out_shape=shape_, transform=ref["transform"],
                         fill=0, dtype="uint8").astype(bool)

    osm_water = burn(water_area)     # all water: rivers, streams, lakes
    river = burn(river_area)         # main rivers only, used for HAND and the debris corridor
    hand, dist_m = terrain_mask.hand_and_distance(dem, river if river.any() else osm_water, pixel_m)
    flood_ok, debris_ok = terrain_mask.masks(slope, hand, dist_m)

    # 3. Sentinel-2 (optional)
    registry.set_status(run_id, "optical")
    permanent = osm_water.copy()
    s2_water_after = s2_clear_after = None
    if (run_dir / "s2_before.tif").exists():
        s2b = preprocess.read_aligned(run_dir / "s2_before.tif", ref, Resampling.nearest, zero_is_nodata=True)
        water_b, _ = optical.water_and_clear(s2b[0], s2b[2])
        permanent |= water_b & debris_ok   # only in the valley corridor, ignores snow and high shadows
    if (run_dir / "s2_after.tif").exists():
        s2a = preprocess.read_aligned(run_dir / "s2_after.tif", ref, Resampling.nearest, zero_is_nodata=True)
        s2_water_after, s2_clear_after = optical.water_and_clear(s2a[0], s2a[2])

    # 4. Detection
    registry.set_status(run_id, "change_detection")
    valid = np.isfinite(lr) & np.isfinite(dem)
    thr = cd.thresholds(lr, valid & (flood_ok | debris_ok))

    # Flood: big drop, dark after, low above the river, not water before
    flood = valid & flood_ok & ~permanent & (lr < thr["flood_thr_db"]) & (vv_a < thr["water_vv_after_db"])
    # Debris: strong change either way in the river corridor (scour = darker, deposit = brighter)
    debris = valid & debris_ok & (np.abs(lr) > thr["debris_thr_db"])

    flood = vectorize.clean(flood, pixel_m)
    debris = vectorize.clean(debris & ~flood, pixel_m)

    # 5. Uncertainty (only on detected pixels)
    unc = uncertainty.score(flood | debris, lr, thr["flood_thr_db"], slope, hand,
                            s2_water_after, s2_clear_after)

    # 6. Write outputs
    registry.set_status(run_id, "writing")
    vectorize.write_mask(flood, valid, ref, run_dir / "flood_mask.tif")
    vectorize.write_mask(debris, valid, ref, run_dir / "debris_mask.tif")
    p = ref.copy()
    p.update(count=1, dtype="uint8", nodata=255, compress="deflate")
    with rasterio.open(run_dir / "uncertainty.tif", "w", **p) as ds:
        ds.write(unc, 1)

    n_flood = vectorize.to_geojson(flood, ref, run_dir / "flood_polygons.geojson")
    n_debris = vectorize.to_geojson(debris, ref, run_dir / "debris_polygons.geojson")

    px_ha = pixel_m ** 2 / 10_000

    def ha(m: np.ndarray) -> float:
        return round(float(m.sum()) * px_ha, 2)

    stats = {
        "flood_area_ha": ha(flood),
        "debris_area_ha": ha(debris),
        "flood_polygon_count": n_flood,
        "debris_polygon_count": n_debris,
        "permanent_water_excluded_ha": ha(permanent & valid),
        "river_pixels_ha": ha(river),
        "valid_fraction": round(float(valid.mean()), 3),
        "flood_plausible_fraction": round(float(flood_ok.mean()), 3),
        "debris_plausible_fraction": round(float(debris_ok.mean()), 3),
        "s2_after_clear_fraction": round(float(s2_clear_after.mean()), 3) if s2_clear_after is not None else None,
        "thresholds": thr,
        "uncertainty_ha": {
            "low": ha(unc == 0), "medium": ha(unc == 1), "high": ha(unc == 2),
        },
        "pixel_size_m": pixel_m,
        "crs": str(ref["crs"]),
        "osm_water": osm_status,
    }
    (run_dir / "flood_stats.json").write_text(json.dumps(stats, indent=2))
    registry.set_status(run_id, "mapped", f"flood {stats['flood_area_ha']} ha, debris {stats['debris_area_ha']} ha")
    log.info(f"Flood {stats['flood_area_ha']} ha, debris {stats['debris_area_ha']} ha")
    return stats