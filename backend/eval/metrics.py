import numpy as np
from scipy.ndimage import binary_dilation


def scores(pred: np.ndarray, ref: np.ndarray, valid: np.ndarray, px_ha: float, tol_px: int = 2) -> dict:
    """Strict and offset-tolerant agreement inside `valid`."""
    p, r = pred & valid, ref & valid
    tp, fp, fn = int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum())
    tn = int((valid & ~p & ~r).sum())

    def div(a, b):
        return round(a / b, 3) if b else None

    prec, rec = div(tp, tp + fp), div(tp, tp + fn)
    f1 = round(2 * prec * rec / (prec + rec), 3) if prec and rec else 0.0

    # Tolerant: a hit counts if the other map has something within tol_px pixels
    r_d = binary_dilation(r, iterations=tol_px) & valid
    p_d = binary_dilation(p, iterations=tol_px) & valid
    prec_t, rec_t = div(int((p & r_d).sum()), int(p.sum())), div(int((r & p_d).sum()), int(r.sum()))
    f1_t = round(2 * prec_t * rec_t / (prec_t + rec_t), 3) if prec_t and rec_t else 0.0

    return {
        "precision": prec, "recall": rec, "f1": f1, "iou": div(tp, tp + fp + fn),
        "precision_tolerant": prec_t, "recall_tolerant": rec_t, "f1_tolerant": f1_t,
        "tolerance_m": None,  # filled by caller
        "ours_ha": round(float(p.sum()) * px_ha, 2), "ems_ha": round(float(r.sum()) * px_ha, 2),
        "overlap_ha": round(tp * px_ha, 2),
        "confusion_px": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }