import logging

import numpy as np
from skimage.filters import threshold_otsu

log = logging.getLogger("flood-watch.change")

DB_FLOOR = -35.0          # low enough to keep real water values
WATER_VV_DB = -15.0       # flooded pixels must be this dark after the event
FLOOD_THR_RANGE = (-6.0, -2.0)
DEBRIS_THR_RANGE = (2.5, 6.0)

def to_db(linear: np.ndarray) -> np.ndarray:
    floor = 10 ** (DB_FLOOR / 10)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(linear > 0, 10.0 * np.log10(np.maximum(linear, floor)), np.nan)


def _otsu_tail(values: np.ndarray, sign: int, default: float, rng: tuple[float, float]) -> float:
    tail = values[np.isfinite(values) & (sign * values > 0)]
    if tail.size < 500:
        log.warning(f"Too few pixels for Otsu, using default {default}")
        return default
    thr = sign * float(threshold_otsu(np.abs(tail)))
    return float(np.clip(thr, *rng))


def thresholds(lr: np.ndarray, candidates: np.ndarray) -> dict:
    """Otsu on each tail, using only pixels on plausible terrain. Clamped to sane ranges."""
    v = lr[candidates]
    return {
        "flood_thr_db": _otsu_tail(v, -1, -3.0, FLOOD_THR_RANGE),
        "debris_thr_db": _otsu_tail(v, +1, 3.0, DEBRIS_THR_RANGE),
        "water_vv_after_db": WATER_VV_DB,
    }