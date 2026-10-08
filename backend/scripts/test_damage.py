import json
import sys

from app.core.config import settings
from pipeline.map_damage import map_damage

if len(sys.argv) < 2:
    print("Usage: python -m scripts.test_damage <run_id>")
    sys.exit(1)

run_id = sys.argv[1]
req = json.loads((settings.runs_dir / run_id / "request.json").read_text())
stats = map_damage(run_id, req["bbox"])
print(json.dumps({k: v for k, v in stats.items() if k != "per_settlement"}, indent=2))
print("\nTop villages:")
for s in stats["per_settlement"][:10]:
    print(f"  {s['name']}: {s['buildings_affected']} bldg, {s['roads_cut']} roads cut, "
          f"{s['bridges_lost']} bridges, score {s['damage_score']}")