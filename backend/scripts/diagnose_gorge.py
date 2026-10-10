"""Why does Flood Watch miss the gorge debris? Diagnostic only. Nothing here changes the method."""
import json
import sys
import warnings

import numpy as np
from scipy.ndimage import median_filter

from app.core.config import settings
from eval import ems_load
from eval.compare_emsr import _burn, _dem_on_grid, _radar_valid
from pipeline import change_detect as cd
from pipeline import preprocess

run_id = sys.argv[1] if len(sys.argv) > 1 else "20261008T063513-021f3e"
run_dir = settings.runs_dir / run_id

flood, ref = preprocess.read_ref(run_dir / "flood_mask.tif")
debris, _ = preprocess.read_ref(run_dir / "debris_mask.tif")
flood, debris = flood[0] == 1, debris[0] == 1
shape, epsg = flood.shape, ref["crs"]
radar_ok = _radar_valid(run_dir)

# Radar change in dB, same as the baseline (mean of VV and VH)
s1, _ = preprocess.read_ref(run_dir / "s1_pair.tif")
vv_b, vh_b, vv_a, vh_a = (cd.to_db(b) for b in s1)
with warnings.catch_warnings():
    warnings.simplefilter("ignore", RuntimeWarning)
    lr = np.nanmean(np.stack([vv_a - vv_b, vh_a - vh_b]), axis=0)
lr_med = median_filter(np.nan_to_num(lr), size=5)   # stand-in for our speckle filter

# Slope from the DEM, in degrees
dem = _dem_on_grid(run_dir, ref)
px = abs(ref["transform"].a)
gy, gx = np.gradient(np.where(np.isfinite(dem), dem, np.nanmean(dem)), px, px)
slope = np.degrees(np.arctan(np.hypot(gx, gy)))

th = json.loads((run_dir / "flood_stats.json").read_text(encoding="utf-8")).get("thresholds", {})
d_thr, f_thr = th.get("debris_thr_db"), th.get("flood_thr_db")
print(f"Run {run_id} | debris threshold {d_thr} dB (brighter) | flood threshold {f_thr} dB (darker)\n")

ems = ems_load.load()
event = ems["event"].to_crs(epsg)
aois = ems["aoi"].to_crs(epsg)

def pct(x):
    return f"{100 * x:5.1f}%"

for name in sorted(event["aoi"].unique()):
    a = aois[aois["aoi"] == name]
    m = _burn(event[event["aoi"] == name].geometry, ref, shape) & _burn(a.geometry, ref, shape) & radar_ok
    n = int(m.sum())
    if n == 0:
        continue
    v = lr[m]
    v = v[np.isfinite(v)]
    vm = lr_med[m]
    print(f"== {name}: {n} EMS debris pixels ({n * px * px / 1e4:.1f} ha)")
    print(f"  found by us (debris | flood)       {pct((debris | flood)[m].mean())}   (debris {pct(debris[m].mean())}, flood {pct(flood[m].mean())})")
    print(f"  radar changed |dB| > 3 (baseline) {pct((np.abs(v) > 3).mean())}")
    print(f"    of those: brighter {pct((v > 3).sum() / max(1, (np.abs(v) > 3).sum()))}, darker {pct((v < -3).sum() / max(1, (np.abs(v) > 3).sum()))}")
    print(f"  median change                      {np.median(v):+.2f} dB")
    if d_thr is not None:
        print(f"  passes debris thr (>= +{d_thr} dB)     raw {pct((v >= d_thr).mean())}   after 5x5 median {pct((vm >= d_thr).mean())}")
    if f_thr is not None:
        print(f"  passes flood thr (<= {f_thr} dB)      raw {pct((v <= f_thr).mean())}   after 5x5 median {pct((vm <= f_thr).mean())}")
    print(f"  VV before (dark = already water)  median {np.nanmedian(vv_b[m]):.1f} dB, below -15 dB: {pct((vv_b[m] < -15).mean())}")
    print(f"  slope                              median {np.median(slope[m]):.0f} deg, over 20 deg: {pct((slope[m] > 20).mean())}")
    print()