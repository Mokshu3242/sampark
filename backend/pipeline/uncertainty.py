import numpy as np

NODATA = 255


def score(detected: np.ndarray, lr: np.ndarray, flood_thr: float,
          slope: np.ndarray, hand: np.ndarray,
          s2_water: np.ndarray | None, s2_clear: np.ndarray | None) -> np.ndarray:
    """
    Points for each detected pixel:
      +1 change is close to the threshold (weak signal)
      +1 slope above 10 degrees
      +1 more than 15 m above the river
      +1 clear S2 after the event says dry
      -1 clear S2 after the event confirms water
    0 or less = low, 1 = medium, 2+ = high. Undetected pixels = 255.
    """
    pts = np.zeros(detected.shape, dtype=np.int8)
    pts += (lr > flood_thr - 1.0).astype(np.int8)
    pts += (slope > 10).astype(np.int8)
    pts += (hand > 15).astype(np.int8)
    if s2_water is not None and s2_clear is not None:
        pts += (s2_clear & ~s2_water).astype(np.int8)
        pts -= (s2_clear & s2_water).astype(np.int8)

    out = np.full(detected.shape, NODATA, dtype=np.uint8)
    out[detected & (pts <= 0)] = 0
    out[detected & (pts == 1)] = 1
    out[detected & (pts >= 2)] = 2
    return out