import json
import sys
import time

import numpy as np
import rasterio

from app.core.config import settings
from pipeline.hydrology import build_hydro, trace, villages_along

if len(sys.argv) < 4:
    print("Usage: python -m scripts.test_hydro <run_id> <lon> <lat> [--force]")
    sys.exit(1)

run_id, lon, lat = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
run_dir = settings.runs_dir / run_id

t = time.time()
fdir_p, facc_p = build_hydro(run_dir, force="--force" in sys.argv)
print(f"Hydrology ready in {time.time() - t:.1f} s")
with rasterio.open(fdir_p) as ds:
    vals, counts = np.unique(ds.read(1), return_counts=True)
print("Direction codes:", dict(zip(vals.tolist(), counts.tolist())))

f = trace(run_dir, lon, lat)
print(json.dumps(f["properties"], indent=2))
print("Points in line:", len(f["geometry"]["coordinates"]))

vs = villages_along(f, run_dir)
print(f"\nVillages within 500 m: {len(vs)}")
for v in vs[:20]:
    print(f"  {v['km_along_path']:>7.2f} km  {str(v['name']):<22s} {v['status']:<13s} {v['distance_to_path_m']} m")