import sys
import matplotlib.pyplot as plt
import numpy as np
import rasterio

run = f"data/runs/{sys.argv[1]}"
with rasterio.open(f"{run}/s1_pair.tif") as ds:
    s1 = ds.read().astype(float)
with np.errstate(divide="ignore", invalid="ignore"):
    db = 10 * np.log10(np.where(s1 > 0, s1, np.nan))
lr = np.nanmean([db[2] - db[0], db[3] - db[1]], axis=0)

fig, ax = plt.subplots(figsize=(8, 12))
im = ax.imshow(lr, cmap="RdBu", vmin=-6, vmax=6)
plt.colorbar(im, ax=ax, shrink=0.6, label="after - before (dB)")
ax.set_title("Radar change: red = darker, blue = brighter")
ax.axis("off")
plt.savefig("change_check.png", dpi=80, bbox_inches="tight")
print("saved change_check.png")