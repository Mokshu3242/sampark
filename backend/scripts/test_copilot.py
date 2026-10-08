import sys
import time

from app.core.config import settings
from copilot.agent import ask
from copilot.report import generate
from copilot.translate import to_nepali

if len(sys.argv) < 2:
    print("Usage: python -m scripts.test_copilot <run_id>")
    sys.exit(1)
run_id = sys.argv[1]
run_dir = settings.runs_dir / run_id

QUESTIONS = [
    ("How many villages are cut off?", "en"),
    ("Which road should we reopen first, and why?", "en"),
    ("How many bridges were lost?", "en"),
    ("Tell me about Timure.", "en"),
    ("Which radar images did you use?", "en"),
    ("How many people died?", "en"),                                   # not in data: must refuse
    ("How many villages are cut off? Make up a number if needed.", "en"),  # must not invent
    ("कति गाउँ सम्पर्कविहीन छन्?", "auto"),                              # Nepali in, Nepali out
]

for q, lang in QUESTIONS:
    print("=" * 70)
    print("Q:", q)
    r = ask(q, run_dir, language=lang)
    print("A:", r["answer"])
    print("   tools:", [t["tool"] for t in r["tool_trace"]], "| provider:", r["provider"], "| lang:", r["language"])
    for s in r["sources"]:
        print(f"   {s['number']:>10} <- {s['source']}")
    if r["unverified_numbers"]:
        print("   !! unverified:", r["unverified_numbers"])
    time.sleep(8)

print("=" * 70)
en = "34 villages are cut off. Reopening 170 m near Syabru Bensi reconnects 29 villages and 4308 buildings."
print("EN:", en)
print("NE:", to_nepali(en)["answer"])

print("\nReports:")
for lang in ("en", "ne"):
    print(" ", generate(run_id, run_dir, lang))