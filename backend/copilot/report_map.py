from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # no window, safe inside the API server
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio


def render(run_dir: Path) -> Path:
    out = run_dir / "report_map.png"
    with rasterio.open(run_dir / "dem.tif") as ds:
        dem, crs, b = ds.read(1).astype(float), ds.crs, ds.bounds
    ext = (b.left, b.right, b.bottom, b.top)
    debris = rasterio.open(run_dir / "debris_mask.tif").read(1)
    flood = rasterio.open(run_dir / "flood_mask.tif").read(1)

    fig, ax = plt.subplots(figsize=(5, 6.5))
    ax.imshow(dem, cmap="gray", extent=ext)
    ax.imshow(np.ma.masked_where(debris != 1, debris), cmap="autumn", extent=ext)
    ax.imshow(np.ma.masked_where(flood != 1, flood), cmap="winter", extent=ext)

    edges = gpd.read_file(run_dir / "road_edges.geojson").to_crs(crs)
    cut = edges[edges["status"].isin(["likely_cut", "bridge_lost"])]
    if len(cut):
        cut.plot(ax=ax, color="red", linewidth=2)
    v = gpd.read_file(run_dir / "cutoff.geojson").to_crs(crs)
    for st, color, size in [("cut_off", "magenta", 30), ("longer_route", "orange", 20)]:
        g = v[v["status"] == st]
        if len(g):
            g.plot(ax=ax, color=color, markersize=size, edgecolor="black", linewidth=0.3)

    plan_p = run_dir / "repair_plan.geojson"
    if plan_p.exists():
        plan = gpd.read_file(plan_p).to_crs(crs)
        for _, r in plan.iterrows():
            c = r.geometry.centroid
            ax.annotate(str(r["rank"]), (c.x, c.y), fontsize=8, fontweight="bold", ha="center", va="center",
                        bbox=dict(boxstyle="circle,pad=0.2", fc="lime", ec="black", lw=0.5))

    ax.set_xlim(b.left, b.right)
    ax.set_ylim(b.bottom, b.top)
    ax.axis("off")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out