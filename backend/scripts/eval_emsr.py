import json
import sys

from eval import ems_load
from eval.compare_emsr import compare

if len(sys.argv) < 2:
    print("Usage: python -m scripts.eval_emsr describe | <run_id>")
    sys.exit(1)

if sys.argv[1] == "describe":
    ems_load.describe()
    sys.exit(0)

res = compare(sys.argv[1])


def row(name, s):
    if not s:
        return f"{name:24s} (no score)"
    return (f"{name:24s} P {s.get('precision')}  R {s.get('recall')}  F1 {s.get('f1')}  "
            f"IoU {s.get('iou')}  |  tolerant P {s.get('precision_tolerant')}  "
            f"R {s.get('recall_tolerant')}  F1 {s.get('f1_tolerant')}")


print("EMS layers:", len(res.get("ems_layers", [])), "files")
print("AOIs used:", res.get("aois_used"))

print("\n--- Pixel scores ---")
if "pixel_scores_note" in res:
    print(res["pixel_scores_note"])
else:
    print("Scored in:", res.get("pixel_scores_aois"), "|", res.get("evaluation_area_ha"), "ha")
    print("EMS event types:", res.get("ems_event_types"))
    print(row("Ours (flood + debris)", res.get("ours_flood_plus_debris")))
    print(row("Ours (flood only)", res.get("ours_flood_only")))
    print(row("Baseline threshold", res.get("baseline_threshold")))

print("\n--- Buildings (EMS damaged points) ---")
b = res.get("buildings")
if not b:
    print("No EMS building layer")
elif "note" in b:
    print(b["note"])
else:
    print("EMS points in run:", b["ems_points_in_run"])
    print("Recall all:", b["recall_all"], "| with tolerance:", b["recall_all_with_tolerance"])
    print("Recall destroyed+damaged (tolerance):", b["recall_destroyed_or_damaged_with_tolerance"])
    print("Baseline recall (tolerance):", res.get("buildings_baseline_recall_with_tolerance"))
    print("Flagged share of run:", res.get("flagged_share_of_run"))
    print("Recall per % of land flagged:", res.get("recall_per_flagged_pct"))
    for k, v in b.get("by_class", {}).items():
        print(f"  {k:18s} n {v['n']:4d}  found {v['found']:4d}  "
              f"tolerant {v['found_with_tolerance']:4d}  recall {v['recall_with_tolerance']}")

print("\n--- Roads ---")
print(res.get("roads"))

print("\n--- Transport areas (EMS polygons) ---")
ta = res.get("transport_areas")
if ta:
    print(f"Found {ta['found']} of {ta['ems_damaged_features']} ({ta['share_found']})")
    for it in ta["items"]:
        print("  ", it["aoi"], it["type"], it["name"] or "(no name)",
              "found" if it["found"] else "MISSED")
else:
    print(None)

if "ems_damaged_facilities" in res:
    print("\nEMS damaged facilities:", res["ems_damaged_facilities"])

print("\nSaved: data/runs/<run_id>/eval.json and eval/*.png, copies in docs/figures/")