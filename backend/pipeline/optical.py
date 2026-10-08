import numpy as np

MNDWI_WATER = 0.0


def mndwi(b03: np.ndarray, b11: np.ndarray) -> np.ndarray:
    """(Green - SWIR1) / (Green + SWIR1). Above 0 is usually water."""
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (b03 - b11) / (b03 + b11)
    return np.where(np.isfinite(out), out, np.nan)


def water_and_clear(b03: np.ndarray, b11: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Returns (water, clear). Cloudy or missing pixels are not 'dry', they're unknown."""
    idx = mndwi(b03, b11)
    clear = np.isfinite(idx)
    return clear & (idx > MNDWI_WATER), clear