import json
import logging
import re
from pathlib import Path

from app.core.llm import LLM, LLMError, get_llm
from copilot.prompts import SYSTEM_EN
from copilot.tools import TOOL_NAMES, ToolBox, ToolError
from copilot.translate import to_english, to_nepali
from copilot.verify import check_answer

log = logging.getLogger("flood-watch.agent")

MAX_TURNS = 8
MAX_NUMBER_RETRIES = 1
MAX_BAD_TOOLS = 2
MAX_BAD_SHAPES = 3
DEVANAGARI_TEXT = re.compile(r"[\u0900-\u097F]")

TOOL_KEYS = ("tool", "name", "function", "tool_name", "action")
ARG_KEYS = ("args", "arguments", "parameters", "params", "input")
ANSWER_KEYS = ("answer", "final", "final_answer", "response", "text")


def _parse(raw: str) -> dict:
    s, e = raw.find("{"), raw.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("no JSON object")
    obj = json.loads(raw[s:e + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    return obj


def _clean_tool_name(name) -> str:
    """Models sometimes add prefixes like 'tool.' or 'functions.'."""
    name = str(name or "").strip()
    for prefix in ("tool.", "tools.", "functions.", "function."):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _as_args(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


def _normalize(obj: dict) -> dict:
    """Map the shapes models actually send onto {"tool","args"} or {"answer"}.
    Handles OpenAI style {"name","arguments"}, nested {"tool": {"name": ...}}, and {"tool_calls": [...]}."""
    if isinstance(obj.get("tool_calls"), list) and obj["tool_calls"]:
        first = obj["tool_calls"][0]
        if isinstance(first, dict):
            obj = first.get("function", first) if isinstance(first.get("function"), dict) else first

    for k in TOOL_KEYS:
        v = obj.get(k)
        if isinstance(v, dict):                                # {"tool": {"name": ..., "arguments": ...}}
            name = next((v[n] for n in ("name", "tool") if v.get(n)), None)
            if name:
                args = next((v[a] for a in ARG_KEYS if a in v), {})
                return {"tool": name, "args": _as_args(args)}
        elif isinstance(v, str) and v.strip():
            if k != "tool" and v.strip() not in TOOL_NAMES and _clean_tool_name(v) not in TOOL_NAMES:
                continue                                       # "name"/"text" that is not a tool
            args = next((obj[a] for a in ARG_KEYS if a in obj), {})
            return {"tool": v, "args": _as_args(args)}

    for k in ANSWER_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return {"answer": v}
    return {}


def _result(answer, tool_outputs, trace, provider, check=None, **extra) -> dict:
    return {
        "answer": answer,
        "language": "en",
        "sources": (check or {}).get("sources", []),
        "unverified_numbers": (check or {}).get("unverified", []),
        "tool_trace": trace,
        "provider": provider,
        **extra,
    }


def ask(question: str, run_dir: Path, language: str = "auto", llm: LLM | None = None,
        history: list[dict] | None = None) -> dict:
    """Answer a question. language: 'en', 'ne', or 'auto' (Nepali if the question is in Devanagari)."""
    llm = llm or get_llm()
    is_ne_text = bool(DEVANAGARI_TEXT.search(question))
    want_ne = language == "ne" or (language == "auto" and is_ne_text)

    q_en = to_english(question, llm) if is_ne_text else question
    if is_ne_text and q_en == question:
        q_en = question + "\n(The question is in Nepali. Understand it, but reply in English JSON.)"

    result = _answer_en(q_en, run_dir, llm, history or [])
    result["question_en"] = q_en

    if want_ne and result.get("verified"):
        ne = to_nepali(result["answer"], llm)
        result["answer_en"] = result["answer"]
        result["answer"], result["language"] = ne["answer"], ne["language"]
        if "note" in ne:
            result["note"] = ne["note"]
    return result


def _answer_en(question: str, run_dir: Path, llm: LLM, history: list[dict]) -> dict:
    box = ToolBox(run_dir)
    past = [{"role": h["role"], "content": str(h["content"])[:500]}
            for h in history[-6:] if h.get("role") in ("user", "assistant")]
    messages = [{"role": "system", "content": SYSTEM_EN}, *past, {"role": "user", "content": question}]
    tool_outputs, trace = [], []
    retries, bad_tools, bad_shapes = 0, 0, 0

    for turn in range(MAX_TURNS):
        try:
            raw = llm.chat(messages, temperature=0.1, max_tokens=700)
        except LLMError as e:
            log.warning(f"LLM failed: {str(e)[:200]}")
            return _result("The assistant is unavailable right now. Please try again.",
                           tool_outputs, trace, None, error=str(e)[:200], verified=False)

        try:
            obj = _normalize(_parse(raw))
        except Exception:
            obj = {}

        # ---- Not usable ----
        if not obj:
            bad_shapes += 1
            log.info(f"Turn {turn + 1}: unusable reply ({bad_shapes}/{MAX_BAD_SHAPES}): {raw[:200]}")
            if bad_shapes >= MAX_BAD_SHAPES:
                break
            messages += [{"role": "assistant", "content": raw[:1000]},
                         {"role": "user", "content":
                             'Reply with ONE JSON object only: {"tool": "<name>", "args": {}} '
                             'or {"answer": "<text>"}.'}]
            continue

        # ---- Tool call ----
        if "tool" in obj:
            tool = _clean_tool_name(obj["tool"])
            args = obj.get("args") or {}

            if tool not in TOOL_NAMES:
                bad_tools += 1
                log.info(f"Invalid tool name '{tool}' ({bad_tools}/{MAX_BAD_TOOLS})")
                if bad_tools >= MAX_BAD_TOOLS:
                    break
                messages += [{"role": "assistant", "content": json.dumps(obj)},
                             {"role": "user", "content":
                                 f"'{tool}' is not a tool. Valid tools: {', '.join(TOOL_NAMES)}. "
                                 "Reply with JSON only."}]
                continue

            try:
                res = box.dispatch(tool, args)
            except ToolError as e:
                res = {"error": str(e)}
            log.info(f"Turn {turn + 1}: tool {tool} {args or ''}")
            tool_outputs.append({"tool": tool, "args": args, "result": res})
            trace.append({"tool": tool, "args": args})
            messages += [{"role": "assistant", "content": json.dumps({"tool": tool, "args": args})},
                         {"role": "user", "content": f"Tool result: {json.dumps(res, ensure_ascii=False)}"}]
            continue

        # ---- Final answer ----
        answer = str(obj["answer"]).strip()
        if not tool_outputs:
            bad_shapes += 1
            log.info(f"Turn {turn + 1}: answered without a tool ({bad_shapes}/{MAX_BAD_SHAPES}): {answer[:150]}")
            if bad_shapes >= MAX_BAD_SHAPES:
                break
            messages += [{"role": "assistant", "content": json.dumps(obj)},
                         {"role": "user", "content": "Call a tool first, then answer from its result. JSON only."}]
            continue

        check = check_answer(answer, tool_outputs)
        if check["ok"]:
            return _result(answer, tool_outputs, trace, llm.last_provider, check, verified=True)

        if retries < MAX_NUMBER_RETRIES:
            retries += 1
            log.info(f"Unverified numbers {check['unverified']}, retrying")
            messages += [{"role": "assistant", "content": json.dumps(obj)},
                         {"role": "user", "content":
                             f"These numbers are not in any tool result: {check['unverified']}. "
                             "Use only numbers from tool results, or call another tool. JSON only."}]
            continue

        return _result("I could not verify every number for that question, so I won't guess. "
                       "Please check the dashboard or ask more specifically.",
                       tool_outputs, trace, llm.last_provider, check, verified=False)

    log.info(f"No verified answer. Trace: {trace}")
    return _result("I couldn't reach a verified answer. Please try a simpler question.",
                   tool_outputs, trace, llm.last_provider, verified=False)