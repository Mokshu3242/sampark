import json
import logging
import time
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point, Polygon

log = logging.getLogger("flood-watch.osm")

SNAPSHOT = "2026-07-27T00:00:00Z"   # pre-event. Post-event OSM is NOT allowed as input.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "flood-watch/0.1 (Multimodal AI Hackathon, educational prototype)"}
TIMEOUT = 180

QUERY = """
[out:json][timeout:{timeout}][maxsize:536870912][date:"{date}"];
(
  way["building"]({b});
  way["highway"]["highway"!~"^(proposed|construction|platform|corridor|elevator)$"]({b});
  way["man_made"="bridge"]({b});
  node["place"~"^(city|town|village|hamlet|suburb)$"]({b});
  way["place"~"^(city|town|village|hamlet|suburb)$"]({b});
  node["amenity"~"^(hospital|clinic|doctors|school)$"]({b});
  way["amenity"~"^(hospital|clinic|doctors|school)$"]({b});
  node["healthcare"]({b});
  way["healthcare"]({b});
  way["waterway"~"^(river|stream|canal)$"]({b});
  way["natural"="water"]({b});
  way["landuse"="reservoir"]({b});
);
out body geom;
"""

AREA_KEYS = ("building", "landuse", "natural", "amenity", "place", "healthcare", "man_made", "leisure")
PLACES = {"city", "town", "village", "hamlet", "suburb"}
FACILITY_AMENITIES = {"hospital", "clinic", "doctors", "school"}
KEEP = ("osm_id", "name", "name:en", "name:ne", "highway", "building", "amenity", "healthcare",
        "place", "waterway", "bridge", "man_made", "population")


def _query(bbox: list[float]) -> dict:
    w, s, e, n = bbox
    body = QUERY.format(timeout=TIMEOUT, date=SNAPSHOT, b=f"{s},{w},{n},{e}")
    last = None
    for url in MIRRORS:
        try:
            log.info(f"Overpass: {url}")
            r = requests.post(url, data={"data": body}, headers=HEADERS, timeout=TIMEOUT + 30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning(f"{url} failed: {e}")
            last = e
            time.sleep(2)
    raise RuntimeError(f"All Overpass mirrors failed: {last}")


def fetch(run_dir: Path, bbox: list[float], force: bool = False) -> dict:
    """One Overpass call per run. Cached as raw JSON for reproducibility."""
    cache = run_dir / "osm_raw.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    data = _query(bbox)
    data["_flood_watch"] = {"snapshot": SNAPSHOT, "bbox": bbox}
    cache.write_text(json.dumps(data), encoding="utf-8")
    log.info(f"Cached {len(data.get('elements', []))} OSM elements")
    return data


def _geom(el: dict):
    tags = el.get("tags", {})
    if el["type"] == "node":
        return Point(el["lon"], el["lat"])
    pts = [(c["lon"], c["lat"]) for c in el.get("geometry") or []]
    if len(pts) < 2:
        return None
    closed = len(pts) >= 4 and pts[0] == pts[-1]
    is_area = closed and (tags.get("area") == "yes" or
                          (any(k in tags for k in AREA_KEYS) and "highway" not in tags and "waterway" not in tags))
    return Polygon(pts) if is_area else LineString(pts)


def load_layers(run_dir: Path, bbox: list[float]) -> dict[str, gpd.GeoDataFrame]:
    """Build layers from the cached response. Layers can overlap (a school can also be a building)."""
    data = fetch(run_dir, bbox)
    buckets = {k: [] for k in ("buildings", "roads", "bridges", "settlements", "facilities", "water")}

    for el in data.get("elements", []):
        g = _geom(el)
        if g is None or not g.is_valid:
            continue
        t = el.get("tags", {})
        row = {k: t.get(k) for k in KEEP if k != "osm_id"}
        row["osm_id"] = f"{el['type']}/{el['id']}"
        row["geometry"] = g
        is_line = g.geom_type == "LineString"

        if "building" in t and g.geom_type == "Polygon":
            buckets["buildings"].append(row)
        if "highway" in t and is_line:
            row_r = {**row, "is_bridge": t.get("bridge") not in (None, "no")}
            buckets["roads"].append(row_r)
            if row_r["is_bridge"]:
                buckets["bridges"].append(row_r)
        if t.get("man_made") == "bridge":
            buckets["bridges"].append({**row, "is_bridge": True})
        if t.get("place") in PLACES:
            buckets["settlements"].append({**row, "geometry": g.representative_point()})
        if t.get("amenity") in FACILITY_AMENITIES or "healthcare" in t:
            buckets["facilities"].append({**row, "geometry": g.representative_point()})
        if t.get("waterway") in ("river", "stream", "canal") or t.get("natural") == "water" or t.get("landuse") == "reservoir":
            buckets["water"].append(row)

    out = {}
    for k, rows in buckets.items():
        out[k] = gpd.GeoDataFrame(rows, crs=4326) if rows else gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
        log.info(f"  {k}: {len(out[k])}")
    return out