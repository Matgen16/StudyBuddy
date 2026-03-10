# -*- coding: utf-8 -*-
"""
Class Notes & Schedule Server
==============================
Receives WAV recordings, transcribes with Whisper, then uses Ollama (local LLM)
to extract structured class notes, assignments, schedule items, key terms, and
action items.
"""

import re as _re
import json
import uuid
import datetime
import threading
import pathlib
import wave
import contextlib

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import whisper
import requests as _requests

# -- Config -------------------------------------------------------------
BASE_DIR    = pathlib.Path(__file__).parent.resolve()
UPLOAD_DIR  = BASE_DIR / "uploads"
DATA_FILE   = BASE_DIR / "data.json"
WHISPER_MODEL = "base"

OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TIMEOUT = 180

UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(BASE_DIR))
CORS(app)

# -- Thread-safe in-memory store -----------------------------------------
_lock = threading.Lock()

def _load_data() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"recordings": [], "schedule": [], "notes": [], "assignments": []}

def _save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# -- Whisper lazy load ---------------------------------------------------
_whisper_model = None
_whisper_lock  = threading.Lock()

def get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            print(f"[Whisper] Loading '{WHISPER_MODEL}' model...")
            _whisper_model = whisper.load_model(WHISPER_MODEL, device="cpu")
            print("[Whisper] Model loaded.")
    return _whisper_model

# -- Ollama helpers -----------------------------------------------------
OLLAMA_BASE = "http://localhost:11434"

def ollama_is_running() -> bool:
    try:
        r = _requests.get(f"{OLLAMA_BASE}/", timeout=3)
        return r.status_code < 500
    except Exception:
        return False

def ollama_list_models() -> list:
    try:
        r = _requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        print(f"[Ollama] Could not list models: {e}")
        return []

def ollama_check_model():
    models = ollama_list_models()
    if not models:
        print("[Ollama] WARNING: no models found or Ollama not running.")
        print(f"         Run:  ollama pull {OLLAMA_MODEL}")
        return
    base = OLLAMA_MODEL.split(":")[0].lower()
    matched = [m for m in models if m.split(":")[0].lower() == base]
    if matched:
        print(f"[Ollama] Model '{matched[0]}' is available OK")
    else:
        print(f"[Ollama] WARNING: model '{OLLAMA_MODEL}' not found!")
        print(f"         Available models: {', '.join(models)}")
        print(f"         Run:  ollama pull {OLLAMA_MODEL}")
        print(f"         Or change OLLAMA_MODEL in server.py to one of the above.")

def _ollama_stream(url: str, payload: dict) -> str:
    payload = dict(payload, stream=True)
    try:
        r = _requests.post(url, json=payload, stream=True, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
        chunks = []
        for line in r.iter_lines():
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "/chat" in url:
                    delta = obj.get("message", {}).get("content", "")
                else:
                    delta = obj.get("response", "")
                if delta:
                    chunks.append(delta)
                if obj.get("done"):
                    break
            except json.JSONDecodeError:
                continue
        result = "".join(chunks).strip()
        print(f"[Ollama] Streaming got {len(result)} chars")
        return result
    except Exception as e:
        print(f"[Ollama] Streaming error: {e}")
        return ""

def ollama_chat(user_message: str, system_message: str = "") -> str:
    """
    Generate a plain-text response from Ollama.

    KEY FIX: "format": "json" is intentionally NOT set anywhere in this function.
    Our prompts ask for plain-text / custom line formats (HEADING:/DETAIL: etc).
    Setting format:json forces the model to emit bare {} or minimal JSON,
    completely ignoring the prompt structure and producing empty results.
    """
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_message})

    chat_url = f"{OLLAMA_BASE}/api/chat"
    gen_url  = f"{OLLAMA_BASE}/api/generate"

    # -- Attempt 1: /api/chat non-streaming -----------------------------------
    chat_payload = {
        "model":    OLLAMA_MODEL,
        "messages": messages,
        "stream":   False,
        # NOTE: no "format": "json" -- we want plain text output per prompt instructions
        "options":  {"temperature": 0.1, "num_predict": 1024},
    }
    try:
        print(f"[Ollama] Trying /api/chat (timeout={OLLAMA_TIMEOUT}s)...")
        r = _requests.post(chat_url, json=chat_payload, timeout=OLLAMA_TIMEOUT)

        if r.status_code == 500:
            try:
                err = r.json().get("error", r.text[:200])
            except Exception:
                err = r.text[:200]
            print(f"[Ollama] /api/chat 500 error: {err}")
            print("[Ollama] Falling back to /api/generate...")
            raise ValueError("500 -- try generate")

        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "").strip()
        if content:
            print(f"[Ollama] /api/chat OK -- {len(content)} chars")
            return content
        print("[Ollama] /api/chat returned empty -- trying /api/generate...")

    except ValueError:
        pass
    except _requests.exceptions.Timeout:
        print(f"[Ollama] /api/chat timed out after {OLLAMA_TIMEOUT}s")
        return ""
    except Exception as e:
        print(f"[Ollama] /api/chat error: {e} -- trying /api/generate...")

    # -- Attempt 2: /api/generate non-streaming --------------------------------
    combined = (f"{system_message}\n\n{user_message}").strip() if system_message else user_message
    gen_payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  combined,
        "stream":  False,
        # NOTE: no "format": "json"
        "options": {"temperature": 0.1, "num_predict": 1024},
    }
    try:
        print("[Ollama] Trying /api/generate...")
        r = _requests.post(gen_url, json=gen_payload, timeout=OLLAMA_TIMEOUT)
        if r.status_code == 500:
            try:
                err = r.json().get("error", r.text[:200])
            except Exception:
                err = r.text[:200]
            print(f"[Ollama] /api/generate 500: {err}")
            return ""
        r.raise_for_status()
        content = r.json().get("response", "").strip()
        if content:
            print(f"[Ollama] /api/generate OK -- {len(content)} chars")
            return content
        print("[Ollama] /api/generate empty -- trying streaming...")
    except _requests.exceptions.Timeout:
        print(f"[Ollama] /api/generate timed out after {OLLAMA_TIMEOUT}s")
        return ""
    except Exception as e:
        print(f"[Ollama] /api/generate error: {e}")

    # -- Attempt 3: /api/generate streaming ------------------------------------
    return _ollama_stream(gen_url, gen_payload)


# -- Core AI pipeline ----------------------------------------------------
#
# llama3.2:3b context window: ~128k tokens but only ~4GB RAM available.
# Strategy:
#   Pass 1 -- Chunk & summarise (plain prose)
#   Pass 2 -- Condense if needed
#   Pass 3 -- Extract via plain-text prompts (HEADING:/DETAIL: format, etc.)

CHUNK_SIZE     = 6_000   # reduced slightly to stay well within 3b context
CONDENSE_MAX   = 3_000
SUMMARY_TARGET = 120
SHORT_CUTOFF   = 400

# All prompts explicitly ask for plain text -- never JSON.
# This is critical because format:json has been removed from ollama_chat.

SUMMARY_PROMPT = (
    "Summarise this spoken lecture transcript in {target} words or fewer.\n\n"
    "TWO-PART TASK:\n"
    "Part 1 -- CONTENT SUMMARY: Write plain English sentences summarising "
    "the main topics and concepts explained.\n"
    "Part 2 -- ASSIGNMENTS (copy word-for-word): "
    "At the end, reproduce verbatim every sentence in which the teacher "
    "mentions homework, an assignment, a quiz, a test, a worksheet, "
    "a deadline, something due, or anything students must do or turn in. "
    "If there are none, write nothing extra.\n\n"
    "STRICT OUTPUT RULES -- breaking any of these is wrong:\n"
    "1. Plain English sentences only. No bullet points, no headers, no JSON.\n"
    "2. NO LaTeX at all. No backslashes. No \\( \\) or \\[ \\] or \\frac or ^ or _.\n"
    "   Write math in plain words: 'x squared', 'one third', 'f of g of x'.\n"
    "3. No markdown: no ** or * or __ or # characters.\n\n"
    "TRANSCRIPT:\n{text}\n\n"
    "SUMMARY:"
)

CONDENSE_PROMPT = (
    "Combine the lecture section summaries below into one coherent set of notes "
    "in 250 words or fewer. Keep all key topics, assignments, and dates.\n\n"
    "STRICT OUTPUT RULES:\n"
    "1. Plain English sentences only. No bullet points, no headers, no JSON.\n"
    "2. NO LaTeX. No backslashes. No \\( \\) \\frac ^ _. "
    "Write math in plain words.\n"
    "3. No markdown: no ** or * or __ or # characters.\n\n"
    "SECTIONS:\n{summaries}\n\n"
    "COMBINED NOTES:"
)

EXTRACT_META_PROMPT = (
    "Read the lecture notes below. Answer every labelled question.\n"
    "Write each answer on its own line starting with the EXACT label shown "
    "(colon included). Do not add any other lines, intro, or explanation.\n\n"
    "STRICT OUTPUT RULES:\n"
    "1. Plain English only.\n"
    "2. NO LaTeX. No backslashes. No \\( \\) \\frac ^ _. "
    "Spell math out: 'x squared', 'one third', 'f of g of x'.\n"
    "3. No markdown: no ** * __ # characters.\n\n"
    "NOTES:\n{notes}\n\n"
    "TITLE: 5 words max describing the lecture topic\n"
    "SUBJECT: one word only, e.g. Math or Physics or History or Biology or General\n"
    "SUMMARY: 2 to 3 plain-English sentences summarising the lecture\n"
    "KEY TERMS: comma-separated list of important terms from the lecture\n"
    "ACTION ITEMS: comma-separated list of things students must do, or NONE\n\n"
    "Your answers (start with TITLE:):"
)

EXTRACT_NOTES_PROMPT = (
    "Read the lecture notes below. List up to 6 main topics that were actually "
    "covered in this specific lecture.\n\n"
    "For each topic output exactly two lines, no other lines:\n"
    "HEADING: the topic name in plain English\n"
    "DETAIL: one plain-English sentence explaining what was taught\n\n"
    "STRICT FORMATTING RULES:\n"
    "- Plain English only. No LaTeX, no math symbols, no backslashes.\n"
    "  Spell math out in words: write 'x squared' not 'x^2', "
    "'one third' not '1/3' or '\\frac{1}{3}'.\n"
    "- No markdown, no bullet points, no numbering, no JSON.\n"
    "- You MUST only write about topics found in the NOTES section below. "
    "Do NOT invent topics.\n\n"
    "SAMPLE FORMAT (these lines are fake placeholders -- "
    "DO NOT copy them into your answer):\n"
    "HEADING: [topic from actual notes goes here]\n"
    "DETAIL: [one sentence about that topic goes here]\n"
    "END SAMPLE\n\n"
    "NOTES:\n{notes}\n\n"
    "Your answer -- HEADING/DETAIL pairs from the NOTES above only:"
)

# Assignment extraction prompt.
# Design decisions:
#   - Explicit keyword list so the 3b model knows exactly what to scan for.
#   - Full TYPE vocabulary with detection hints so the model picks correctly.
#   - Example block clearly labelled and fenced so it is never copied into output.
#   - "Start with the first ITEM: line" kills preamble chatter.
#   - Bans LaTeX/markdown in descriptions.
EXTRACT_ASSIGN_PROMPT = (
    "You are a student assistant. Scan the transcript below for EVERY task, "
    "deadline, or graded item the teacher mentions.\n\n"
    "WHAT TO LOOK FOR -- scan for these words and phrases:\n"
    "  homework, assignment, due, turn in, submit, hand in, bring in,\n"
    "  quiz, test, exam, midterm, final, pop quiz,\n"
    "  worksheet, handout, problem set, practice problems,\n"
    "  lab, lab report, experiment,\n"
    "  essay, paper, report, write-up, writing assignment,\n"
    "  project, presentation, poster, slides,\n"
    "  reading, read chapter, read pages, textbook pages,\n"
    "  study, review, practice, complete, finish, do.\n\n"
    "HOW TO DETECT THE TYPE:\n"
    "  worksheet  -- teacher says worksheet, handout, or problem set on paper\n"
    "  quiz       -- short graded test, pop quiz\n"
    "  test       -- larger graded assessment, unit test, chapter test\n"
    "  exam       -- exam, midterm, final exam\n"
    "  lab        -- lab, experiment, lab report\n"
    "  essay      -- essay, paper, written report, write-up\n"
    "  project    -- project, presentation, poster, slides\n"
    "  reading    -- read chapter, read pages, textbook reading\n"
    "  homework   -- general homework or practice problems\n"
    "  other      -- anything that does not fit the above\n\n"
    "Today is {today}. Convert relative dates like 'Friday', 'next Monday', "
    "'tomorrow' to YYYY-MM-DD. Use unspecified if no date is given.\n\n"
    "OUTPUT RULES:\n"
    "- Output one line per item using EXACTLY this format:\n"
    "  ITEM: <description> | DUE: <YYYY-MM-DD or unspecified> | "
    "TYPE: <type from the list above> | PRIORITY: <high or medium or low>\n"
    "- Start your answer with the first ITEM: line. No intro sentence, no summary.\n"
    "- Plain English descriptions only. No LaTeX, no backslashes, no markdown.\n"
    "- If there are truly zero tasks or deadlines, output exactly: NONE\n\n"
    "IMPORTANT: The lines below are the FORMAT TEMPLATE only -- "
    "they show the structure, not real content. "
    "Do NOT copy them into your answer.\n"
    "  ITEM: [description of task from transcript] | DUE: [date or unspecified] | "
    "TYPE: [type] | PRIORITY: [priority]\n"
    "END TEMPLATE\n\n"
    "TRANSCRIPT:\n{transcript}\n\n"
    "Your answer -- ITEM lines extracted from the TRANSCRIPT above only:"
)


def _chunk_transcript(text: str):
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks, step = [], CHUNK_SIZE - 200
    for i in range(0, len(text), step):
        chunks.append(text[i : i + CHUNK_SIZE])
    return chunks


def _call(prompt: str, label: str) -> str:
    print(f"[AI] {label} ({len(prompt)} char prompt)...")
    result = ollama_chat(prompt)
    print(f"[AI] {label} -> {len(result)} chars")
    return result


def _summarise_chunks(chunks: list) -> list:
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        prompt = (SUMMARY_PROMPT
                  .replace("{target}", str(SUMMARY_TARGET))
                  .replace("{text}", chunk))
        s = _call(prompt, f"summarise chunk {i}/{len(chunks)}")
        if s:
            summaries.append(s)
    return summaries


def _condense(summaries: list) -> str:
    combined = "\n\n".join(f"[Section {i+1}]\n{s}" for i, s in enumerate(summaries))
    if len(combined) <= CONDENSE_MAX:
        return combined
    prompt = CONDENSE_PROMPT.replace("{summaries}", combined)
    result = _call(prompt, "condense summaries")
    return result or combined[:CONDENSE_MAX]


def _parse_meta(raw: str) -> dict:
    result = {"title": "", "subject": "General", "summary": "", "key_terms": [], "action_items": []}
    if not raw:
        return result
    lines = raw.strip().splitlines()
    current = None
    buf = []

    def flush():
        if current and buf:
            val = " ".join(buf).strip()
            if current == "title":
                result["title"] = val
            elif current == "subject":
                result["subject"] = val.split()[0] if val else "General"
            elif current == "summary":
                result["summary"] = val
            elif current == "terms":
                result["key_terms"] = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
            elif current == "actions":
                items = [t.strip() for t in val.split(",") if t.strip() and t.strip().lower() != "none"]
                result["action_items"] = items

    for line in lines:
        s = line.strip()
        if not s:
            continue
        su = s.upper()
        if su.startswith("TITLE:") or su.startswith("TITLE "):
            flush(); current = "title"
            buf = [s.split(":", 1)[-1].strip() if ":" in s else ""]
        elif su.startswith("SUBJECT:") or su.startswith("SUBJECT "):
            flush(); current = "subject"
            buf = [s.split(":", 1)[-1].strip() if ":" in s else ""]
        elif su.startswith("SUMMARY:") or su.startswith("SUMMARY "):
            flush(); current = "summary"
            buf = [s.split(":", 1)[-1].strip() if ":" in s else ""]
        elif su.startswith("KEY TERM"):
            flush(); current = "terms"
            buf = [s.split(":", 1)[-1].strip() if ":" in s else ""]
        elif su.startswith("ACTION"):
            flush(); current = "actions"
            buf = [s.split(":", 1)[-1].strip() if ":" in s else ""]
        elif current and s:
            buf.append(s)
    flush()
    return result


def _parse_notes_sections(raw: str) -> list:
    """
    Parse HEADING:/DETAIL: pairs into note blocks.
    Also handles JSON-style output as a fallback in case the model
    ignores the plain-text instruction (shouldn't happen now that
    format:json is removed, but kept as safety net).
    """
    notes = []
    if not raw or raw.strip() in ("{}", ""):
        return notes

    heading, detail = "", ""
    found_structured = False

    for line in raw.strip().splitlines():
        s = line.strip()
        su = s.upper()
        if su.startswith("HEADING:"):
            if heading:
                notes.append({"heading": heading, "content": detail or "—"})
            heading = s[8:].strip()
            detail  = ""
            found_structured = True
        elif su.startswith("DETAIL:"):
            detail = s[7:].strip()
            found_structured = True
        elif heading and s and not su.startswith("HEADING") and not su.startswith("DETAIL"):
            # continuation of detail text
            detail = (detail + " " + s).strip() if detail else s
    if heading:
        notes.append({"heading": heading, "content": detail or "—"})

    # JSON fallback (safety net)
    if not found_structured and raw.strip().startswith("{"):
        try:
            obj = json.loads(raw.strip())
            for k, v in obj.items():
                notes.append({"heading": str(k), "content": str(v)})
        except Exception:
            pass

    return notes[:6]


# Types that belong on the schedule (timed events) vs the assignment list
_SCHEDULE_TYPES = {"quiz", "test", "exam", "midterm", "final"}

# Full set of recognised assignment types (everything not in _SCHEDULE_TYPES)
_ASSIGNMENT_TYPES = {
    "homework", "worksheet", "lab", "essay", "paper",
    "project", "presentation", "reading", "report", "other",
}

# Keyword → TYPE mapping for fuzzy fallback when the model omits TYPE
_TYPE_KEYWORDS = [
    ({"worksheet"},                          "worksheet"),
    ({"lab", "laboratory", "lab report"},    "lab"),
    ({"essay"},                              "essay"),
    ({"paper", "write-up", "writeup"},       "paper"),
    ({"presentation", "present", "poster"},  "presentation"),
    ({"project"},                            "project"),
    ({"reading", "read", "chapter", "pages", "textbook"}, "reading"),
    ({"quiz"},                               "quiz"),
    ({"test"},                               "test"),
    ({"exam", "midterm", "final"},           "exam"),
    ({"homework", "hw", "problem set", "pset"}, "homework"),
]


def _infer_type(text: str) -> str:
    """Guess assignment type from the task description when model omits it."""
    lower = text.lower()
    for keywords, t in _TYPE_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return t
    return "homework"


def _parse_assignments(raw: str, today: str) -> tuple:
    assignments, schedule_items = [], []
    if not raw:
        return assignments, schedule_items

    # Entire response is just NONE
    if raw.strip().upper() == "NONE":
        print("[AI] Assignment extraction returned NONE")
        return assignments, schedule_items

    for line in raw.strip().splitlines():
        s = line.strip()

        # Skip blank lines and bare NONE
        if not s or s.upper() == "NONE":
            continue

        # Skip obvious preamble lines the model sometimes adds
        # (e.g. "Here are the assignments I found:")
        su = s.upper()
        if not su.startswith("ITEM:") and "|" not in s:
            print(f"[AI] Skipping non-ITEM line: {s[:80]}")
            continue

        # Strip ITEM: prefix — we want just the payload
        if su.startswith("ITEM:"):
            s = s[5:].strip()

        # Split on pipes into segments, then build a key→value dict
        # from any segment that contains a colon.
        segments = [seg.strip() for seg in s.split("|")]
        parts: dict = {}
        for seg in segments:
            if ":" in seg:
                k, v = seg.split(":", 1)
                parts[k.strip().upper()] = v.strip()

        # The task description is ALWAYS the first segment (before the
        # first pipe), now that ITEM: has already been stripped.
        name = segments[0].strip() if segments else ""

        # Clean junk from name (model sometimes leaves a stray key prefix)
        for junk in ("ITEM:", "NAME:", "TASK:"):
            if name.upper().startswith(junk):
                name = name[len(junk):].strip()

        due      = parts.get("DUE", "unspecified").strip()
        itype    = parts.get("TYPE", "").strip().lower()
        priority = parts.get("PRIORITY", "medium").strip().lower()

        # Validate / sanitise
        if not name or name.lower() in ("none", "n/a", ""):
            print(f"[AI] Skipping empty assignment name from line: {s[:80]}")
            continue
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        # If the model gave an unrecognised type, infer from description
        all_known = _SCHEDULE_TYPES | _ASSIGNMENT_TYPES
        if itype not in all_known:
            itype = _infer_type(name)

        print(f"[AI] Parsed assignment: name={name!r} due={due!r} type={itype!r} pri={priority!r}")

        if itype in _SCHEDULE_TYPES:
            schedule_items.append({
                "event": name,
                "date":  due,
                "time":  "unspecified",
                "type":  itype,
            })
        else:
            assignments.append({
                "task":     name,
                "due_date": due,
                "priority": priority,
                "details":  "",
                "type":     itype,
            })

    return assignments, schedule_items


# ---------------------------------------------------------------------------
# LaTeX / markdown sanitiser
# ---------------------------------------------------------------------------
# The 3b model sometimes ignores "no LaTeX" instructions.  We scrub the most
# common patterns from every text field before saving so the frontend always
# receives clean human-readable strings.

_LATEX_SUBS = [
    # Inline math delimiters  \( ... \)  and  \[ ... \]
    (_re.compile(r"\\\((.+?)\\\)", _re.S),  r"\1"),
    (_re.compile(r"\\\[(.+?)\\\]", _re.S),  r"\1"),
    # Dollar-sign math  $...$
    (_re.compile(r"\$\$(.+?)\$\$", _re.S),  r"\1"),
    (_re.compile(r"\$(.+?)\$"),              r"\1"),
    # \frac{a}{b}  →  a/b
    (_re.compile(r"\\frac\{([^}]+)\}\{([^}]+)\}"), r"\1/\2"),
    # \text{...}  →  ...
    (_re.compile(r"\\text\{([^}]+)\}"),      r"\1"),
    # Generic \command{...}  →  ...
    (_re.compile(r"\\[a-zA-Z]+\{([^}]*)\}"), r"\1"),
    # Lone backslash-commands like \cdot \times \to \rightarrow etc.
    (_re.compile(r"\\[a-zA-Z]+"),            " "),
    # Superscript ^ and subscript _  (only when inside likely math context)
    (_re.compile(r"\^(\{[^}]+\}|\w)"),       r" to the power of \1"),
    (_re.compile(r"_(\{[^}]+\}|\w)"),        r" sub \1"),
    # Remove curly braces left over
    (_re.compile(r"[{}]"),                   ""),
    # Collapse multiple spaces
    (_re.compile(r"  +"),                    " "),
]

# Markdown bold/italic we also strip from prose fields
_MD_SUBS = [
    (_re.compile(r"\*\*(.+?)\*\*"), r"\1"),   # **bold**
    (_re.compile(r"\*(.+?)\*"),     r"\1"),   # *italic*
    (_re.compile(r"__(.+?)__"),     r"\1"),   # __bold__
    (_re.compile(r"_(.+?)_"),       r"\1"),   # _italic_
    (_re.compile(r"`(.+?)`"),       r"\1"),   # `code`
]


def _clean(text: str) -> str:
    """Strip LaTeX and markdown formatting from a plain-text string."""
    if not text:
        return text
    for pattern, repl in _LATEX_SUBS:
        text = pattern.sub(repl, text)
    for pattern, repl in _MD_SUBS:
        text = pattern.sub(repl, text)
    return text.strip()


def _clean_result(result: dict) -> dict:
    """Apply _clean() to every human-visible text field in an extracted result."""
    result["title"]   = _clean(result.get("title",   ""))
    result["subject"] = _clean(result.get("subject", ""))
    result["summary"] = _clean(result.get("summary", ""))
    result["key_terms"]    = [_clean(t) for t in result.get("key_terms",    [])]
    result["action_items"] = [_clean(t) for t in result.get("action_items", [])]
    result["notes"] = [
        {"heading": _clean(n.get("heading", "")),
         "content": _clean(n.get("content", ""))}
        for n in result.get("notes", [])
    ]
    for a in result.get("assignments", []):
        a["task"]    = _clean(a.get("task",    ""))
        a["details"] = _clean(a.get("details", ""))
    for s in result.get("schedule_items", []):
        s["event"] = _clean(s.get("event", ""))
    return result


# ---------------------------------------------------------------------------

def _extract_structure(brief: str, original_transcript: str, today: str) -> dict:
    """
    Pass 3: multi-prompt plain-text extraction.

    notes/meta are extracted from `brief` (the condensed summary).
    Assignments are extracted from `original_transcript` so that
    casually-mentioned deadlines are never lost in summarisation.
    All text fields are sanitised with _clean_result() before returning.
    """
    # 3a: meta fields
    meta_raw = _call(EXTRACT_META_PROMPT.replace("{notes}", brief), "extract meta")
    print(f"[AI] Meta raw:\n{meta_raw[:400]}\n---")
    result = _parse_meta(meta_raw)

    # 3b: note sections
    notes_raw = _call(EXTRACT_NOTES_PROMPT.replace("{notes}", brief), "extract note sections")
    print(f"[AI] Notes raw:\n{notes_raw[:400]}\n---")
    result["notes"] = _parse_notes_sections(notes_raw)

    # 3c: assignments / schedule items -- run against the FULL transcript
    # (capped to CHUNK_SIZE to avoid timeouts on very long recordings)
    transcript_for_assign = original_transcript[:CHUNK_SIZE]
    assign_raw = _call(
        EXTRACT_ASSIGN_PROMPT
            .replace("{transcript}", transcript_for_assign)
            .replace("{today}", today),
        "extract assignments"
    )
    print(f"[AI] Assignments raw:\n{assign_raw[:400]}\n---")
    assignments, schedule_items = _parse_assignments(assign_raw, today)
    result["assignments"]    = assignments
    result["schedule_items"] = schedule_items

    # Strip any LaTeX / markdown that slipped through despite prompt instructions
    result = _clean_result(result)

    print(f"[AI] Extracted: title={result.get('title')!r}, "
          f"{len(result['notes'])} notes, {len(assignments)} assignments, "
          f"{len(result.get('key_terms', []))} terms")
    return result


# ── Duplicate assignment guard ────────────────────────────────────────────────
# Before appending new assignments/schedule items extracted from a recording,
# remove any previously saved entries for the same recording_id.  This prevents
# duplicates if a recording is ever reprocessed (e.g. after a partial failure).
def _save_result(result: dict, text: str, recording_id: str, today: str):
    with _lock:
        data = _load_data()

        # Update the recording record
        for rec in data["recordings"]:
            if rec["id"] == recording_id:
                rec.update({
                    "transcript":   text,
                    "title":        result.get("title",        rec.get("title")),
                    "subject":      result.get("subject",      rec.get("subject")),
                    "summary":      result.get("summary",      ""),
                    "notes":        result.get("notes",        []),
                    "key_terms":    result.get("key_terms",    []),
                    "action_items": result.get("action_items", []),
                    "status":       "done",
                })
                break

        # Remove any stale assignments / schedule items from a previous run
        # for this recording before inserting the fresh ones.
        data["assignments"] = [
            a for a in data["assignments"] if a.get("recording_id") != recording_id
        ]
        data["schedule"] = [
            s for s in data["schedule"] if s.get("recording_id") != recording_id
        ]

        for a in result.get("assignments", []):
            a.update({"id": str(uuid.uuid4()), "recording_id": recording_id,
                      "created": today, "completed": False})
            data["assignments"].append(a)
        for s in result.get("schedule_items", []):
            s.update({"id": str(uuid.uuid4()), "recording_id": recording_id})
            data["schedule"].append(s)

        _save_data(data)
    print(f"[AI] Done: '{result.get('title')}' -- "
          f"{len(result.get('notes', []))} note sections, "
          f"{len(result.get('key_terms', []))} key terms, "
          f"{len(result.get('assignments', []))} assignments")


def process_recording(transcript: str, recording_id: str):
    try:
        _process_recording_inner(transcript, recording_id)
    except Exception as e:
        import traceback
        print(f"[AI] UNHANDLED ERROR in process_recording: {e}")
        traceback.print_exc()
        with _lock:
            data = _load_data()
            for rec in data["recordings"]:
                if rec["id"] == recording_id:
                    rec["status"]  = "done"
                    rec["summary"] = f"Processing error: {e}"
                    break
            _save_data(data)


def _process_recording_inner(transcript: str, recording_id: str):
    text  = transcript.strip() or "[No speech detected]"
    today = datetime.date.today().isoformat()

    if not ollama_is_running():
        print("[Ollama] Not reachable -- skipping AI analysis")
        with _lock:
            data = _load_data()
            for rec in data["recordings"]:
                if rec["id"] == recording_id:
                    rec["transcript"] = text
                    rec["status"]     = "done"
                    rec["summary"]    = "AI analysis unavailable (Ollama not running)"
                    break
            _save_data(data)
        return

    chars = len(text)
    print(f"[AI] Processing recording {recording_id} ({chars:,} chars)...")

    if chars <= SHORT_CUTOFF:
        print("[AI] Tiny transcript -- extracting structure directly")
        result = _extract_structure(text, text, today)
        if not result.get("title"):   result["title"]   = f"Recording {recording_id[:8]}"
        if not result.get("subject"): result["subject"] = "General"
        if not result.get("summary"): result["summary"] = _clean(text[:300])
        _save_result(result, text, recording_id, today)
        return

    if chars <= CHUNK_SIZE:
        print("[AI] Medium transcript -- single summary then extract")
        prompt = (SUMMARY_PROMPT
                  .replace("{target}", str(SUMMARY_TARGET * 2))
                  .replace("{text}", text))
        brief = _call(prompt, "summarise full transcript")
        if not brief:
            brief = text[:2000]
        # FIX: pass original `text` as second arg so assignments are pulled
        # from the full transcript, not just the condensed brief.
        result = _extract_structure(brief, text, today)
        if not result.get("title"):   result["title"]   = f"Recording {recording_id[:8]}"
        if not result.get("subject"): result["subject"] = "General"
        if not result.get("summary"): result["summary"] = _clean(brief[:300])
        _save_result(result, text, recording_id, today)
        return

    # Long transcript: chunk -> summarise -> condense -> extract
    chunks    = _chunk_transcript(text)
    print(f"[AI] {len(chunks)} chunk(s) of up to {CHUNK_SIZE:,} chars each")
    summaries = _summarise_chunks(chunks)

    if not summaries:
        print("[AI] All summaries empty -- saving raw transcript only")
        with _lock:
            data = _load_data()
            for rec in data["recordings"]:
                if rec["id"] == recording_id:
                    rec.update({"transcript": text, "status": "done",
                                "title": f"Recording {recording_id[:8]}",
                                "subject": "General", "summary": text[:300] + "..."})
                    break
            _save_data(data)
        return

    brief = _condense(summaries)
    print(f"[AI] Brief after condensing: {len(brief):,} chars")
    # FIX: pass original `text` as second arg so assignments are pulled
    # from the full transcript, not just the condensed brief.
    result = _extract_structure(brief, text, today)

    if not result.get("title"):   result["title"]   = f"Recording {recording_id[:8]}"
    if not result.get("subject"): result["subject"] = "General"
    if not result.get("summary"): result["summary"] = _clean(summaries[0][:300]) + "..."

    _save_result(result, text, recording_id, today)


# -- Routes -------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file"}), 400

    audio_file    = request.files["audio"]
    rec_id        = str(uuid.uuid4())
    filename      = f"{rec_id}.wav"
    wav_path      = UPLOAD_DIR / filename
    audio_file.save(str(wav_path))

    timestamp     = datetime.datetime.now()
    title         = timestamp.strftime("Recording %b %-d, %I:%M %p")

    duration_secs = 0
    try:
        with contextlib.closing(wave.open(str(wav_path))) as wf:
            duration_secs = wf.getnframes() / wf.getframerate()
    except Exception:
        pass

    record = {
        "id":           rec_id,
        "file":         filename,
        "title":        title,
        "subject":      "Processing...",
        "summary":      "",
        "notes":        [],
        "key_terms":    [],
        "action_items": [],
        "transcript":   "",
        "duration":     round(duration_secs),
        "created":      timestamp.isoformat(),
        "status":       "transcribing",
    }

    with _lock:
        data = _load_data()
        data["recordings"].insert(0, record)
        _save_data(data)

    def _bg():
        transcript = ""
        try:
            print(f"[Whisper] Transcribing {filename}...")
            model      = get_whisper()
            result     = model.transcribe(str(wav_path), language="en", fp16=False)
            transcript = result.get("text", "").strip()
            print(f"[Whisper] Done -- {len(transcript)} chars.")
        except Exception as e:
            print(f"[Whisper] Error: {e}")
            transcript = "[No speech detected]"

        with _lock:
            data = _load_data()
            for rec in data["recordings"]:
                if rec["id"] == rec_id:
                    rec["transcript"] = transcript
                    rec["status"]     = "analysing"
            _save_data(data)

        process_recording(transcript, rec_id)

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"id": rec_id, "title": title, "status": "queued"}), 200


@app.route("/api/status", methods=["GET"])
def api_status():
    with _lock:
        data = _load_data()
    pending = sum(1 for r in data["recordings"] if r.get("status") not in ("done",))
    return jsonify({
        "ok":          True,
        "recordings":  len(data["recordings"]),
        "assignments": len(data["assignments"]),
        "schedule":    len(data["schedule"]),
        "pending":     pending,
        "whisper":     WHISPER_MODEL,
        "ollama":      OLLAMA_MODEL,
        "ollama_up":   ollama_is_running(),
    })


@app.route("/api/recordings", methods=["GET"])
def get_recordings():
    with _lock:
        data = _load_data()
    return jsonify(data["recordings"])


@app.route("/api/recordings/<rid>", methods=["GET"])
def get_recording(rid):
    with _lock:
        data = _load_data()
    for rec in data["recordings"]:
        if rec["id"] == rid:
            return jsonify(rec)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/recordings/<rid>", methods=["PATCH"])
def patch_recording(rid):
    body = request.get_json(silent=True) or {}
    with _lock:
        data = _load_data()
        for rec in data["recordings"]:
            if rec["id"] == rid:
                if "title" in body:
                    rec["title"] = str(body["title"])[:120]
                if "transcript" in body:
                    rec["transcript"] = str(body["transcript"])
                _save_data(data)
                return jsonify(rec)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/recordings/<rid>", methods=["DELETE"])
def delete_recording(rid):
    with _lock:
        data = _load_data()
        data["recordings"] = [r for r in data["recordings"] if r["id"] != rid]
        # FIX: also clean up orphaned assignments and schedule items
        data["assignments"] = [a for a in data["assignments"] if a.get("recording_id") != rid]
        data["schedule"]    = [s for s in data["schedule"]    if s.get("recording_id") != rid]
        _save_data(data)
    return jsonify({"ok": True})  # FIX: was `jsoniy` (typo → NameError crash)


@app.route("/api/assignments", methods=["GET"])
def get_assignments():
    with _lock:
        data = _load_data()
    return jsonify(data["assignments"])


@app.route("/api/assignments", methods=["POST"])
def create_assignment():
    body = request.get_json(silent=True) or {}
    a = {
        "id":           str(uuid.uuid4()),
        "recording_id": body.get("recording_id", ""),
        "task":         str(body.get("task", "Untitled"))[:200],
        "due_date":     str(body.get("due_date", "unspecified")),
        "priority":     body.get("priority", "medium"),
        "details":      str(body.get("details", "")),
        "type":         body.get("type", "assignment"),
        "created":      datetime.date.today().isoformat(),
        "completed":    False,
    }
    with _lock:
        data = _load_data()
        data["assignments"].append(a)
        _save_data(data)
    return jsonify(a), 201


@app.route("/api/assignments/<aid>", methods=["PATCH"])
def patch_assignment(aid):
    body = request.get_json(silent=True) or {}
    with _lock:
        data = _load_data()
        for a in data["assignments"]:
            if a["id"] == aid:
                for field in ("task", "due_date", "priority", "details", "type", "completed"):
                    if field in body:
                        a[field] = body[field]
                _save_data(data)
                return jsonify(a)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/assignments/<aid>/complete", methods=["POST"])
def complete_assignment(aid):
    with _lock:
        data = _load_data()
        for a in data["assignments"]:
            if a["id"] == aid:
                a["completed"] = not a.get("completed", False)
                _save_data(data)
                return jsonify({"id": aid, "completed": a["completed"]})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/assignments/<aid>", methods=["DELETE"])
def delete_assignment(aid):
    with _lock:
        data = _load_data()
        data["assignments"] = [a for a in data["assignments"] if a["id"] != aid]
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    with _lock:
        data = _load_data()
    return jsonify(data["schedule"])


@app.route("/api/schedule", methods=["POST"])
def create_schedule_item():
    body = request.get_json(silent=True) or {}
    s = {
        "id":    str(uuid.uuid4()),
        "event": str(body.get("event", "Untitled"))[:200],
        "date":  str(body.get("date",  "unspecified")),
        "time":  str(body.get("time",  "unspecified")),
        "type":  body.get("type", "other"),
        "recording_id": body.get("recording_id", ""),
    }
    with _lock:
        data = _load_data()
        data["schedule"].append(s)
        _save_data(data)
    return jsonify(s), 201


@app.route("/api/schedule/<sid>", methods=["PATCH"])
def patch_schedule_item(sid):
    body = request.get_json(silent=True) or {}
    with _lock:
        data = _load_data()
        for s in data["schedule"]:
            if s["id"] == sid:
                for field in ("event", "date", "time", "type"):
                    if field in body:
                        s[field] = body[field]
                _save_data(data)
                return jsonify(s)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/schedule/<sid>", methods=["DELETE"])
def delete_schedule_item(sid):
    with _lock:
        data = _load_data()
        data["schedule"] = [s for s in data["schedule"] if s["id"] != sid]
        _save_data(data)
    return jsonify({"ok": True})


@app.route("/interval_bold_italic.otf")
def serve_font():
    return send_from_directory(str(BASE_DIR), "interval_bold_italic.otf")


# -- Main ---------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  Class Notes & Schedule Server")
    print(f"  Whisper model  : {WHISPER_MODEL}")
    print(f"  Ollama model   : {OLLAMA_MODEL}")
    print(f"  Ollama URL     : {OLLAMA_BASE}")
    print(f"  Upload dir     : {UPLOAD_DIR.resolve()}")
    print(f"  Ollama running : {ollama_is_running()}")
    print("=" * 60)
    ollama_check_model()
    print("  Checklist:")
    print("    ollama serve              (must be running)")
    print(f"   ollama pull {OLLAMA_MODEL:<16}  (model must be pulled)")
    print("    sudo python3 setup_https.py   (first-time HTTPS setup)")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)