import logging

import numpy as np
from scipy import ndimage

log = logging.getLogger("flood-watch.terrain")

MAX_SLOPE_FLOOD = 15.0     # degrees
MAX_HAND_FLOOD = 25.0      # meters above nearest river
MAX_SLOPE_DEBRIS = 25.0
MAX_HAND_DEBRIS = 60.0
MAX_DIST_DEBRIS_M = 1000.0
FALLBACK_WINDOW_M = 1500.0


def hand_and_distance(dem: np.ndarray, river: np.ndarray, pixel_m: float) -> tuple[np.ndarray, np.ndarray]:
    """
    HAND: how high each pixel sits above its nearest river pixel.
    Also returns distance to the nearest river in meters.
    Falls back to height above the local minimum if no rivers were found.
    """
    dem_f = np.where(np.isfinite(dem), dem, np.nanmax(dem))
    if river.any():
        dist_px, (iy, ix) = ndimage.distance_transform_edt(~river, return_indices=True)
        hand = dem_f - dem_f[iy, ix]
        dist_m = dist_px * pixel_m
    else:
        log.warning("No river pixels. Using local-minimum fallback for HAND.")
        size = max(3, int(FALLBACK_WINDOW_M / pixel_m))
        hand = dem_f - ndimage.minimum_filter(dem_f, size=size)
        dist_m = np.zeros_like(dem_f)
    hand[~np.isfinite(dem)] = np.nan
    return hand.astype(np.float32), dist_m.astype(np.float32)


def masks(slope: np.ndarray, hand: np.ndarray, dist_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ok = np.isfinite(slope) & np.isfinite(hand)
    flood_ok = ok & (slope <= MAX_SLOPE_FLOOD) & (hand <= MAX_HAND_FLOOD)
    debris_ok = ok & (slope <= MAX_SLOPE_DEBRIS) & (hand <= MAX_HAND_DEBRIS) & (dist_m <= MAX_DIST_DEBRIS_M)
    log.info(f"Terrain: flood-plausible {flood_ok.mean():.1%}, debris-plausible {debris_ok.mean():.1%}")
    return flood_ok, debris_ok