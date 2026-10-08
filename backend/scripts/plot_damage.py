import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio

run = f"data/runs/{sys.argv[1]}"
with rasterio.open(f"{run}/dem.tif") as ds:
    dem, crs, b = ds.read(1).astype(float), ds.crs, ds.bounds
extent = (b.left, b.right, b.bottom, b.top)   # draw rasters in meters so vectors line up

flood = rasterio.open(f"{run}/flood_mask.tif").read(1)
debris = rasterio.open(f"{run}/debris_mask.tif").read(1)

fig, ax = plt.subplots(figsize=(10, 14))
ax.imshow(dem, cmap="gray", extent=extent)
ax.imshow(np.ma.masked_where(flood != 1, flood), cmap="winter", alpha=0.85, extent=extent)
ax.imshow(np.ma.masked_where(debris != 1, debris), cmap="autumn", alpha=0.85, extent=extent)

def layer(name, status):
    g = gpd.read_file(f"{run}/damage_{name}.geojson")
    return g[g["status"] == status].to_crs(crs) if len(g) else g

for g, kw in [
    (layer("roads", "affected"), dict(color="yellow", linewidth=1)),
    (layer("roads", "likely_cut"), dict(color="magenta", linewidth=2.5)),
    (layer("buildings", "affected"), dict(color="red", markersize=6)),
    (layer("bridges", "likely_lost"), dict(color="cyan", linewidth=4)),
]:
    if len(g):
        g.plot(ax=ax, **kw)

ax.set_title("Debris (orange), flood (blue), roads affected (yellow), cut (magenta),\nbuildings hit (red), bridges lost (cyan)")
ax.axis("off")
plt.savefig("damage_check.png", dpi=80, bbox_inches="tight")
print("saved damage_check.png")