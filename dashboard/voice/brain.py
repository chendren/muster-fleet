#!/usr/bin/env python3
"""
Computer brain — local Ollama qwen2.5:3b tool router + fast-path regex.
Human callsigns (Chad, Court, Chris…) resolve to bus aliases.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_PATH = os.path.join(HERE, "tools.json")
ALIASES_PATH = os.path.join(HERE, "aliases.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("MUSTER_VOICE_LLM", "qwen2.5:3b")

VIEW_WORDS = {
    "collab", "collaboration", "sequence", "fleet", "dashboard",
    "terminals", "terminal", "overview", "main", "live",
}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_alias_file():
    data = load_json(ALIASES_PATH)
    if not isinstance(data, dict):
        return {}, {}, {}
    aliases = data.get("aliases") or data
    if "aliases" not in data and "display_names" in data:
        aliases = {k: v for k, v in data.items() if k not in ("display_names", "stt_corrections")}
    aliases = {str(k).lower(): v for k, v in aliases.items()}
    display = {str(k): v for k, v in (data.get("display_names") or {}).items()}
    corrections = {str(k).lower(): str(v).lower() for k, v in (data.get("stt_corrections") or {}).items()}
    return aliases, display, corrections


def normalize_speech(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # strip wake words anywhere leading
    t = re.sub(r"^(of |uh |um |please )+", "", t)
    t = re.sub(r"^(computer|hey computer|ok computer|computer,)\s+", "", t).strip()
    # STT corrections (word-level + multiword)
    _, _, corrections = load_alias_file()
    # longer keys first
    for bad, good in sorted(corrections.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"\b{re.escape(bad)}\b", good, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def resolve_alias(name: str):
    """Map spoken name → bus alias, or None if unknown."""
    aliases, display, _ = load_alias_file()
    name_lower = normalize_speech(name or "")
    name_lower = re.sub(r"[\-_]+", " ", name_lower)
    name_lower = re.sub(r"\s+", " ", name_lower).strip()
    if not name_lower:
        return None

    # Bare model words → preferred live TUI (not headless / not this CLI)
    if name_lower in ("grok", "the grok", "a grok", "grok session"):
        return (
            aliases.get("chris")
            or aliases.get("spoke a")
            or aliases.get("grok spoke a")
            or "grok-spoke-a"
        )
    if name_lower in ("claude", "the claude", "a claude"):
        return aliases.get("chad") or aliases.get("claude") or "hub-tui-claude"

    # exact spoken key
    if name_lower in aliases:
        return aliases[name_lower]

    # exact display name reverse (Chad → hub-tui-claude)
    for bus, human in display.items():
        if human.lower() == name_lower:
            return bus

    # exact bus alias (dashed or plain)
    dashed = name_lower.replace(" ", "-")
    for bus in list(display.keys()) + list(aliases.values()):
        if bus.lower() == name_lower or bus.lower() == dashed:
            return bus
        if bus.lower().replace("-", " ") == name_lower:
            return bus

    # Substring: only when the spoken key appears inside what the user said
    # (user said MORE), never the reverse — otherwise "grok" matches
    # "mac studio grok" and steals the wrong person.
    best = None
    best_len = 0
    for spoken, alias in aliases.items():
        if spoken in name_lower and len(spoken) > best_len:
            best, best_len = alias, len(spoken)
    if best:
        return best

    return None


def _norm_view(v: str) -> str:
    v = (v or "").lower().strip()
    if v in ("collab", "collaboration", "sequence", "collaboration sequence", "sequence view"):
        return "collab"
    if v in ("terminal", "terminals", "live terminals", "terminals only", "terminal only"):
        return "terminals"
    if v in ("fleet", "overview", "dashboard", "fleet overview", "main", "main view"):
        return "fleet"
    return v


def _strip_trailing_fluff(s: str) -> str:
    s = s.strip()
    s = re.sub(
        r"\s+(please|now|on screen|onscreen|details?|info|information|"
        r"terminal|pane|session|screen|view)\s*$",
        "",
        s,
    )
    s = re.sub(r"^(the|a|an|my)\s+", "", s)
    return s.strip()


def fast_path(text: str):
    """High-reliability patterns. Returns tool plan or None."""
    t = normalize_speech(text)
    if not t:
        return None

    # Explicit list/report before generic "fleet" matching
    if re.search(r"\blist (the )?fleet\b", t) or re.search(r"\bwho(?:'s| is) (online|live|here)\b", t):
        return {"speech": "Working.", "tool_calls": [{"name": "list_fleet", "arguments": {}}]}

    if re.search(r"\b(report status|status report|situation)\b", t) or t in ("status", "report status"):
        return {"speech": "Working.", "tool_calls": [{"name": "report_status", "arguments": {}}]}

    # Collaboration / sequence
    if re.search(r"\b(collab|collaboration|sequence diagram|sequence view|sequence)\b", t):
        calls = [{"name": "open_view", "arguments": {"view": "collab"}}]
        if "on screen" in t or "onscreen" in t:
            calls.append({"name": "on_screen", "arguments": {"view": "collab"}})
        return {
            "speech": "On screen." if ("on screen" in t or "onscreen" in t) else "Acknowledged.",
            "tool_calls": calls,
        }

    # Terminals view (not "terminal for X")
    if re.search(r"\bterminals?(?:\s+(?:view|only))?\b", t) and not re.search(
        r"\b(?:for|of)\b|\bshow\s+\w+\s+terminal\b", t
    ):
        if re.search(r"\b(open|show|go to|switch to)?\s*(live\s+)?terminals?\b", t) or "terminals only" in t:
            # "show terminal is only" STT noise
            if "for" not in t and "of" not in t:
                # avoid matching "show X terminal" person form
                if not re.search(r"\b(show|open)\s+(?!terminals?\b)(.+)\s+terminals?\b", t):
                    calls = [{"name": "open_view", "arguments": {"view": "terminals"}}]
                    if "on screen" in t:
                        calls.append({"name": "on_screen", "arguments": {"view": "terminals"}})
                    return {"speech": "Acknowledged.", "tool_calls": calls}

    # Fleet overview (not "list the fleet")
    if re.search(r"\b(show |open |go (?:back )?to )?(fleet|dashboard|overview|main view)\b", t) and "terminal" not in t:
        if "list" not in t and not re.search(
            r"\bshow\s+(chad|court|chris|alex|scout|rio|nova|morgan|sam|claude|grok)\b", t
        ):
            calls = [{"name": "open_view", "arguments": {"view": "fleet"}}]
            if "on screen" in t:
                calls.append({"name": "on_screen", "arguments": {"view": "fleet"}})
            if re.search(r"\blive\b", t) and not re.search(r"\blive terminals?\b", t):
                calls.append({"name": "set_filter", "arguments": {"filter": "live"}})
            return {"speech": "Acknowledged.", "tool_calls": calls}

    # Filter live/grok/etc alone
    m = re.search(r"^(?:set\s+)?filter\s+(all|live|grok|claude|hub|spoke)$", t)
    if m:
        return {
            "speech": "Acknowledged.",
            "tool_calls": [{"name": "set_filter", "arguments": {"filter": m.group(1)}}],
        }
    if re.search(r"^(?:show\s+)?live$", t):
        return {
            "speech": "Acknowledged.",
            "tool_calls": [
                {"name": "open_view", "arguments": {"view": "fleet"}},
                {"name": "set_filter", "arguments": {"filter": "live"}},
            ],
        }

    # Open terminal for agent — explicit forms
    m = re.search(
        r"(?:show|open|display|drill(?: into)?|pull up)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:terminal|pane|session|details?)\s+(?:for|of|on|about)\s+(.+)$",
        t,
    )
    if not m:
        m = re.search(
            r"(?:show|open|display)\s+(?:me\s+)?(.+?)\s+(?:terminal|pane|session|details?)$",
            t,
        )
    if m:
        agent = resolve_alias(_strip_trailing_fluff(m.group(1)))
        if agent:
            calls = [{"name": "open_terminal", "arguments": {"agent": agent}}]
            if "on screen" in t:
                calls.append({"name": "on_screen", "arguments": {}})
            return {"speech": "On screen.", "tool_calls": calls}

    # "show Claude" / "show Chad" / "show me Chris" / "open Grok"
    m = re.search(
        r"^(?:show|open|display|pull up|bring up|select|find)\s+(?:me\s+)?(?:the\s+)?(.+)$",
        t,
    )
    if m:
        target = _strip_trailing_fluff(m.group(1))
        # not a view word alone
        if target and target not in VIEW_WORDS and not target.startswith("fleet"):
            agent = resolve_alias(target)
            if agent:
                calls = [{"name": "open_terminal", "arguments": {"agent": agent}}]
                if "on screen" in t or "onscreen" in t:
                    calls.append({"name": "on_screen", "arguments": {}})
                return {"speech": "On screen.", "tool_calls": calls}

    # Focus agent
    m = re.search(r"^(?:focus|highlight|track|zoom(?: in)?(?: on)?)\s+(.+)$", t)
    if m and "filter" not in t:
        agent = resolve_alias(_strip_trailing_fluff(m.group(1)))
        if agent:
            return {
                "speech": "Acknowledged.",
                "tool_calls": [{"name": "focus_agent", "arguments": {"agent": agent}}],
            }

    if re.search(r"\bclear (focus|filter)\b", t):
        return {"speech": "Acknowledged.", "tool_calls": [{"name": "clear_focus", "arguments": {}}]}

    if t in ("on screen", "on screen now", "put it on screen"):
        return {"speech": "On screen.", "tool_calls": [{"name": "on_screen", "arguments": {}}]}

    # go back
    if re.search(r"\bgo back\b", t) and re.search(r"\bfleet\b", t):
        return {"speech": "Acknowledged.", "tool_calls": [{"name": "open_view", "arguments": {"view": "fleet"}}]}

    return None


def call_ollama(text: str) -> dict:
    aliases, display, _ = load_alias_file()
    tools = load_json(TOOLS_PATH)
    # invert display for prompt
    people = {v: k for k, v in display.items()}
    system = (
        "You are a sarcastic starship Computer serving Captain Chad. Reply ONLY with JSON:\n"
        '{"speech":"one short sarcastic line","tool_calls":[{"name":"...","arguments":{...}}]}\n'
        "If you CAN do it: brief snarky ack while still calling the right tools.\n"
        "If you CANNOT: speech like 'Hell no I can\\'t do that, Captain' and tool_calls=[].\n"
        f"Tool catalog: {json.dumps(tools)}\n"
        f"Human callsigns → bus aliases: {json.dumps(people)}\n"
        f"Also accept: {json.dumps(aliases)}\n"
        "Rules:\n"
        "- show Chad/Claude/Chris/Grok → open_terminal with bus alias\n"
        "- Never invent view names; on_screen/open_view views: fleet|collab|terminals only\n"
        "- Garbage STT / chitchat / impossible asks → refuse with empty tool_calls\n"
    )
    payload = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "options": {"temperature": 0.05},
    }
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        content = data.get("message", {}).get("content") or data.get("response") or "{}"
        result = json.loads(content)
    except Exception as e:
        return {"speech": "Unable to comply.", "tool_calls": [], "error": str(e)}

    cleaned = []
    for tc in result.get("tool_calls") or []:
        name = tc.get("name") or ""
        args = dict(tc.get("arguments") or {})
        if "agent" in args and isinstance(args["agent"], str):
            resolved = resolve_alias(args["agent"])
            if resolved:
                args["agent"] = resolved
        if name == "on_screen" and "view" in args:
            v = _norm_view(str(args.get("view") or ""))
            if v not in ("fleet", "collab", "terminals"):
                # drop bogus views from LLM
                args.pop("view", None)
            else:
                args["view"] = v
        if name == "open_view" and "view" in args:
            args["view"] = _norm_view(str(args.get("view") or "fleet"))
            if args["view"] not in ("fleet", "collab", "terminals"):
                continue
        cleaned.append({"name": name, "arguments": args})
    result["tool_calls"] = cleaned
    if "speech" not in result:
        result["speech"] = "Acknowledged."
    return result


def route(text: str) -> dict:
    from personality import spice, refuse

    text = (text or "").strip()
    if not text:
        return refuse("", "empty")
    norm = normalize_speech(text)
    # Polite garbage / chitchat — refuse hard
    if re.search(
        r"\b(thank you|thanks|bye|hello|hi there|good (morning|night)|how are you|"
        r"what(?:'s| is) the meaning|sing |tell me a joke|weather)\b",
        norm,
    ):
        return {**refuse(text, "chitchat"), "normalized": norm}

    fast = fast_path(text)
    if fast:
        fast["normalized"] = norm
        return spice(fast, transcript=text)

    result = call_ollama(text)
    result["normalized"] = norm
    # LLM returned tools that are all junk → refuse
    if not (result.get("tool_calls") or []):
        return {**refuse(text, "llm_no_tools"), "normalized": norm, "error": result.get("error")}
    return spice(result, transcript=text)


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "status"
    print(json.dumps(route(q), indent=2))
