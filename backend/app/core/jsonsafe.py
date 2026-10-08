import math


def clean(obj):
    """Recursively make a value JSON-safe: NaN/inf -> None, numpy -> Python."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            obj = obj.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj