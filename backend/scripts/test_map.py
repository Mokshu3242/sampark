import json
import sys

from app.core.config import settings
from pipeline.map_flood import map_flood

if len(sys.argv) < 2:
    print("Usage: python -m scripts.test_map <run_id>")
    sys.exit(1)

run_id = sys.argv[1]
req = json.loads((settings.runs_dir / run_id / "request.json").read_text())
print(json.dumps(map_flood(run_id, req["bbox"]), indent=2))