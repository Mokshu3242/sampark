import ast
import pathlib
import sys
import importlib.metadata as md

std = set(sys.stdlib_module_names)
local = {"app", "pipeline", "copilot", "eval", "scripts"}
mods = set()

for f in pathlib.Path(".").rglob("*.py"):
    if ".venv" in f.parts:
        continue
    try:
        tree = ast.parse(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])

pkgs = md.packages_distributions()
missing = ["NOT INSTALLED"]
for m in sorted(mods - std - local):
    print(m.ljust(20), "->", pkgs.get(m, missing))