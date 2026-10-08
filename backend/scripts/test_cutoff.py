import json
import sys

from app.core.config import settings
from pipeline.map_cutoff import map_cutoff

if len(sys.argv) < 2:
    print("Usage: python -m scripts.test_cutoff <run_id>")
    sys.exit(1)

run_id = sys.argv[1]
req = json.loads((settings.runs_dir / run_id / "request.json").read_text())
stats = map_cutoff(run_id, req["bbox"])
print(json.dumps({k: v for k, v in stats.items() if k != "ranked"}, indent=2, ensure_ascii=False))

fmt = lambda v: f"{v:7.2f}" if v is not None else "   none"
print("\nCut off / longer route:")
for r in stats["ranked"][:15]:
    print(f"  {r['name'][:25]:25s} {r['status']:13s} {r.get('buildings_total', 0):>5} bldg  "
          f"{fmt(r['dist_before_km'])} -> {fmt(r['dist_after_km'])} km  (snap {r.get('road_snap_m', 0):.0f} m)")

print("\nRepair priority (reopen in this order):")
for p in stats.get("repair_plan", []):
    names = ", ".join(p["villages_reconnected"][:4]) + (" ..." if p["villages_count"] > 4 else "")
    print(f"  {p['rank']:>2}. near {str(p['near'])[:18]:18s} {p['stretches']} stretch(es), {p['length_m']:>5.0f} m"
          f"{' (bridge)' if p['has_bridge'] else '':9s} -> {p['villages_count']:>2} villages, "
          f"{p['buildings_reconnected']:>5} bldg  [{names}]")