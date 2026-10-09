import json
import logging
import shutil

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import binary_dilation
from shapely.geometry import box

from app.core.config import BACKEND, settings
from app.core.jsonsafe import clean
from eval import ems_load
from eval.metrics import scores
from pipeline import change_detect as cd
from pipeline import preprocess

import warnings
from rasterio.warp import reproject, Resampling

log = logging.getLogger("flood-watch.eval")

TOL_PX = 2
ROAD_MATCH_M = 40.0
NOT_DAMAGED = "no visible|not affected|unaffected|no damage|not analys"
OUR_ROAD_HIT = ["affected", "likely_cut", "bridge_lost"]
CREDIT = "European Union, Copernicus Emergency Management Service data (EMSR927). Used for validation only."


def _burn(geoms, ref, shape) -> np.ndarray:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros(shape, bool)
    return rasterize(((g, 1) for g in geoms), out_shape=shape, transform=ref["transform"],
                     fill=0, dtype="uint8", all_touched=True).astype(bool)


def _damaged(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if "damage_gra" not in g.columns:
        return g
    return g[~g["damage_gra"].astype(str).str.lower().str.contains(NOT_DAMAGED, na=False)]


def _baseline(run_dir) -> np.ndarray:
    """Naive method for comparison: fixed 3 dB change, no speckle filter, no terrain or water masks."""
    s1, _ = preprocess.read_ref(run_dir / "s1_pair.tif")
    vv_b, vh_b, vv_a, vh_a = (cd.to_db(b) for b in s1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        lr = np.nanmean(np.stack([vv_a - vv_b, vh_a - vh_b]), axis=0)
    return np.isfinite(lr) & (np.abs(lr) > 3.0)


def _radar_valid(run_dir) -> np.ndarray:
    s1, _ = preprocess.read_ref(run_dir / "s1_pair.tif")
    return np.all(np.isfinite(s1), axis=0) & np.all(s1 != 0, axis=0)


def _our_roads(run_dir, epsg):
    p = run_dir / "road_edges.geojson"
    if not p.exists():
        return None
    r = gpd.read_file(p).to_crs(epsg)
    return r[r["status"].isin(OUR_ROAD_HIT)]


def _road_check(ours, ems_roads, epsg) -> dict | None:
    if ems_roads is None or ems_roads.empty or ours is None:
        return None
    dmg = _damaged(ems_roads.to_crs(epsg))
    if dmg.empty:
        return {"note": "EMS has no damaged road lines here"}
    total = dmg.length.sum()
    if ours.empty:
        return {"ems_damaged_road_km": round(total / 1000, 2), "matched_km": 0.0, "share_found": 0.0}
    zone = ours.buffer(ROAD_MATCH_M).union_all()
    matched = dmg.intersection(zone).length.sum()
    return {
        "ems_damaged_road_km": round(total / 1000, 2),
        "matched_km": round(matched / 1000, 2),
        "share_found": round(matched / total, 3),
        "match_distance_m": ROAD_MATCH_M,
    }


def _area_check(ours, ems_areas, epsg) -> dict | None:
    """EMS transportationA polygons (coded oddly, no names). Reported, not called bridges."""
    if ems_areas is None or ems_areas.empty or ours is None:
        return None
    dmg = _damaged(ems_areas.to_crs(epsg))
    if dmg.empty:
        return None
    zone = ours.buffer(ROAD_MATCH_M).union_all() if not ours.empty else None
    hits = [bool(zone is not None and g.intersects(zone)) for g in dmg.geometry]
    names = dmg["name"].fillna("").astype(str).tolist() if "name" in dmg.columns else [""] * len(dmg)
    types = dmg["obj_type"].astype(str).tolist() if "obj_type" in dmg.columns else [""] * len(dmg)
    return {
        "ems_damaged_features": len(dmg),
        "found": int(sum(hits)),
        "share_found": round(sum(hits) / len(dmg), 3),
        "items": [{"name": n or None, "type": t, "aoi": a, "found": h}
                  for n, t, a, h in zip(names, types, dmg["aoi"], hits)],
        "match_distance_m": ROAD_MATCH_M,
    }


def _building_check(ems_b, impact, ref, epsg) -> dict | None:
    """Share of EMS damaged buildings inside our flood+debris mask (with tolerance)."""
    if ems_b is None or ems_b.empty:
        return None
    b = ems_b.to_crs(epsg)
    pts = b.geometry.representative_point()
    zone = binary_dilation(impact, iterations=TOL_PX)
    inv = ~ref["transform"]
    h, w = impact.shape
    hit_strict, hit_tol, inside = [], [], []
    for p in pts:
        c, r = inv * (p.x, p.y)
        r, c = int(np.floor(r)), int(np.floor(c))
        ok = 0 <= r < h and 0 <= c < w
        inside.append(ok)
        hit_strict.append(ok and bool(impact[r, c]))
        hit_tol.append(ok and bool(zone[r, c]))
    b = b.assign(inside=inside, hit=hit_strict, hit_tol=hit_tol)
    b = b[b["inside"]]
    if b.empty:
        return {"note": "No EMS building points inside this run"}
    col = "damage_gra" if "damage_gra" in b.columns else None
    by = {}
    if col:
        for val, g in b.groupby(col):
            by[str(val)] = {"n": len(g),
                            "found": int(g["hit"].sum()),
                            "found_with_tolerance": int(g["hit_tol"].sum()),
                            "recall_with_tolerance": round(float(g["hit_tol"].mean()), 3)}
    core = _damaged(b)
    if col:
        core = core[~core[col].astype(str).str.lower().str.contains("possibly", na=False)]
    return {
        "ems_points_in_run": len(b),
        "recall_all": round(float(b["hit"].mean()), 3),
        "recall_all_with_tolerance": round(float(b["hit_tol"].mean()), 3),
        "recall_destroyed_or_damaged_with_tolerance":
            round(float(core["hit_tol"].mean()), 3) if len(core) else None,
        "by_class": by,
        "tolerance_m": TOL_PX * abs(ref["transform"].a),
    }

def _dem_on_grid(run_dir, ref) -> np.ndarray:
    """Read the DEM resampled onto the mask grid (sizes can differ by a pixel)."""
    out = np.full((ref["height"], ref["width"]), np.nan, dtype="float32")
    with rasterio.open(run_dir / "dem.tif") as ds:
        reproject(
            source=rasterio.band(ds, 1), destination=out,
            src_transform=ds.transform, src_crs=ds.crs,
            dst_transform=ref["transform"], dst_crs=ref["crs"],
            dst_nodata=np.nan, resampling=Resampling.bilinear,
        )
    return out.astype(float)

def _figure(run_dir, ours, ems, valid, ref, path, title, pts=None):
    dem = _dem_on_grid(run_dir, ref)
    dem = np.where(np.isfinite(dem), dem, np.nanmin(dem))
    gy, gx = np.gradient(dem)
    shade = np.clip(0.5 + 0.5 * (gx - gy) / (np.nanstd(gx - gy) * 3 + 1e-6), 0, 1)

    rgb = np.dstack([shade] * 3) * 0.8
    rgb[~valid] *= 0.5
    rgb[ours & ems & valid] = [0.1, 0.75, 0.2]    # agree
    rgb[ours & ~ems & valid] = [0.9, 0.15, 0.15]  # only ours
    rgb[~ours & ems & valid] = [0.15, 0.4, 0.95]  # only EMS

    b = rasterio.transform.array_bounds(ref["height"], ref["width"], ref["transform"])
    fig, ax = plt.subplots(figsize=(8, 10))
    ax.imshow(rgb, extent=(b[1], b[3], b[0], b[2]))
    if pts is not None and not pts.empty:
        ax.scatter(pts.x, pts.y, s=4, c="yellow", edgecolors="black", linewidths=0.2,
                   label="EMS damaged building")
        ax.legend(loc="lower right", fontsize=7)
    ax.set_title(f"{title}\ngreen = both · red = ours only · blue = EMS only · dim = not scored",
                 fontsize=9)
    ax.axis("off")
    fig.text(0.01, 0.01, CREDIT, fontsize=6)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def compare(run_id: str) -> dict:
    run_dir = settings.runs_dir / run_id
    out_dir = run_dir / "eval"
    out_dir.mkdir(exist_ok=True)

    ems = ems_load.load()
    if "event" not in ems and "buildings" not in ems:
        raise RuntimeError("No EMS event or building layer found. Run `python -m scripts.eval_emsr describe`.")

    flood, ref = preprocess.read_ref(run_dir / "flood_mask.tif")
    debris, _ = preprocess.read_ref(run_dir / "debris_mask.tif")
    flood, debris = flood[0] == 1, debris[0] == 1
    shape = flood.shape
    epsg = ref["crs"]
    px_ha = abs(ref["transform"].a * ref["transform"].e) / 10_000
    radar_ok = _radar_valid(run_dir)

    with rasterio.open(run_dir / "flood_mask.tif") as ds:
        run_box = box(*ds.bounds)

    # Only judge what this run could see: clip every EMS layer to the run box
    clipped = {}
    for k, g in ems.items():
        c = gpd.clip(g.to_crs(epsg), run_box)
        c = c[c.geometry.notna() & ~c.geometry.is_empty]
        if not c.empty:
            clipped[k] = c.to_crs(4326)
    ems = clipped
    if "event" not in ems and "buildings" not in ems:
        raise RuntimeError("No EMS features inside this run's box. Pick a run that covers an EMS AOI.")

    impact = flood | debris
    base = _baseline(run_dir)
    our_roads = _our_roads(run_dir, epsg)

    res = {
        "status": "computed",
        "reference": CREDIT,
        "ems_layers": sorted({s for g in ems.values() for s in g["src_file"].unique()}),
        "aois_used": sorted({a for g in ems.values() for a in g["aoi"].unique()}),
    }

    # Pixel scores: only inside AOIs where EMS drew an event polygon
    ems_mask = np.zeros(shape, bool)
    valid = np.zeros(shape, bool)
    if "event" in ems:
        event = ems["event"].to_crs(epsg)
        ev_aois = sorted(event["aoi"].unique())
        aoi_geom = None
        if "aoi" in ems:
            a = ems["aoi"].to_crs(epsg)
            a = a[a["aoi"].isin(ev_aois)]
            if not a.empty:
                aoi_geom = a.union_all().intersection(run_box)
        if aoi_geom is None or aoi_geom.is_empty:
            aoi_geom = event.union_all().convex_hull.buffer(500).intersection(run_box)
        valid = _burn([aoi_geom], ref, shape) & radar_ok
        ems_mask = _burn(event.geometry, ref, shape)
        tol_m = TOL_PX * abs(ref["transform"].a)

        res["pixel_scores_aois"] = ev_aois
        res["evaluation_area_ha"] = round(float(valid.sum()) * px_ha, 1)
        res["ems_event_types"] = (event["event_type"].astype(str).value_counts().to_dict()
                                  if "event_type" in event.columns else None)
        for k, m in (("ours_flood_plus_debris", impact),
                     ("ours_flood_only", flood),
                     ("baseline_threshold", base)):
            res[k] = scores(m, ems_mask, valid, px_ha, TOL_PX)
            res[k]["tolerance_m"] = tol_m
    else:
        res["pixel_scores_note"] = "EMS has no event polygons in these AOIs"

    # Building points: the main check
    b_ours = _building_check(ems.get("buildings"), impact, ref, epsg)
    b_base = _building_check(ems.get("buildings"), base, ref, epsg)
    res["buildings"] = b_ours
    if b_base and "recall_all_with_tolerance" in b_base:
        res["buildings_baseline_recall_with_tolerance"] = b_base["recall_all_with_tolerance"]

    # Recall alone is unfair: a method that flags everything finds everything.
    land = radar_ok.sum()
    if land:
        res["flagged_share_of_run"] = {
            "ours": round(float((impact & radar_ok).sum() / land), 4),
            "baseline": round(float((base & radar_ok).sum() / land), 4),
        }
        if b_ours and b_base and "recall_all_with_tolerance" in b_ours:
            per = {}
            for k, rec, m in (("ours", b_ours["recall_all_with_tolerance"], impact),
                              ("baseline", b_base["recall_all_with_tolerance"], base)):
                share = float((m & radar_ok).sum() / land)
                per[k] = round(rec / (share * 100), 3) if share else None
            res["recall_per_flagged_pct"] = per

    res["roads"] = _road_check(our_roads, ems.get("roads"), epsg)
    res["transport_areas"] = _area_check(our_roads, ems.get("transport_areas"), epsg)
    if "facilities" in ems:
        res["ems_damaged_facilities"] = len(_damaged(ems["facilities"]))

    # Figures
    show = valid if valid.any() else radar_ok
    pts = None
    if "buildings" in ems:
        bp = _damaged(ems["buildings"].to_crs(epsg)).geometry.representative_point()
        pts = bp[bp.within(run_box)]
    _figure(run_dir, impact, ems_mask, show, ref, out_dir / "error_map.png",
            f"Flood Watch vs EMSR927 · run {run_id}", pts)
    _figure(run_dir, base, ems_mask, show, ref, out_dir / "error_map_baseline.png",
            "Baseline (plain threshold) vs EMSR927", pts)

    res = clean(res)
    (run_dir / "eval.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

    figs = BACKEND.parent / "docs" / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    for f in ("error_map.png", "error_map_baseline.png"):
        shutil.copy(out_dir / f, figs / f"{run_id}_{f}")
    return res