from datetime import date
import sys

from app.core.config import settings
from app.core.run_registry import RunRegistry
from pipeline.fetch import fetch_all

BBOX = [85.15, 27.90, 85.45, 28.30]
FLOOD_DATE = date(2026, 8, 26)

registry = RunRegistry(settings.runs_dir)
if len(sys.argv) > 1:
    run_id = sys.argv[1]   # resume an existing run
else:
    run_id = registry.new_run({"name": "test-trishuli", "bbox": BBOX, "flood_date": FLOOD_DATE.isoformat()})
print("Run ID:", run_id)
print(fetch_all(run_id, BBOX, FLOOD_DATE))