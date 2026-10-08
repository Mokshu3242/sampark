import sys
import matplotlib.pyplot as plt
import numpy as np
import rasterio

run = f"data/runs/{sys.argv[1]}"
with rasterio.open(f"{run}/dem.tif") as ds:
    dem = ds.read(1).astype(float)
flood = rasterio.open(f"{run}/flood_mask.tif").read(1)
debris = rasterio.open(f"{run}/debris_mask.tif").read(1)

fig, ax = plt.subplots(figsize=(8, 12))
ax.imshow(dem, cmap="gray")
ax.imshow(np.ma.masked_where(flood != 1, flood), cmap="winter", alpha=0.9)
ax.imshow(np.ma.masked_where(debris != 1, debris), cmap="autumn", alpha=0.9)
ax.set_title("Flood (blue) and debris (orange) over DEM")
ax.axis("off")
plt.savefig("flood_check.png", dpi=80, bbox_inches="tight")
print("saved flood_check.png")