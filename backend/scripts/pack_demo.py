"""Pack the finished runs for deployment (skips files the live app never reads)."""
import zipfile

from app.core.config import BACKEND, settings

RUNS = ["20261008T063513-021f3e", "20261008T230310-8ae6e4"]
SKIP = {"s1_pair.tif", "osm_raw.json"}   # only used while computing a run / by the EMS check
out = BACKEND / "demo_runs.zip"

total = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for run in RUNS:
        run_dir = settings.runs_dir / run
        for f in sorted(run_dir.rglob("*")):
            if f.is_file() and f.name not in SKIP:
                z.write(f, arcname=f"{run}/{f.relative_to(run_dir)}")
                total += f.stat().st_size
                print(f"  {run[-6:]}  {f.stat().st_size / 1e6:7.1f} MB  {f.name}")

print(f"\nRaw size: {total / 1e6:.0f} MB  ->  zip: {out.stat().st_size / 1e6:.0f} MB  ({out})")