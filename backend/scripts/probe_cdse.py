import json
import requests
import openeo

STAC = "https://stac.dataspace.copernicus.eu/v1"

print("=== STAC collections with 'sentinel-1' ===")
cols = requests.get(f"{STAC}/collections", timeout=60).json()["collections"]
print([c["id"] for c in cols if "sentinel-1" in c["id"].lower()])

print("\n=== One S1 GRD item over the corridor ===")
r = requests.post(f"{STAC}/search", json={
    "collections": ["sentinel-1-grd"],
    "bbox": [85.15, 27.90, 85.45, 28.30],
    "datetime": "2026-08-01T00:00:00Z/2026-09-10T23:59:59Z",
    "limit": 1,
}, timeout=60)
print("status:", r.status_code)
feats = r.json().get("features", [])
if feats:
    print(json.dumps(feats[0]["properties"], indent=2)[:2500])

print("\n=== openEO collection metadata ===")
conn = openeo.connect("openeo.dataspace.copernicus.eu").authenticate_oidc()
for cid in ("SENTINEL1_GRD", "SENTINEL2_L2A", "COPERNICUS_30"):
    meta = conn.describe_collection(cid)
    bands = meta.get("cube:dimensions", {}).get("bands", {}).get("values")
    print(cid, "bands:", bands)
    print("  queryables:", list(meta.get("summaries", {}).keys())[:15])