"""Load EMSR927 layers. Checking only. Never used by the pipeline."""
import re
from pathlib import Path
import geopandas as gpd
import pandas as pd
from app.core.config import BACKEND

EMS_DIR = BACKEND / "data" / "emsr927"
USE_AOIS = ("AOI01", "AOI02", "AOI03")   # these overlap our main run
USE_PRODUCT = "PRODUCT"                  # set to "MONIT01" to test the updates

# layer name in the file (with its A/P/L letter) -> our kind
LAYERS = {
    "observedeventa": "event",
    "areaofinteresta": "aoi",
    "transportationl": "roads",
    "transportationa": "transport_areas",
    "builtupp": "buildings",
    "builtupa": "buildings",
    "facilitiesa": "facilities",
    "facilitiesp": "facilities",
}
CLASS_COLS = ["damage_gra", "event_type", "obj_type", "notation"]

# EMSR927_AOI01_GRA_PRODUCT_builtUpP_v1.shp -> AOI01, PRODUCT, builtupp
NAME_RE = re.compile(r"EMSR\d+_(AOI\d+)_\w+?_(PRODUCT|MONIT\d+)_([A-Za-z]+)_v\d+", re.I)


def _parse(p: Path):
    m = NAME_RE.search(p.stem)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), m.group(3).lower()


def _files():
    """Yield (path, aoi, kind) for the .shp layers we want."""
    for p in sorted(EMS_DIR.rglob("*.shp")):
        info = _parse(p)
        if not info:
            continue
        aoi, prod, layer = info
        if aoi not in USE_AOIS or prod != USE_PRODUCT:
            continue
        kind = LAYERS.get(layer)
        if kind:
            yield p, aoi, kind


def _read(p: Path):
    try:
        g = gpd.read_file(p)
    except Exception as e:
        print(f"  skip {p.name}: {e}")
        return None
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    if g.empty:
        return None
    if g.crs is None:
        g = g.set_crs(4326)
    return g.to_crs(4326)


def load():
    """Returns {kind: GeoDataFrame in EPSG:4326}. Each row has 'aoi' and 'src_file'."""
    parts = {}
    for p, aoi, kind in _files():
        g = _read(p)
        if g is None:
            continue
        g["aoi"] = aoi
        g["src_file"] = p.name
        parts.setdefault(kind, []).append(g)
    return {k: gpd.GeoDataFrame(pd.concat(v, ignore_index=True), crs=4326)
            for k, v in parts.items()}


def class_column(g):
    if g is None:
        return None
    cols = {c.lower(): c for c in g.columns}
    for c in CLASS_COLS:
        if c in cols:
            return cols[c]
    return None


def describe():
    print("EMS dir:", EMS_DIR, "| AOIs:", USE_AOIS, "| product:", USE_PRODUCT)
    found = False
    for p, aoi, kind in _files():
        g = _read(p)
        if g is None:
            print(f"\n[{kind}] {aoi} {p.name}: empty")
            continue
        found = True
        geoms = sorted(str(t) for t in g.geom_type.dropna().unique())
        print(f"\n[{kind}] {aoi} {p.name}")
        print("  rows:", len(g), "| geom:", geoms)
        print("  cols:", [c for c in g.columns if c != "geometry"])
        for c in ("damage_gra", "event_type", "obj_type"):
            if c in g.columns:
                print(f"  {c}:", g[c].value_counts(dropna=False).head(8).to_dict())
    if not found:
        print("No layers matched. Check USE_AOIS and the file names.")