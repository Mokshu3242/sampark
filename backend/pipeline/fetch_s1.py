import logging
from datetime import date, datetime, timedelta
from typing import Any

import requests
from shapely.geometry import box, shape
from shapely.ops import unary_union

log = logging.getLogger("flood-watch.s1")

# Confirm these with probe_cdse.py
STAC_URL = "https://stac.dataspace.copernicus.eu/v1/search"
COLLECTION = "sentinel-1-grd"
P_ORBIT = "sat:relative_orbit"
P_DIRECTION = "sat:orbit_state"
P_MODE = "sar:instrument_mode"
P_POLS = "sar:polarizations"

MIN_COVERAGE = 0.9


def search_s1_items(bbox: list[float], start: date, end: date) -> list[dict[str, Any]]:
    """Query STAC, follow pagination, keep only IW-mode scenes."""
    body = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": 100,
    }
    items, url = [], STAC_URL
    while url:
        r = requests.post(url, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("features", []))
        nxt = next((l for l in data.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            break
        url, body = nxt["href"], nxt.get("body", body)

    iw = [it for it in items if it["properties"].get(P_MODE, "IW") == "IW"]
    log.info(f"STAC: {len(items)} items, {len(iw)} IW")
    return iw


def item_date(item: dict) -> date:
    return datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00")).date()


def _orbit_key(item: dict) -> tuple | None:
    p = item["properties"]
    orbit, direction = p.get(P_ORBIT), p.get(P_DIRECTION)
    if orbit is None or direction is None:
        return None
    pols = tuple(sorted(p.get(P_POLS, []) or []))
    return (int(orbit), str(direction).lower(), pols)


def _coverage(items: list[dict], bbox: list[float]) -> float:
    aoi = box(*bbox)
    footprint = unary_union([shape(it["geometry"]) for it in items])
    return footprint.intersection(aoi).area / aoi.area

def _single_orbit_on_date(items: list[dict], day: date, key: tuple) -> bool:
    """True if no other orbit in the same direction flew over this area on this date."""
    for it in items:
        k = _orbit_key(it)
        if k and item_date(it) == day and k[1] == key[1] and k[0] != key[0]:
            return False
    return True


def _time_window(items: list[dict]) -> list[str]:
    """Exact acquisition window (start to end), padded by 1 minute, in openEO format."""
    def parse(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    starts = [parse(it["properties"].get("start_datetime", it["properties"]["datetime"])) for it in items]
    ends = [parse(it["properties"].get("end_datetime", it["properties"]["datetime"])) for it in items]
    pad = timedelta(minutes=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return [(min(starts) - pad).strftime(fmt), (max(ends) + pad).strftime(fmt)]

def pick_best_pair(items: list[dict], bbox: list[float], flood_date: date) -> dict | None:
    """Group by orbit, then by date. Pick the closest well-covered before/after pair."""
    groups: dict[tuple, dict[date, list[dict]]] = {}
    for it in items:
        key = _orbit_key(it)
        if key is None:
            continue
        groups.setdefault(key, {}).setdefault(item_date(it), []).append(it)

    best, best_score = None, float("inf")
    for key, by_date in groups.items():
        dates = {d: _coverage(its, bbox) for d, its in by_date.items()}
        before = sorted([d for d, c in dates.items()
                         if d < flood_date and c >= MIN_COVERAGE and _single_orbit_on_date(items, d, key)],
                        reverse=True)
        after = sorted([d for d, c in dates.items()
                        if d > flood_date and c >= MIN_COVERAGE and _single_orbit_on_date(items, d, key)])
        if not before or not after:
            continue

        b, a = before[0], after[0]
        gap_b, gap_a = (flood_date - b).days, (a - flood_date).days
        # Favor a short wait after the event, then a close before image
        score = 2 * gap_a + gap_b

        if score < best_score:
            best_score = score
            best = {
                "orbit": key[0],
                "direction": key[1],
                "polarizations": list(key[2]),
                "before_date": b.isoformat(),
                "after_date": a.isoformat(),
                "before_ids": [it["id"] for it in by_date[b]],
                "after_ids": [it["id"] for it in by_date[a]],
                "coverage_before": round(dates[b], 3),
                "coverage_after": round(dates[a], 3),
                "gap_before_days": gap_b,
                "gap_after_days": gap_a,
                "before_time": _time_window(by_date[b]),
                "after_time": _time_window(by_date[a]),
            }
    return best


def find_pair(bbox: list[float], flood_date: date, window_days: int = 30) -> dict | None:
    """Search, widening the window if nothing fits."""
    for window in (window_days, window_days + 15, window_days + 30):
        items = search_s1_items(bbox, flood_date - timedelta(days=window), flood_date + timedelta(days=window))
        pair = pick_best_pair(items, bbox, flood_date)
        if pair:
            pair["window_days"] = window
            return pair
        log.info(f"No pair in ±{window} days, widening")
    return None