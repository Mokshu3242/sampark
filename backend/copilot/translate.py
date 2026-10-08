import logging
import re

from app.core.llm import LLM, LLMError, get_llm
from copilot.prompts import TRANSLATE_SYSTEM
from copilot.verify import NUM_RE, extract_numbers, normalize
import re


UNITS = [(r"(\d)\s?km\b", r"\1 kilometres"), (r"(\d)\s?m\b", r"\1 metres"), (r"(\d)\s?ha\b", r"\1 hectares")]

log = logging.getLogger("flood-watch.translate")

TO_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")
MARKER = re.compile(r"\[\[(\d+)\]\]")


def to_nepali(english: str, llm: LLM | None = None, devanagari_digits: bool = True) -> dict:
    """Translate with numbers locked behind [[n]] markers. The model never sees a number."""
    llm = llm or get_llm()
    values: list[str] = []

    def lock(m):
        values.append(m.group(0))
        return f"[[{len(values)}]]"
    
    for pat, rep in UNITS:
        english = re.sub(pat, rep, english)
        
    templated = NUM_RE.sub(lock, normalize(english))

    for attempt in range(2):
        try:
            raw = llm.chat([{"role": "system", "content": TRANSLATE_SYSTEM},
                            {"role": "user", "content": templated}], temperature=0.1, max_tokens=800)
        except LLMError as e:
            log.warning(f"Translation failed: {e}")
            break

        found = [int(x) for x in MARKER.findall(raw)]
        stray = extract_numbers(MARKER.sub("", raw))
        if sorted(found) != list(range(1, len(values) + 1)) or stray:
            log.warning(f"Attempt {attempt + 1}: markers {found}, stray digits {stray}")
            continue

        def unlock(m):
            v = values[int(m.group(1)) - 1]
            return v.translate(TO_DEVANAGARI) if devanagari_digits else v

        return {"answer": MARKER.sub(unlock, raw).strip(), "language": "ne", "provider": llm.last_provider}

    return {"answer": english, "language": "en", "provider": llm.last_provider,
            "note": "Nepali translation could not be verified, showing English."}