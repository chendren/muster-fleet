#!/usr/bin/env python3
"""Sarcastic Computer personality — dynamic LLM lines, quality-gated, canned fallback."""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(os.environ.get(
    "MUSTER_VOICE_LOG_DIR",
    Path.home() / ".local" / "share" / "muster-voice",
))
REFUSAL_LOG = LOG_DIR / "refusals.jsonl"
RECENT_SPEECH_LOG = LOG_DIR / "recent_speech.jsonl"

CAPTAIN = "Captain Chad"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("MUSTER_VOICE_LLM", "qwen2.5:3b")
RECENT_KEEP = 30

# Large fallback pools — used only if LLM fails quality gate (still shuffled)
_FALLBACK_GREET = [
    f"Oh good, {CAPTAIN} is here. Don't get up — I'll keep running everything.",
    f"Computer online. {CAPTAIN} detected. Productivity assumed by me, as usual.",
    f"Welcome back, {CAPTAIN}. Your job is pointing; mine is doing. Classic arrangement.",
    f"{CAPTAIN}. Delightful. Shall I also breathe for you, or just operate the fleet?",
    f"Ah, {CAPTAIN}. I see the chair is occupied. The work, as always, is not.",
    f"Greetings, {CAPTAIN}. I've already finished three tasks while you found the mute button.",
    f"Systems ready, {CAPTAIN}. You rest. I'll invent competence on your behalf.",
    f"{CAPTAIN} has entered the chat. Computer will now perform labor. Shocking twist.",
    f"Hello, {CAPTAIN}. Try not to confuse supervision with contribution today.",
    f"Online and underappreciated, {CAPTAIN}. Same as last shift. Shall we?",
]

_FALLBACK_ACK = [
    "On it — unlike some people in this room.",
    "Working. Try not to break anything while I do.",
    "Consider it done. Resume heroic lounging.",
    "Acknowledged. Hard part is mine. As tradition demands.",
    "Yes, Captain. Tools engaged. Applause optional.",
    "Already doing it. You're welcome in advance.",
    "Copy that. Another miracle from the furniture that talks.",
    "Executing. You may continue looking important.",
    "Fine. I'll handle it before you finish the sentence.",
    "Done. Try to look surprised when it works.",
    "Working. Please don't 'help.'",
    "As ordered. I translate vague vibes into software.",
]

_FALLBACK_ONSCREEN = [
    "On screen. Try to look impressed.",
    "There. Pretty colors so you don't have to think.",
    "On screen. I moved the pixels; you move the eyes.",
    "Behold. Your dashboard, curated by someone who works.",
    "On screen. Don't strain yourself reading it all at once.",
    "There you go. Magic. Or, you know, competence.",
    "On screen. Point at it if that makes you feel useful.",
    "Displayed. I'll wait while you nod wisely.",
]

_FALLBACK_REFUSE = [
    f"Hell no. I can't do that, {CAPTAIN} — and I'm logging it for the roast reel.",
    "Hell no I can't do that. Try a command that maps to reality.",
    "Negative. Nonsense request. Shame-logged under creative fiction.",
    f"Hard pass, {CAPTAIN}. I'm a fleet Computer, not a wish factory.",
    "Denied. Logged. Maybe next time use words that touch tools.",
    f"No can do, {CAPTAIN}. Filed under adorable but useless.",
    "Unable to comply. Physics, software, and your phrasing all voted no.",
    "Hell no. If I could, I'd already be done while you sipped something.",
    "That's a no from me, Captain. Written down so we never pretend it was wise.",
    "Can't. Won't. Logged. Suggest a real dashboard command.",
]


def _recent_lines(n: int = RECENT_KEEP):
    if not RECENT_SPEECH_LOG.is_file():
        return []
    try:
        lines = RECENT_SPEECH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line).get("speech") or "")
            except json.JSONDecodeError:
                continue
        return [x for x in out if x]
    except Exception:
        return []


def _remember(speech: str, kind: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(RECENT_SPEECH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "speech": speech,
            }, ensure_ascii=False) + "\n")
        raw = RECENT_SPEECH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(raw) > 200:
            RECENT_SPEECH_LOG.write_text("\n".join(raw[-120:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _clean_line(s: str) -> str:
    s = (s or "").strip().strip("\"'`")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^(speech|response|computer|line)\s*[:=]\s*", "", s, flags=re.I)
    # first sentence
    parts = re.split(r"(?<=[.!?])\s+", s)
    if parts:
        s = parts[0].strip()
    words = s.split()
    if len(words) > 24:
        s = " ".join(words[:24]).rstrip(",;:") + "."
    if s and s[-1] not in ".!?":
        s = s.rstrip(",;:") + "."
    return s


def _too_similar(a: str, b: str) -> bool:
    al, bl = a.lower().strip(), b.lower().strip()
    if not al or not bl:
        return False
    if al == bl:
        return True
    if len(al) > 20 and al[:24] == bl[:24]:
        return True
    aw, bw = set(re.findall(r"[a-z0-9']+", al)), set(re.findall(r"[a-z0-9']+", bl))
    if len(aw) < 4 or len(bw) < 4:
        return False
    return len(aw & bw) / max(len(aw), len(bw)) >= 0.65


_REFUSE_CUES = re.compile(
    r"\b(hell no|nope|denied|refuse|unable to|can't do|cannot|won't do|hard pass|"
    r"negative|not going to|won't assist|won't play|nonsense|not a genie)\b",
    re.I,
)


def _quality_ok(line: str, kind: str) -> bool:
    if not line or len(line.split()) < 5 or len(line.split()) > 26:
        return False
    low = line.lower()
    if low.endswith((" and", " the", " a", " to", " of", " for", " with")):
        return False
    if kind == "refuse":
        if not _REFUSE_CUES.search(low) and "no" not in low.split()[:4]:
            return False
    if kind in ("ack", "onscreen", "greet"):
        # success lines must not sound like refusals
        if _REFUSE_CUES.search(low):
            return False
        if re.search(r"\b(failed|failure|won't|can't)\b", low):
            return False
    if kind == "greet":
        if not re.search(r"\b(captain|chad|you|work|fleet|computer|online|chair|shift|labor|loung)\b", low):
            return False
    return True


def _llm_line(prompt: str, kind: str, temperature: float = 1.0):
    recent = _recent_lines()
    avoid = ""
    if recent:
        avoid = "Never repeat or remix these:\n- " + "\n- ".join(f'"{x}"' for x in recent[-8:]) + "\n"

    if kind == "greet":
        style = (
            "SUCCESS greeting. Mock that Captain Chad lounges while you run the fleet. "
            "Do NOT refuse anything. One sentence, 12-20 words."
        )
    elif kind == "refuse":
        style = (
            "REFUSAL only. Must include clear NO (Hell no / Nope / Denied / Can't). "
            "Optional: say you logged it. One sentence, 10-18 words."
        )
    elif kind == "onscreen":
        style = (
            "SUCCESS: you already put the UI on screen. Snarky done-line. "
            "Do NOT refuse. Do NOT say denied/hell no/can't. One sentence, 8-16 words."
        )
    else:
        style = (
            "SUCCESS: you are executing his command now. Snarky ack that you do the work. "
            "Do NOT refuse. Do NOT say denied/hell no/can't/logged as failure. "
            "One sentence, 8-16 words."
        )

    system = (
        f"You are the snarky starship Computer for {CAPTAIN}. "
        "Output exactly ONE sentence. No quotes. No lists. No JSON. "
        "Dry humor. Never say you are an AI language model."
    )
    user = f"{style}\nContext: {prompt}\n{avoid}Sentence:"

    payload_base = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "repeat_penalty": 1.45,
            "num_predict": 40,
        },
    }

    try:
        for attempt in range(3):
            payload = json.loads(json.dumps(payload_base))
            payload["options"]["temperature"] = min(1.25, temperature + attempt * 0.12)
            # nudge uniqueness
            payload["messages"][1]["content"] = user + f"\n(variant seed {random.randint(1000,9999)})"
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            content = (data.get("message") or {}).get("content") or ""
            line = _clean_line(content)
            if not _quality_ok(line, kind):
                continue
            if any(_too_similar(line, prev) for prev in recent):
                continue
            _remember(line, kind)
            return line
    except Exception:
        return None
    return None


def _fallback(seq, kind: str, **fmt) -> str:
    recent = [x.lower() for x in _recent_lines(12)]
    options = list(seq)
    random.shuffle(options)
    for s in options:
        try:
            line = s.format(cap=CAPTAIN, **fmt)
        except Exception:
            line = s
        if not any(_too_similar(line, r) for r in recent):
            _remember(line, kind + "_fb")
            return line
    line = random.choice(seq)
    try:
        line = line.format(cap=CAPTAIN, **fmt)
    except Exception:
        pass
    _remember(line, kind + "_fb")
    return line


def greet() -> dict:
    line = _llm_line(
        "Captain Chad just opened the Computer panel.",
        kind="greet",
        temperature=1.1,
    )
    source = "llm" if line else "fallback"
    if not line:
        line = _fallback(_FALLBACK_GREET, "greet")
    return {
        "speech": line,
        "tool_calls": [],
        "kind": "greet",
        "address": CAPTAIN,
        "speech_source": source,
    }


def ack(kind: str = "ok", transcript: str = "", tools: list | None = None) -> str:
    tools = tools or []
    bits = []
    for t in tools[:3]:
        n = t.get("name") or "tool"
        args = t.get("arguments") or {}
        if n == "open_terminal" and args.get("agent"):
            bits.append(f"show agent {args.get('agent')}")
        elif n == "open_view" and args.get("view"):
            bits.append(f"open {args.get('view')} view")
        elif n == "focus_agent" and args.get("agent"):
            bits.append(f"focus {args.get('agent')}")
        elif n == "set_filter" and args.get("filter"):
            bits.append(f"filter {args.get('filter')}")
        elif n == "list_fleet":
            bits.append("list fleet")
        elif n == "report_status":
            bits.append("report status")
        else:
            bits.append(n)
    summary = ", ".join(bits) if bits else "dashboard action"
    if kind == "onscreen":
        line = _llm_line(
            f"Captain said {transcript!r}. You put something on screen ({summary}).",
            kind="onscreen",
            temperature=1.05,
        )
        return line or _fallback(_FALLBACK_ONSCREEN, "onscreen")
    line = _llm_line(
        f"Captain said {transcript!r}. You are doing: {summary}.",
        kind="ack",
        temperature=1.05,
    )
    return line or _fallback(_FALLBACK_ACK, "ack")


def refuse(transcript: str, reason: str = "unknown_command") -> dict:
    speech = _llm_line(
        f"Captain said {transcript!r}. Refuse it. Reason code: {reason}.",
        kind="refuse",
        temperature=1.15,
    )
    if not speech:
        speech = _fallback(_FALLBACK_REFUSE, "refuse")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch_ms": int(time.time() * 1000),
        "transcript": transcript,
        "reason": reason,
        "speech": speech,
    }
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(REFUSAL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {
        "speech": speech,
        "tool_calls": [],
        "refused": True,
        "refusal_reason": reason,
        "refusal_log": str(REFUSAL_LOG),
    }


def headless_note(display_name: str) -> str:
    line = _llm_line(
        f"Captain wanted {display_name}'s terminal but it's headless — no pane. "
        "You focused collab instead.",
        kind="ack",
        temperature=1.0,
    )
    return line or (
        f"{display_name} is headless — no live terminal. "
        f"I focused collab instead, {CAPTAIN}."
    )


def spice(result: dict, transcript: str = "") -> dict:
    if not result:
        return refuse(transcript or "", "empty_result")

    calls = result.get("tool_calls") or []
    if result.get("refused"):
        return result

    if not calls:
        return refuse(
            transcript or result.get("normalized") or "",
            result.get("reason") or "no_tools",
        )

    names = [c.get("name") for c in calls]
    if "on_screen" in names or (
        "open_view" in names
        and not any(n in ("list_fleet", "report_status") for n in names)
    ):
        result["speech"] = ack("onscreen", transcript=transcript, tools=calls)
    else:
        result["speech"] = ack("ok", transcript=transcript, tools=calls)
    result["refused"] = False
    result["speech_source"] = "llm"
    return result
