"""Build every map image the frontend asks for, so the server never renders after a restore."""
import sys

from app.api.routes_images import LAYERS, PRERENDER, cached_image
from app.core.config import settings

runs = sys.argv[1:] or [d.name for d in settings.runs_dir.iterdir() if d.is_dir()]
for run_id in runs:
    for name, widths in PRERENDER.items():
        if not (settings.runs_dir / run_id / LAYERS[name]["file"]).exists():
            continue
        for w in widths:
            cached_image(run_id, name, w)
            print(f"{run_id}  {name} @ {w}px")