"""One-time: copy finished local runs into Mongo, so the server can restore them."""
import sys

from app.core import storage
from app.core.config import settings

if not storage.enabled():
    sys.exit("MONGO_URI is not set in .env")

runs = sys.argv[1:] or ["20261008T063513-021f3e", "20261008T230310-8ae6e4"]
for run_id in runs:
    if not (settings.runs_dir / run_id / "request.json").exists():
        print(f"skip {run_id}: not found locally")
        continue
    storage.upload_run(run_id)
    print(f"uploaded {run_id}")