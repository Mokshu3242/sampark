import re

DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
# A number not glued to a letter or dot on its left: "34", "4,308", "8.79", "-2.05"
NUM_RE = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
# Fields that are metadata, not facts a rescuer would quote
SKIP_KEYS = {
    "area_bbox", "thresholds", "pixel_size_m", "crs", "radar_orbit", "rules",
    "field_meanings", "osm_snapshot", "note", "network_note", "quality_notes",
    "confusion_px", "items", "by_class", "tolerance_m",
}
COUNTED_LISTS = {"villages", "packages", "villages_reconnected", "top_damaged_villages", "matches"}


def normalize(text: str) -> str:
    return text.translate(DEVANAGARI)


def extract_numbers(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(normalize(text)):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def build_pool(obj, path: str = "", out: list | None = None) -> list[tuple[float, str]]:
    """Numbers a correct answer may quote, with their source path."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            build_pool(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(obj, list):
        if path.split(".")[-1].split("[")[0] in COUNTED_LISTS:
            out.append((float(len(obj)), f"{path}.length"))
        for i, v in enumerate(obj):
            build_pool(v, f"{path}[{i}]", out)
    elif isinstance(obj, bool) or obj is None:
        pass
    elif isinstance(obj, (int, float)):
        out.append((float(obj), path))
    elif isinstance(obj, str):
        if path.endswith(("date", "_date")):        # "2026-08-16" -> allow 2026, 8, 16 from dates only
            for n in extract_numbers(obj.replace("-", " ")):
                out.append((n, path))
    return out

def _match(n: float, pool: list[tuple[float, str]]) -> str | None:
    for p, src in pool:
        if abs(n - p) < 1e-9:
            return src
    for p, src in pool:
        if abs(round(p, 1) - n) < 1e-9 or abs(round(p, 2) - n) < 1e-9:   # 8.79 written as 8.8
            return src
        if 0 < p <= 1 and (abs(p * 100 - n) < 1e-6 or abs(round(p * 100, 1) - n) < 1e-9):  # 0.972 -> 97.2%
            return src
    return None


def check_answer(answer: str, tool_outputs: list[dict]) -> dict:
    """Every number in the answer must trace to a tool result. Returns ok, unverified, and sources."""
    pool = []
    for t in tool_outputs:
        build_pool(t["result"], t["tool"], pool)
    sources, unverified = [], []
    for n in extract_numbers(answer):
        src = _match(n, pool)
        if src:
            sources.append({"number": n, "source": src})
        else:
            unverified.append(n)
    return {"ok": not unverified, "unverified": unverified, "sources": sources}