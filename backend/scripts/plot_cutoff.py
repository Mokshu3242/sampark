import os
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

edges = gpd.read_file(f"{run}/road_edges.geojson").to_crs(crs)
villages = gpd.read_file(f"{run}/cutoff.geojson").to_crs(crs)

fig, ax = plt.subplots(figsize=(10, 14))
ax.imshow(dem, cmap="gray", extent=extent)
ax.imshow(np.ma.masked_where(flood != 1, flood), cmap="winter", alpha=0.7, extent=extent)
ax.imshow(np.ma.masked_where(debris != 1, debris), cmap="autumn", alpha=0.7, extent=extent)

# Roads, drawn from least to most important so cuts sit on top
for status, color, width in [("ok", "white", 0.5),
                             ("affected", "yellow", 1.0),
                             ("likely_cut", "red", 2.5),
                             ("bridge_lost", "cyan", 4.0)]:
    g = edges[edges["status"] == status]
    if len(g):
        g.plot(ax=ax, color=color, linewidth=width)

# Villages
for status, color, size in [("pre_isolated", "gray", 8),
                            ("ok", "white", 10),
                            ("longer_route", "orange", 45),
                            ("cut_off", "magenta", 70)]:
    g = villages[villages["status"] == status]
    if len(g):
        g.plot(ax=ax, color=color, markersize=size, marker="o", edgecolor="black", linewidth=0.4)

if os.path.exists(f"{run}/repair_plan.geojson"):
    plan = gpd.read_file(f"{run}/repair_plan.geojson").to_crs(crs)
    plan.plot(ax=ax, color="lime", linewidth=6, alpha=0.8)
    for _, r in plan.iterrows():
        c = r.geometry.centroid
        ax.annotate(str(r["rank"]), (c.x, c.y), color="black", fontsize=9, fontweight="bold",
                    ha="center", va="center",
                    bbox=dict(boxstyle="circle,pad=0.2", fc="lime", ec="black", lw=0.5))

ax.set_xlim(b.left, b.right)
ax.set_ylim(b.bottom, b.top)
ax.set_title("Roads: white ok, yellow affected, red cut, cyan bridge lost, green = repair order\n"
             "Villages: magenta cut off, orange longer route, white ok, gray no road before")
ax.axis("off")
plt.savefig("cutoff_check.png", dpi=80, bbox_inches="tight")
print("saved cutoff_check.png")