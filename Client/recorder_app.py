"""
Voice Recorder
==============
Versioning: MAJOR.MINOR.REVISION
  MAJOR    — complete overhaul or large new feature set
  MINOR    — new small features or notable improvements
  REVISION — bug fixes and small tweaks

*** Update ALL three version variables on every single change. ***

Dependencies (install once):
    pip install vosk pyaudio pillow

Vosk model (download once, place folder next to this script):
    Small English model (~40 MB):
    https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    Extract so the folder is:  vosk-model-small-en-us-0.15/
    Or set VOSK_MODEL_PATH below to any other Vosk model folder.

Icons:
    Place icon PNGs in an `icons/` folder next to this script.
    Theme-aware icons use the suffix "Dark" or "Light" in the filename.
    e.g. MicDark.png, MicLight.png, StopDark.png, StopLight.png, etc.
"""

VERSION_MAJOR    = 1
VERSION_MINOR    = 5
VERSION_REVISION = 23
VERSION_STRING   = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_REVISION}"

APP_NAME   = "Voice Recorder"
DEVELOPERS = ["Matthew Gentle", "Ryan Parker"]
TOS_FILE   = "tos.txt"

VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"
ICONS_DIR       = "Icons"   # folder next to this script containing the PNGs

SAMPLE_RATE  = 16000
CHUNK_SIZE   = 4000
CHANNELS     = 1
AUDIO_FORMAT = None

# ── INMP441 gain / normalization config ───────────────────────────────────────
MIC_GAIN                = 600.0
NOISE_GATE_THRESHOLD    = 0
SOFT_KNEE_THRESHOLD     = 0.65
NORMALIZE_WAV           = True

# ── Server upload config ───────────────────────────────────────────────────────
SERVER_URL          = "http://18.219.159.74:5000"
SERVER_UPLOAD_ENABLED = True
SERVER_TIMEOUT      = 30

# ── Recordings storage ────────────────────────────────────────────────────────
RECORDINGS_DIR   = "recordings"
RECORDINGS_INDEX = "recordings_index.json"

# ── Header / top-bar sizing ───────────────────────────────────────────────────
# Increase these values to make the top bar and its icons larger.
# BAR_HEIGHT   : total pixel height of the header bar
# BAR_ICON_SIZE: (width, height) of every icon rendered inside the header bar
BAR_HEIGHT    = 44          # was 36
BAR_ICON_SIZE = (30, 30)    # was (26, 26)

import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont
import datetime, os, math, wave, struct, json, queue, threading, subprocess

try:
    import requests as _requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    import pyaudio
    PYAUDIO_OK = True
except ImportError:
    PYAUDIO_OK = False

try:
    from vosk import Model, KaldiRecognizer
    VOSK_OK = True
except ImportError:
    VOSK_OK = False

import sys as _sys

PIL_OK = False
try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    _PIL_SEARCH = [
        "/usr/lib/python3/dist-packages",
        "/usr/lib/python3.11/dist-packages",
        "/usr/lib/python3.12/dist-packages",
        "/usr/local/lib/python3/dist-packages",
    ]
    for _p in _PIL_SEARCH:
        if _p not in _sys.path and __import__("os").path.isdir(_p):
            _sys.path.insert(0, _p)
    try:
        from PIL import Image, ImageTk
        PIL_OK = True
        print(f"[Icons] PIL found via system path")
    except ImportError:
        print("[Icons] PIL/Pillow not available — using tkinter PNG fallback")

_tk_img_cache: dict = {}

def _load_png_tk(path: str, size=None):
    key = (path, size)
    if key in _tk_img_cache:
        return _tk_img_cache[key]
    try:
        img = tk.PhotoImage(file=path)
        if size:
            iw, ih = img.width(), img.height()
            tw, th = size
            if iw > 0 and ih > 0:
                sx = max(1, iw // tw)
                sy = max(1, ih // th)
                if sx > 1 or sy > 1:
                    img = img.subsample(sx, sy)
        _tk_img_cache[key] = img
        return img
    except Exception as exc:
        print(f"[Icons] tk fallback failed for {path}: {exc}")
        _tk_img_cache[key] = None
        return None

def _set_alsa_capture_volume():
    controls = ["Capture", "ADC", "Mic", "Digital Capture Volume",
                "PGA", "Input", "Master Capture"]
    for ctrl in controls:
        try:
            result = subprocess.run(
                ["amixer", "sset", ctrl, "100%", "cap"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                print(f"[Audio] ALSA capture '{ctrl}' set to 100%")
                return
        except FileNotFoundError:
            print("[Audio] amixer not found — skipping ALSA volume set")
            return
        except Exception:
            continue
    try:
        result = subprocess.run(
            ["amixer", "sset", "Capture", "100%"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            print("[Audio] ALSA Capture set to 100%")
    except Exception as e:
        print(f"[Audio] Could not set ALSA volume: {e}")

# ── Theme palettes ─────────────────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg":       "#1e1e20",
        "surface":  "#2a2a2d",
        "surface2": "#3a3a3e",
        "fg":       "#f5f5f7",
        "fg2":      "#a1a1a6",
        "fg3":      "#58585e",
        "accent":   "#c0392b",
        "accent2":  "#992020",
        "green":    "#30d158",
        "blue":     "#3a8ef6",
        "sep":      "#3a3a3e",
        "wave":     "#3a8ef6",
        "tog_off":  "#48484e",
        "variant":  "Dark",
    },
    "light": {
        "bg":       "#f0f0f5",
        "surface":  "#ffffff",
        "surface2": "#e8e8ee",
        "fg":       "#1c1c1e",
        "fg2":      "#6e6e73",
        "fg3":      "#aeaeb2",
        "accent":   "#c0392b",
        "accent2":  "#992020",
        "green":    "#28a745",
        "blue":     "#1a6ed8",
        "sep":      "#d8d8de",
        "wave":     "#1a6ed8",
        "tog_off":  "#bbbbc2",
        "variant":  "Light",
    },
}

LANGUAGES = [
    "English (US)", "English (UK)", "Español", "Français",
    "Deutsch", "Português", "Italiano", "日本語",
    "中文 (简体)", "한국어", "Русский", "العربية",
]

# ── Icon cache & loader ────────────────────────────────────────────────────────
_icon_cache: dict = {}

def _icons_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isabs(ICONS_DIR):
        return ICONS_DIR
    script_rel = os.path.join(here, ICONS_DIR)
    cwd_rel    = os.path.join(os.getcwd(), ICONS_DIR)
    if os.path.isdir(script_rel):
        return script_rel
    if os.path.isdir(cwd_rel):
        return cwd_rel
    return script_rel

def load_icon(name: str, variant: str, size=None, bg_hex: str = None):
    pass
    key = (name, variant, size, bg_hex)
    if key in _icon_cache:
        return _icon_cache[key]

    d        = _icons_dir()
    parent_d = os.path.dirname(d)
    opposite = "Light" if variant == "Dark" else "Dark"

    candidates = [
        (d,                                   f"{name}{variant}.png"),
        (d,                                   f"{name}.png"),
        (d,                                   f"{name}{opposite}.png"),
        (os.path.join(parent_d, "icons"),     f"{name}{variant}.png"),
        (os.path.join(parent_d, "icons"),     f"{name}.png"),
    ]

    for base, filename in candidates:
        path = os.path.join(base, filename)
        if not os.path.exists(path):
            continue

        if PIL_OK:
            try:
                img = Image.open(path)
                if size:
                    img = img.resize(size, Image.LANCZOS)
                if img.mode in ("RGBA", "LA") or \
                        (img.mode == "P" and "transparency" in img.info):
                    img = img.convert("RGBA")
                    if bg_hex and bg_hex.startswith("#"):
                        try:
                            r = int(bg_hex[1:3], 16)
                            g = int(bg_hex[3:5], 16)
                            b = int(bg_hex[5:7], 16)
                        except ValueError:
                            r, g, b = 30, 30, 32
                    else:
                        r, g, b = 30, 30, 32
                    bg_img = Image.new("RGBA", img.size, (r, g, b, 255))
                    bg_img.paste(img, mask=img.split()[3])
                    img = bg_img.convert("RGB")
                else:
                    img = img.convert("RGB")
                photo = ImageTk.PhotoImage(img)
                _icon_cache[key] = photo
                return photo
            except Exception as exc:
                print(f"[Icon] PIL load failed for {path}: {exc}")

        photo = _load_png_tk(path, size)
        if photo is not None:
            _icon_cache[key] = photo
            return photo

    _icon_cache[key] = None
    return None


def _debug_icons():
    import sys
    print(f"[Icons] Python   : {sys.executable}")
    print(f"[Icons] Directory: {_icons_dir()}  (exists: {os.path.isdir(_icons_dir())})")
    print(f"[Icons] PIL      : {'available' if PIL_OK else 'NOT available — using tk fallback'}")
    test_icons = ["Mic", "Stop", "BackArrow", "SettingsIcon", "DarkMode", "Upload", "Power"]
    for name in test_icons:
        result = load_icon(name, "Dark", size=(24,24))
        print(f"[Icons]   {name}Dark  →  {'OK' if result else 'MISSING'}")


# ── Drawing helpers ────────────────────────────────────────────────────────────
def _hover(w, c1, c2):
    pass

def _row_hover(f, c1, c2):
    def _in(e):
        f.configure(bg=c2)
        for ch in f.winfo_children():
            try: ch.configure(bg=c2)
            except tk.TclError: pass
    def _out(e):
        f.configure(bg=c1)
        for ch in f.winfo_children():
            try: ch.configure(bg=c1)
            except tk.TclError: pass
    f.bind("<Enter>", _in)
    f.bind("<Leave>", _out)

def _line(p, c, padx=0):
    tk.Frame(p, bg=c["sep"], height=1).pack(fill="x", padx=padx)

def _line_top(p, c):
    f = tk.Frame(p, bg=c["sep"], height=1)
    f.place(x=0, y=0, relwidth=1)

def _rrect(cv, x1, y1, x2, y2, r, fill):
    d = 2 * r
    for args in [
        (x1, y1, x1+d, y1+d, 90,  90),
        (x2-d, y1, x2, y1+d, 0,   90),
        (x1, y2-d, x1+d, y2, 180, 90),
        (x2-d, y2-d, x2, y2, 270, 90),
    ]:
        cv.create_arc(*args[:4], start=args[4], extent=args[5], fill=fill, outline="")
    cv.create_rectangle(x1+r, y1, x2-r, y2, fill=fill, outline="")
    cv.create_rectangle(x1, y1+r, x2, y2-r, fill=fill, outline="")

def _lerp_color(c1, c2, t):
    def p(c): return int(c[1:3],16), int(c[3:5],16), int(c[5:7],16)
    r1,g1,b1 = p(c1); r2,g2,b2 = p(c2)
    r=int(r1+(r2-r1)*t); g=int(g1+(g2-g1)*t); b=int(b1+(b2-b1)*t)
    return f"#{r:02x}{g:02x}{b:02x}"

# ── Global touch drag guard ──────────────────────────────────────────────────
# Tracks whether the active press has been classified as a scroll drag.
# _tap_ok() returns False once dragging is confirmed, blocking tap callbacks.
_DRAG = {"active": False, "dist": 0}

# Minimum travel (px) before a press is considered a drag, not a tap.
_DRAG_THRESHOLD = 10

def _tap_ok():
    return not _DRAG["active"]

# ── Touch-drag scrollable area (no scrollbar, momentum) ──────────────────────
def make_touch_scroll(parent, bg):
    import time as _time

    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0, cursor="none")
    canvas.pack(fill="both", expand=True)

    inner = tk.Frame(canvas, bg=bg, cursor="none")
    win   = canvas.create_window((0, 0), window=inner, anchor="nw")

    # Keep the scroll region and inner width in sync whenever content changes.
    def _update_scroll_region(e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _update_inner_width(e=None):
        canvas.itemconfig(win, width=canvas.winfo_width())
        canvas.configure(scrollregion=canvas.bbox("all"))

    inner.bind("<Configure>", _update_scroll_region)
    canvas.bind("<Configure>", _update_inner_width)

    # Per-scroll-area state — each instance gets its own dict so multiple
    # scroll areas on screen never interfere with each other.
    _st = {
        "y":           None,   # last recorded y_root during drag
        "y0":          None,   # y_root at press — used for threshold check
        "t":           None,   # monotonic time of last motion sample
        "vel":         0.0,    # px/ms at release, used for momentum
        "dragging":    False,  # True once threshold crossed
        "momentum_id": None,   # after() job id for momentum loop
        "pressed":     False,  # True between ButtonPress and ButtonRelease
    }

    # Tuning constants
    DRAG_THRESHOLD = _DRAG_THRESHOLD   # px before scroll activates
    DECEL          = 0.90              # velocity multiplier each frame
    MIN_VEL        = 0.5               # px/ms below which momentum stops
    FRAME_MS       = 14                # ~70 fps momentum loop

    # ── helpers ──────────────────────────────────────────────────────────────

    def _content_height():
        bbox = canvas.bbox("all")
        return (bbox[3] - bbox[1]) if bbox else 0

    def _view_height():
        return canvas.winfo_height()

    def _can_scroll():
        return _content_height() > _view_height()

    def _scroll_by_px(delta_px):
        """Move the viewport by delta_px (positive = scroll down/content up)."""
        if not _can_scroll():
            return
        total = _content_height()
        view  = _view_height()
        top   = canvas.yview()[0]
        new_top = top + delta_px / total
        new_top = max(0.0, min(1.0 - view / total, new_top))
        canvas.yview_moveto(new_top)

    def _stop_momentum():
        if _st["momentum_id"] is not None:
            try:
                canvas.after_cancel(_st["momentum_id"])
            except Exception:
                pass
            _st["momentum_id"] = None
        _st["vel"] = 0.0

    def _momentum_step():
        if not _st["vel"] or abs(_st["vel"]) < MIN_VEL:
            _st["vel"] = 0.0
            _st["momentum_id"] = None
            return
        _scroll_by_px(_st["vel"] * FRAME_MS)
        _st["vel"] *= DECEL
        _st["momentum_id"] = canvas.after(FRAME_MS, _momentum_step)

    # ── event handlers ───────────────────────────────────────────────────────

    def _press(e):
        _stop_momentum()
        _st["y"]        = e.y_root
        _st["y0"]       = e.y_root
        _st["t"]        = _time.monotonic()
        _st["vel"]      = 0.0
        _st["dragging"] = False
        _st["pressed"]  = True
        # Do NOT set _DRAG["active"] here — wait until threshold is crossed.

    def _motion(e):
        if not _st["pressed"] or _st["y"] is None:
            return

        total_travel = abs(e.y_root - _st["y0"])

        # Promote to drag once the finger has moved enough.
        if not _st["dragging"]:
            if total_travel >= DRAG_THRESHOLD:
                _st["dragging"]  = True
                _DRAG["active"]  = True
                _DRAG["dist"]    = total_travel
            else:
                return   # still within tap zone — don't scroll yet

        now = _time.monotonic()
        dt  = now - _st["t"]
        dy  = _st["y"] - e.y_root   # positive = finger moved up = scroll down

        # Guard against dt==0 (can happen on very fast events).
        if dt > 0:
            # Velocity in px/ms — exponential moving average for smoothness.
            instant_vel = dy / (dt * 1000)
            _st["vel"]  = _st["vel"] * 0.6 + instant_vel * 0.4

        _st["y"] = e.y_root
        _st["t"] = now
        _scroll_by_px(dy)

    def _release(e):
        if not _st["pressed"]:
            return
        was_dragging   = _st["dragging"]
        _st["pressed"] = False
        _st["y"]       = None
        _st["y0"]      = None
        _st["dragging"] = False

        if was_dragging:
            # Small delay so ButtonRelease-1 handlers on children fire first,
            # then we clear the drag guard — this ensures taps are never
            # accidentally suppressed on the release event itself.
            canvas.after(30, lambda: _DRAG.update({"active": False, "dist": 0}))
            if abs(_st["vel"]) >= MIN_VEL:
                _momentum_step()
        else:
            _DRAG["active"] = False
            _DRAG["dist"]   = 0

    # ── bind helpers ─────────────────────────────────────────────────────────

    def _bind_scroll(widget):
        """Recursively attach scroll handlers to widget and all descendants."""
        try:
            widget.bind("<ButtonPress-1>",   _press,   add="+")
            widget.bind("<B1-Motion>",       _motion,  add="+")
            widget.bind("<ButtonRelease-1>", _release, add="+")
        except Exception:
            pass
        for ch in widget.winfo_children():
            _bind_scroll(ch)

    # Bind directly on the canvas first.
    canvas.bind("<ButtonPress-1>",   _press)
    canvas.bind("<B1-Motion>",       _motion)
    canvas.bind("<ButtonRelease-1>", _release)

    # Re-bind whenever new children are added to inner (e.g. list rows).
    def _on_map(e):
        _bind_scroll(inner)
    inner.bind("<Map>", _on_map)

    return canvas, inner, _bind_scroll

# ── Toggle Switch ──────────────────────────────────────────────────────────────
class Toggle(tk.Canvas):
    def __init__(self, parent, c, variable=None, initial=False, on_color=None):
        super().__init__(parent, width=42, height=24, bg=parent["bg"],
                         highlightthickness=0, cursor="none")
        self.c = c
        self.on_color = on_color or c["accent"]
        self.state = initial
        self.var   = variable
        self.cb    = None
        self.bind("<ButtonRelease-1>", lambda e: self._toggle(e) if _tap_ok() else None)
        self._draw()

    def set_cb(self, cb): self.cb = cb

    def _toggle(self, e):
        self.state = not self.state
        if self.var: self.var.set(self.state)
        if self.cb:  self.cb(self.state)
        self._draw()

    def _draw(self):
        self.delete("all")
        bg = self.on_color if self.state else self.c["tog_off"]
        _rrect(self, 2, 2, 40, 22, 10, bg)
        x = 29 if self.state else 13
        self.create_oval(x-8, 4, x+8, 20, fill="white", outline="")

# ── PNG-aware label helper ─────────────────────────────────────────────────────
def _png_label(parent, icon_name, variant, fallback, font, bg, fg,
               size=(24,24), **kw):
    photo = load_icon(icon_name, variant, size=size, bg_hex=bg)
    if photo:
        lbl = tk.Label(parent, image=photo, bg=bg, cursor="none", **kw)
        lbl.image = photo
    else:
        lbl = tk.Label(parent, text=fallback, font=font, bg=bg, fg=fg,
                       cursor="none", **kw)
    return lbl

# ── Audio DSP helpers ─────────────────────────────────────────────────────────
def _apply_gain_clip(raw_bytes: bytes, gain: float, clip: float) -> bytes:
    CEILING  = 32767
    KNEE_ABS = int(CEILING * SOFT_KNEE_THRESHOLD)
    KNEE_RM  = CEILING - KNEE_ABS

    count   = len(raw_bytes) // 2
    samples = struct.unpack(f"{count}h", raw_bytes)
    out = []
    for s in samples:
        v    = s * gain
        sign = 1 if v >= 0 else -1
        av   = abs(v)

        if NOISE_GATE_THRESHOLD and av < NOISE_GATE_THRESHOLD:
            out.append(0)
            continue

        if av > KNEE_ABS:
            excess = av - KNEE_ABS
            av = KNEE_ABS + KNEE_RM * math.sqrt(min(excess / KNEE_RM, 1.0))

        v = sign * av
        if   v >  CEILING: v =  CEILING
        elif v < -CEILING: v = -CEILING
        out.append(int(v))

    return struct.pack(f"{count}h", *out)


def _normalize_frames(frames: list) -> list:
    peak = 0
    for chunk in frames:
        count   = len(chunk) // 2
        samples = struct.unpack(f"{count}h", chunk)
        local   = max(abs(s) for s in samples) if samples else 0
        if local > peak:
            peak = local
    if peak < 64:
        return frames
    TARGET = 29204
    scale  = TARGET / peak
    if scale < 1.0:
        return frames
    out = []
    for chunk in frames:
        count   = len(chunk) // 2
        samples = struct.unpack(f"{count}h", chunk)
        normed  = [max(-32768, min(32767, int(s * scale))) for s in samples]
        out.append(struct.pack(f"{count}h", *normed))
    return out


# ══════════════════════════════════════════════════════════════════════════════
class AudioRecorder:
    def __init__(self, tq: queue.Queue, lq: queue.Queue):
        self._tq = tq; self._lq = lq
        self._stop   = threading.Event()
        self._thread = None
        self._frames = []
        self._model = self._rec = self._pa = self._stream = None
        self.error  = None

    def _load_model(self):
        if not VOSK_OK:
            self.error = "vosk not installed.\nRun: pip install vosk"; return False
        if not PYAUDIO_OK:
            self.error = "pyaudio not installed.\nRun: pip install pyaudio"; return False
        here = os.path.dirname(os.path.abspath(__file__))
        mp = VOSK_MODEL_PATH if os.path.isabs(VOSK_MODEL_PATH) \
             else os.path.join(here, VOSK_MODEL_PATH)
        if not os.path.isdir(mp):
            self.error = (f"Vosk model not found at:\n{mp}\n\n"
                          "Download from https://alphacephei.com/vosk/models")
            return False
        try:
            self._model = Model(mp)
            self._rec   = KaldiRecognizer(self._model, SAMPLE_RATE)
            self._rec.SetWords(True)
        except Exception as e:
            self.error = f"Failed to load Vosk model:\n{e}"; return False
        return True

    def start(self):
        if not self._load_model(): return False
        try:
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=CHANNELS,
                rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
        except Exception as e:
            self.error = f"Could not open microphone:\n{e}"; return False
        self._frames = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=3)
        for obj, method in [(self._stream, "stop_stream"), (self._stream, "close"),
                             (self._pa, "terminate")]:
            if obj:
                try: getattr(obj, method)()
                except Exception: pass
        self._stream = self._pa = None

    def get_frames(self) -> list:
        frames = list(self._frames)
        if NORMALIZE_WAV and frames:
            frames = _normalize_frames(frames)
        return frames

    def _run(self):
        # How many audio chunks to skip between partial-result polls.
        # At CHUNK_SIZE=4000 / SAMPLE_RATE=16000 each chunk is 250 ms.
        # Polling partials every 4 chunks = ~1 s — cheap enough for a Pi.
        PARTIAL_EVERY = 4
        chunk_count   = 0

        while not self._stop.is_set():
            try:
                raw = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception: break

            boosted = _apply_gain_clip(raw, MIC_GAIN, SOFT_KNEE_THRESHOLD)
            self._frames.append(boosted)

            # RMS for the waveform visualiser — drop if queue is full, never block.
            count   = len(boosted) // 2
            samples = struct.unpack(f"{count}h", boosted)
            rms = math.sqrt(sum(s * s for s in samples) / count) / 32768
            try: self._lq.put_nowait(min(1.0, rms * 1.5))
            except queue.Full: pass

            # Feed audio to Vosk.  AcceptWaveform returns True when a full
            # utterance boundary is detected (silence / end of sentence).
            got_final = self._rec.AcceptWaveform(boosted)

            if got_final:
                text = json.loads(self._rec.Result()).get("text", "").strip()
                if text:
                    try: self._tq.put_nowait({"final": text})
                    except queue.Full: pass
                chunk_count = 0   # reset partial throttle after each final
            else:
                # Only fetch a partial every N chunks to avoid hammering the Pi.
                chunk_count += 1
                if chunk_count >= PARTIAL_EVERY:
                    chunk_count = 0
                    text = json.loads(self._rec.PartialResult()).get("partial", "").strip()
                    if text:
                        try: self._tq.put_nowait({"partial": text})
                        except queue.Full: pass

        if self._rec:
            text = json.loads(self._rec.FinalResult()).get("text", "").strip()
            if text:
                try: self._tq.put_nowait({"final": text})
                except queue.Full: pass


def save_wav(frames, path):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE); wf.writeframes(b"".join(frames))


def upload_to_server(wav_path, local_transcript="", on_done=None):
    if not SERVER_UPLOAD_ENABLED or not REQUESTS_OK or not wav_path:
        return

    def _worker():
        try:
            with open(wav_path, "rb") as f:
                resp = _requests.post(
                    f"{SERVER_URL}/upload",
                    files={"audio": (os.path.basename(wav_path), f, "audio/wav")},
                    data={"local_transcript": local_transcript},
                    timeout=SERVER_TIMEOUT,
                )
            if resp.status_code == 200:
                result = resp.json()
                msg = "Uploaded & processed: " + result.get("title", "recording")
                if on_done: on_done(True, msg)
            else:
                if on_done: on_done(False, f"Server error {resp.status_code}")
        except Exception as exc:
            if on_done: on_done(False, f"Upload failed: {exc}")

    threading.Thread(target=_worker, daemon=True).start()

# ── Persistent recordings index ───────────────────────────────────────────────
def _recordings_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    d    = RECORDINGS_DIR if os.path.isabs(RECORDINGS_DIR) \
           else os.path.join(here, RECORDINGS_DIR)
    os.makedirs(d, exist_ok=True)
    return d

def _recordings_index_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return RECORDINGS_INDEX if os.path.isabs(RECORDINGS_INDEX) \
           else os.path.join(here, RECORDINGS_INDEX)

def _load_recordings() -> list:
    path = _recordings_index_path()
    records = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                records = json.load(f)
        except Exception as e:
            print(f"Warning: could not load recordings index: {e}")

    records = [r for r in records if r.get("wav") and os.path.exists(r["wav"])]

    known = {r["wav"] for r in records}

    rec_dir = _recordings_dir()
    found = sorted(
        (os.path.join(rec_dir, fn) for fn in os.listdir(rec_dir)
         if fn.lower().endswith(".wav")),
        key=os.path.getmtime
    )
    for wav in found:
        if wav not in known:
            mtime = os.path.getmtime(wav)
            dt    = datetime.datetime.fromtimestamp(mtime)
            try:    name = dt.strftime("Recording %b %-d, %I:%M %p")
            except: name = dt.strftime("Recording %b %d, %I:%M %p")
            records.append({
                "name":       name,
                "timestamp":  dt.strftime("%I:%M %p"),
                "wav":        wav,
                "transcript": "",
            })

    return records

def _save_recordings(records: list):
    path = _recordings_index_path()
    try:
        with open(path, "w") as f:
            json.dump(records, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save recordings index: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# WiFi helpers
# ══════════════════════════════════════════════════════════════════════════════

def _wifi_current_ssid():
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and "wireless" in parts[1] and "activated" in parts[2]:
                return parts[0]
    except Exception:
        pass
    return ""


def _wifi_scan():
    try:
        subprocess.run(["nmcli", "dev", "wifi", "rescan"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
             "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=8)
        seen, nets = set(), []
        for line in r.stdout.strip().splitlines():
            parts = line.rsplit(":", 3)
            if len(parts) < 4:
                continue
            ssid, signal, security, inuse = parts
            ssid = ssid.strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:    sig = int(signal)
            except: sig = 0
            nets.append({
                "ssid":      ssid,
                "signal":    sig,
                "secured":   bool(security.strip()),
                "connected": inuse.strip() == "*",
            })
        nets.sort(key=lambda n: -n["signal"])
        return nets
    except Exception as e:
        print("[WiFi] scan error:", e)
        return []


def _wifi_connect(ssid, password=""):
    try:
        r = subprocess.run(["sudo", "nmcli", "con", "up", ssid],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, "Connected to " + ssid
    except Exception:
        pass
    if not password:
        return False, "Password required"
    try:
        r = subprocess.run(
            ["sudo", "nmcli", "dev", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=25)
        if r.returncode == 0:
            return True, "Connected to " + ssid
        raw = (r.stderr or r.stdout).strip().splitlines()
        msg = raw[-1] if raw else "Failed"
        return False, msg[:60]
    except Exception as e:
        return False, str(e)[:60]


def _wifi_disconnect():
    ssid = _wifi_current_ssid()
    if not ssid:
        return False
    try:
        r = subprocess.run(["sudo", "nmcli", "con", "down", ssid],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _signal_bars(signal):
    if signal >= 75: return "▂▄▆█"
    if signal >= 50: return "▂▄▆ "
    if signal >= 25: return "▂▄  "
    return "▂   "


# ══════════════════════════════════════════════════════════════════════════════
# On-screen keyboard
# ══════════════════════════════════════════════════════════════════════════════

class TouchKeyboard(tk.Frame):
    _ROWS_LO  = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
    _ROWS_HI  = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
    _ROWS_NUM = ["1234567890", "-/:;()$&@\"", ".,?!'[]{}"]

    def __init__(self, parent, c, fonts,
                 on_submit=None, on_cancel=None, placeholder="Password"):
        super().__init__(parent, bg=c["bg"], cursor="none")
        self._c           = c
        self._F           = fonts
        self._on_submit   = on_submit
        self._on_cancel   = on_cancel
        self._placeholder = placeholder
        self._text        = ""
        self._shift       = False
        self._caps        = False
        self._nums        = False
        self._show        = False
        self._build()

    def _build(self):
        for w in self.winfo_children():
            w.destroy()
        c, F = self._c, self._F

        inp = tk.Frame(self, bg=c["surface"], cursor="none")
        inp.pack(fill="x", pady=(0, 3))

        displayed = self._text if self._show else ("•" * len(self._text))
        inp_fg    = c["fg"] if self._text else c["fg3"]
        inp_text  = displayed if self._text else self._placeholder

        self._lbl = tk.Label(inp, text=inp_text, font=F["body"],
                             bg=c["surface"], fg=inp_fg,
                             anchor="w", padx=10, pady=7, cursor="none")
        self._lbl.pack(side="left", fill="x", expand=True)

        eye = tk.Label(inp, text=("○" if self._show else "●"),
                       font=F["small"], bg=c["surface"], fg=c["fg2"],
                       padx=10, cursor="none")
        eye.pack(side="right")
        eye.bind("<ButtonRelease-1>", lambda e: self._toggle_show())

        keys_f = tk.Frame(self, bg=c["bg"], cursor="none")
        keys_f.pack(fill="both", expand=True)

        rows = self._ROWS_NUM if self._nums else \
               (self._ROWS_HI if (self._shift or self._caps) else self._ROWS_LO)

        for row_str in rows:
            rf = tk.Frame(keys_f, bg=c["bg"], cursor="none")
            rf.pack(fill="x", pady=1)
            for ch in row_str:
                self._key(rf, ch)

        ctrl = tk.Frame(keys_f, bg=c["bg"], cursor="none")
        ctrl.pack(fill="x", pady=1)

        sh_label = ("ABC" if self._nums
                    else ("⇪" if self._caps else ("⇧" if self._shift else "⇧")))
        sh_fg    = c["blue"] if (self._shift or self._caps) else c["fg2"]
        self._ctrl_key(ctrl, sh_label,  self._do_shift,  fg=sh_fg,  w=4)
        self._ctrl_key(ctrl, "123" if not self._nums else "abc",
                                         self._do_nums,               w=4)
        self._ctrl_key(ctrl, "        ", lambda: self._type(" "), expand=True)
        self._ctrl_key(ctrl, "⌫",        self._do_back,               w=4)
        self._ctrl_key(ctrl, "↵",        self._do_submit, fg=c["green"], w=4)

    def _key(self, parent, ch):
        c = self._c
        b = tk.Label(parent, text=ch, font=self._F["body"],
                     bg=c["surface2"], fg=c["fg"],
                     width=2, pady=3, relief="flat", cursor="none")
        b.pack(side="left", expand=True, fill="x", padx=1)
        b.bind("<ButtonRelease-1>", lambda e, x=ch: self._type(x))
        _hover(b, c["surface2"], c["surface"])

    def _ctrl_key(self, parent, label, cmd, fg=None, w=None, expand=False):
        c = self._c
        kw = dict(text=label, font=self._F["small"],
                  bg=c["surface2"], fg=fg or c["fg2"],
                  pady=5, relief="flat", cursor="none")
        if w:
            kw["width"] = w
        b = tk.Label(parent, **kw)
        b.pack(side="left", expand=expand, fill="x", padx=1)
        b.bind("<ButtonRelease-1>", lambda e: cmd())
        _hover(b, c["surface2"], c["surface"])

    def _type(self, ch):
        self._text += ch
        if self._shift and not self._caps:
            self._shift = False
            self._build()
        else:
            self._refresh_input()

    def _do_back(self):
        self._text = self._text[:-1]
        self._refresh_input()

    def _do_shift(self):
        if self._nums:
            self._nums = False
            self._shift = False
        else:
            if not self._shift and not self._caps:
                self._shift = True
            elif self._shift and not self._caps:
                self._caps = True
                self._shift = False
            else:
                self._caps = False
        self._build()

    def _do_nums(self):
        self._nums = not self._nums
        self._shift = self._caps = False
        self._build()

    def _toggle_show(self):
        self._show = not self._show
        self._refresh_input()

    def _do_submit(self):
        if self._on_submit:
            self._on_submit(self._text)

    def _refresh_input(self):
        displayed = self._text if self._show else ("•" * len(self._text))
        if self._text:
            self._lbl.configure(text=displayed, fg=self._c["fg"])
        else:
            self._lbl.configure(text=self._placeholder, fg=self._c["fg3"])

    def get(self):
        return self._text


# ══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("480x320")
        self.minsize(480, 320)
        self.maxsize(480, 320)
        self.attributes('-fullscreen', True)
        self.resizable(False, False)
        self.config(cursor="none")

        self.theme              = "dark"
        self.language           = tk.StringVar(value="English (US)")
        self.live_transcription = tk.BooleanVar(value=True)
        self.system_sounds      = tk.BooleanVar(value=True)
        self.is_recording       = False
        self.recordings         = _load_recordings()

        self._wave_job    = None
        self._mic_level   = 0.0
        self._vol_history = [0.0] * 110
        self._transcript_final   = ""
        self._transcript_partial = ""
        self._tq = queue.Queue(maxsize=50)
        self._lq = queue.Queue(maxsize=10)
        self._audio_rec = None

        _set_alsa_capture_volume()
        _debug_icons()

        self._init_fonts()
        self.configure(bg=self.c["bg"])
        self.frame = tk.Frame(self, bg=self.c["bg"], cursor="none")
        self.frame.pack(fill="both", expand=True)
        self._show_main()
        self._clock_tick()

    # ── Shorthand properties ──────────────────────────────────────────────────
    @property
    def c(self): return THEMES[self.theme]
    @property
    def tv(self): return self.c["variant"]

    # ── Font init ─────────────────────────────────────────────────────────────
    def _init_fonts(self):
        av = list(tkfont.families())
        def pick(*ns):
            for n in ns:
                if n in av: return n
            return "TkDefaultFont"
        body = pick("Outfit","Nunito","Poppins","SF Pro Display",
                    "Helvetica Neue","Segoe UI","Ubuntu","Helvetica")
        self.F = {
            "clock":   (body, 13, "bold"),
            "date":    (body, 12),
            "title":   (body, 25, "bold"),
            "body":    (body, 13),
            "bodyb":   (body, 13, "bold"),
            "small":   (body, 11),
            "tiny":    (body, 9),
            "section": (body, 9, "bold"),
            "btn":     (body, 10, "bold"),
            "trans":   (body, 12),
            "il":      (body, 24),
            "im":      (body, 19),
            "is":      (body, 15),
        }

    def _clear(self, cancel_wave=True):
        if cancel_wave: self._wave_job = None
        for w in self.frame.winfo_children(): w.destroy()
        self.configure(bg=self.c["bg"])
        self.frame.configure(bg=self.c["bg"])

    def _clock_tick(self):
        now = datetime.datetime.now()
        self._time_str = now.strftime("%I:%M %p").lstrip("0")
        try:    self._date_str = now.strftime("%b %-d")
        except: self._date_str = now.strftime("%b %d").lstrip("0")
        for attr in ("_clk", "_dat"):
            lbl = getattr(self, attr, None)
            if lbl and lbl.winfo_exists():
                lbl.configure(text=self._time_str if attr=="_clk" else self._date_str)
        self.after(1000, self._clock_tick)

    # ── Shared header ─────────────────────────────────────────────────────────
    def _header(self, parent, back=None, title=None, actions=False):
        c = self.c
        # BAR_HEIGHT and BAR_ICON_SIZE are the module-level config variables.
        # Adjust those two constants at the top of the file to resize the bar.
        BAR_H  = BAR_HEIGHT
        ICO_SZ = BAR_ICON_SIZE

        # Derive a comfortable horizontal padding from icon height
        ICO_PADX = max(2, ICO_SZ[1] // 6)

        bar = tk.Frame(parent, bg=c["bg"], height=BAR_H, cursor="none")
        bar.pack(fill="x", padx=10, pady=(3, 0))
        bar.pack_propagate(False)   # enforce fixed height

        # ── helper: icon-or-text label centred in bar ─────────────────────────
        def _bar_icon(parent, icon_name, fallback, fg, padx=None, **kw):
            _padx = ICO_PADX if padx is None else padx
            photo = load_icon(icon_name, self.tv, size=ICO_SZ, bg_hex=c["bg"])
            if photo:
                lbl = tk.Label(parent, image=photo, bg=c["bg"],
                               cursor="none", padx=_padx, **kw)
                lbl.image = photo
            else:
                lbl = tk.Label(parent, text=fallback,
                               font=self.F["small"], bg=c["bg"], fg=fg,
                               cursor="none", padx=_padx, **kw)
            return lbl

        # ── LEFT side ─────────────────────────────────────────────────────────
        left = tk.Frame(bar, bg=c["bg"], cursor="none")
        left.pack(side="left", fill="y")

        if back:
            b = _bar_icon(left, "BackArrow", "←", c["fg"])
            b.pack(side="left", fill="y")
            b.bind("<ButtonRelease-1>", lambda e: back() if _tap_ok() else None)
            if title:
                tk.Label(left, text=title, font=self.F["bodyb"],
                         bg=c["bg"], fg=c["fg"], padx=8,
                         cursor="none").pack(side="left", fill="y")
        else:
            # Power-off button on the far left (main screen only)
            if actions:
                # Use "Power" icon name so it resolves to PowerDark.png / PowerLight.png
                pw = _bar_icon(left, "Power", "⏻", c["fg3"])
                pw.pack(side="left", fill="y")
                pw.bind("<ButtonRelease-1>",
                        lambda e: self._confirm_power_off() if _tap_ok() else None)

            self._clk = tk.Label(left, text=getattr(self, "_time_str", ""),
                                 font=self.F["clock"], bg=c["bg"], fg=c["fg"],
                                 cursor="none", padx=6)
            self._clk.pack(side="left", fill="y")

        # ── RIGHT side ────────────────────────────────────────────────────────
        right = tk.Frame(bar, bg=c["bg"], cursor="none")
        right.pack(side="right", fill="y")

        if not back:
            self._dat = tk.Label(right, text=getattr(self, "_date_str", ""),
                                 font=self.F["date"], bg=c["bg"], fg=c["fg2"],
                                 cursor="none", padx=4)
            self._dat.pack(side="left", fill="y")

        if actions:
            up = _bar_icon(right, "Upload", "↑", c["fg3"])
            up.pack(side="left", fill="y")
            def _manual_upload():
                last = next((r for r in reversed(self.recordings) if r.get("wav")), None)
                if not last:
                    self._show_toast("No recording to upload", color="accent"); return
                self._show_toast("Uploading…", color="blue")
                def _done(ok, msg):
                    self.after(0, lambda: self._show_toast(msg, color="green" if ok else "accent", duration=4000))
                upload_to_server(last["wav"], local_transcript=last.get("transcript",""), on_done=_done)
            up.bind("<ButtonRelease-1>", lambda e: _manual_upload() if _tap_ok() else None)

            sg = _bar_icon(right, "SettingsIcon", "⚙", c["fg"])
            sg.pack(side="left", fill="y")
            sg.bind("<ButtonRelease-1>",
                    lambda e: self._show_settings() if _tap_ok() else None)

    # ── Power off confirmation ─────────────────────────────────────────────────
    def _confirm_power_off(self):
        """Show a full-screen confirmation before powering off."""
        c = self.c
        overlay = tk.Frame(self, bg=c["bg"], cursor="none")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)

        tk.Label(overlay, text="⏻", font=(self.F["title"][0], 48),
                 bg=c["bg"], fg=c["fg3"], cursor="none").pack(pady=(52, 8))
        tk.Label(overlay, text="Power Off?", font=self.F["title"],
                 bg=c["bg"], fg=c["fg"], cursor="none").pack()
        tk.Label(overlay, text="The device will shut down.",
                 font=self.F["body"], bg=c["bg"], fg=c["fg2"],
                 cursor="none").pack(pady=(4, 28))

        btn_row = tk.Frame(overlay, bg=c["bg"], cursor="none")
        btn_row.pack()

        cancel_btn = tk.Label(btn_row, text="  Cancel  ",
                              font=self.F["bodyb"],
                              bg=c["surface2"], fg=c["fg"],
                              padx=18, pady=10, cursor="none")
        cancel_btn.pack(side="left", padx=10)
        cancel_btn.bind("<ButtonRelease-1>",
                        lambda e: overlay.destroy() if _tap_ok() else None)

        off_btn = tk.Label(btn_row, text="  Power Off  ",
                           font=self.F["bodyb"],
                           bg=c["accent"], fg="white",
                           padx=18, pady=10, cursor="none")
        off_btn.pack(side="left", padx=10)
        off_btn.bind("<ButtonRelease-1>",
                     lambda e: self._do_power_off() if _tap_ok() else None)

    def _do_power_off(self):
        """Execute system shutdown."""
        try:
            subprocess.run(["sudo", "shutdown", "-h", "now"],
                           capture_output=True, timeout=5)
        except Exception as e:
            print(f"[PowerOff] shutdown failed: {e}")
            try:
                subprocess.run(["shutdown", "-h", "now"],
                               capture_output=True, timeout=5)
            except Exception as e2:
                print(f"[PowerOff] fallback also failed: {e2}")

    # ── Main screen ───────────────────────────────────────────────────────────
    def _show_main(self):
        self._clear(cancel_wave=False)
        c = self.c
        self._header(self.frame, actions=True)
        _line(self.frame, c)

        center = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        center.pack(fill="both", expand=True)

        sz = 180
        self._main_cv = tk.Canvas(center, width=sz, height=sz,
                                  bg=c["bg"], highlightthickness=0, cursor="none")
        self._main_cv.place(relx=0.5, rely=0.44, anchor="center")
        self._draw_record_btn(self._main_cv, sz)
        self._main_cv.bind("<ButtonRelease-1>", lambda e: self._start_recording() if _tap_ok() else None)

        bot = tk.Frame(self.frame, bg=c["surface"], height=56, cursor="none")
        bot.pack(fill="x", side="bottom")
        bot.pack_propagate(False)
        _line_top(bot, c)

        if self.recordings:
            rec = self.recordings[-1]
            row = tk.Frame(bot, bg=c["surface"], cursor="none")
            row.pack(fill="x", padx=20, pady=10)
            mic_ic = _png_label(row, "Mic", self.tv, "🎙", self.F["im"],
                                c["surface2"], c["fg2"], size=(22,22), padx=6, pady=2)
            mic_ic.pack(side="left")
            inf = tk.Frame(row, bg=c["surface"], cursor="none")
            inf.pack(side="left", padx=10)
            tk.Label(inf, text=rec["name"], font=self.F["bodyb"],
                     bg=c["surface"], fg=c["fg"], cursor="none").pack(anchor="w")
            tk.Label(inf, text=rec["timestamp"], font=self.F["small"],
                     bg=c["surface"], fg=c["fg2"], cursor="none").pack(anchor="w")
        else:
            tk.Label(bot, text="No recordings yet", font=self.F["body"],
                     bg=c["surface"], fg=c["fg3"], cursor="none").pack(expand=True)

    def _draw_record_btn(self, cv, sz):
        cv.delete("all")
        c = self.c; pad = 6
        glow = _lerp_color(c["accent"], c["bg"], 0.65)
        cv.create_oval(pad-4, pad-4, sz-pad+4, sz-pad+4,
                       fill="", outline=glow, width=6)
        cv.create_oval(pad, pad, sz-pad, sz-pad, fill=c["accent"], outline="")
        photo = load_icon("Mic", self.tv, size=(68,68), bg_hex=c["accent"])
        if photo:
            cv.create_image(sz//2, sz//2, image=photo, anchor="center")
            cv._mic_ref = photo
        else:
            cv.create_text(sz//2, sz//2, text="🎙",
                           font=self.F["il"], fill="white")

    # ── Recording screen ──────────────────────────────────────────────────────
    def _start_recording(self):
        self._transcript_final = self._transcript_partial = ""
        self._vol_history = [0.0]*110
        for q in (self._tq, self._lq):
            while True:
                try: q.get_nowait()
                except queue.Empty: break

        self._audio_rec = AudioRecorder(self._tq, self._lq)
        if not self._audio_rec.start():
            messagebox.showerror("Microphone Error",
                                 self._audio_rec.error or "Unknown error.")
            return

        self.is_recording = True
        self._clear(cancel_wave=False)
        c = self.c
        self._header(self.frame)
        _line(self.frame, c)

        trans_bar = tk.Frame(self.frame, bg=c["surface"], height=72, cursor="none")
        trans_bar.pack(side="bottom", fill="x")
        trans_bar.pack_propagate(False)
        _line_top(trans_bar, c)

        # Use a Text widget so long transcripts scroll instead of clipping.
        # The widget is read-only; we insert text programmatically.
        self._lt = tk.Text(
            trans_bar,
            font=self.F["trans"],
            bg=c["surface"], fg=c["fg2"],
            wrap="word",
            relief="flat", bd=0,
            highlightthickness=0,
            cursor="none",
            state="disabled",
        )
        self._lt.pack(fill="both", expand=True, padx=8, pady=4)

        mid = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        mid.pack(fill="both", expand=True)

        ssz = 110
        self._stop_cv = tk.Canvas(mid, width=ssz, height=ssz,
                                  bg=c["bg"], highlightthickness=0, cursor="none")
        self._stop_cv.pack(pady=(8,2))
        _rrect(self._stop_cv, 6, 6, ssz-6, ssz-6, 20, c["accent"])
        stop_ph = load_icon("Stop", self.tv, size=(46,46))
        if stop_ph:
            self._stop_cv.create_image(ssz//2, ssz//2, image=stop_ph, anchor="center")
            self._stop_cv._stop_ref = stop_ph
        else:
            sq = 36
            self._stop_cv.create_rectangle(
                ssz//2-sq//2, ssz//2-sq//2, ssz//2+sq//2, ssz//2+sq//2,
                fill="white", outline="")
        self._stop_cv.bind("<ButtonRelease-1>", lambda e: self._stop_recording() if _tap_ok() else None)

        tk.Label(mid, text="Recording", font=self.F["bodyb"],
                 bg=c["bg"], fg=c["fg"], cursor="none").pack(pady=(0,4))

        self._wc = tk.Canvas(mid, width=440, height=44,
                             bg=c["bg"], highlightthickness=0, cursor="none")
        self._wc.pack(pady=(0,4))
        self._mic_level = 0.0
        self._wave_tick()
        self._poll_transcript()

    def _poll_transcript(self):
        if not self.is_recording: return

        # Drain the level queue — we only care about the most recent value.
        while True:
            try:    self._mic_level = self._lq.get_nowait()
            except queue.Empty: break

        # Drain all pending transcript messages.
        changed = False
        while True:
            try:    msg = self._tq.get_nowait()
            except queue.Empty: break
            if "final" in msg:
                self._transcript_final += (" " if self._transcript_final else "") + msg["final"]
                self._transcript_partial = ""
                changed = True
            elif "partial" in msg:
                self._transcript_partial = msg["partial"]
                changed = True

        # Only redraw the text widget when something actually changed —
        # avoids the constant re-layout cost that slows the Pi down.
        if changed and hasattr(self, "_lt") and self._lt.winfo_exists():
            display = self._transcript_final
            if self._transcript_partial:
                display += (" " if display else "") + self._transcript_partial + "…"
            if not display:
                display = "Listening…"

            self._lt.configure(state="normal")
            self._lt.delete("1.0", "end")
            self._lt.insert("end", display)
            self._lt.configure(state="disabled")
            # Keep the newest text visible.
            self._lt.see("end")

        elif not changed and hasattr(self, "_lt") and self._lt.winfo_exists():
            # Show placeholder if nothing has arrived yet.
            if not self._transcript_final and not self._transcript_partial:
                current = self._lt.get("1.0", "end").strip()
                if current != "Listening…":
                    self._lt.configure(state="normal")
                    self._lt.delete("1.0", "end")
                    self._lt.insert("end", "Listening…")
                    self._lt.configure(state="disabled")

        self.after(80, self._poll_transcript)

    def _wave_tick(self):
        if not (hasattr(self,"_wc") and self._wc.winfo_exists()): return
        if not self.is_recording: return
        self._vol_history.append(self._mic_level)
        if len(self._vol_history) > 110: self._vol_history.pop(0)
        ww, wh = 440, 44
        self._wc.delete("all")
        col = self.c["wave"]
        gap = ww / len(self._vol_history)   # ~4 px per bar at 110 samples
        bw  = max(1.0, gap * 0.6)           # bar width leaving a small gap between
        for i, val in enumerate(self._vol_history):
            x   = i * gap + gap / 2
            # 0.42 scale (half of original 0.85) keeps peaks from clipping
            amp = max(1, val * wh * 0.42)
            self._wc.create_line(x, wh/2 - amp/2, x, wh/2 + amp/2,
                                 fill=col, width=bw, capstyle="round")
        self._wave_job = self.after(50, self._wave_tick)

    def _stop_recording(self):
        self.is_recording = False
        self._wave_job    = None
        if self._audio_rec: self._audio_rec.stop()
        now = datetime.datetime.now()
        try:    name = now.strftime("Recording %b %-d, %I:%M %p")
        except: name = now.strftime("Recording %b %d, %I:%M %p")
        ts      = now.strftime("%H%M%S")
        wav_dir = _recordings_dir()
        wav_path = os.path.join(wav_dir, f"recording_{now.strftime('%Y%m%d')}_{ts}.wav")
        frames = self._audio_rec.get_frames() if self._audio_rec else []
        transcript = self._transcript_final.strip()
        if frames:
            try:   save_wav(frames, wav_path)
            except Exception as e:
                print(f"Warning: could not save WAV: {e}"); wav_path = None
        else:
            wav_path = None
        self.recordings.append({
            "name": name, "timestamp": now.strftime("%I:%M %p"),
            "wav": wav_path, "transcript": transcript,
        })
        _save_recordings(self.recordings)
        self._audio_rec = None

        if wav_path and SERVER_UPLOAD_ENABLED:
            self._show_toast("Uploading to server…", color="blue")
            def _upload_done(success, msg):
                color = "green" if success else "accent"
                self.after(0, lambda: self._show_toast(msg, color=color, duration=4000))
            upload_to_server(wav_path, local_transcript=transcript,
                             on_done=_upload_done)

        self._show_main()

    def _show_toast(self, message: str, color: str = "blue", duration: int = 3000):
        """Brief banner for neutral / success messages.
        Error messages (color='accent') are routed to a blocking popup instead."""
        if color == "accent":
            self._show_error_popup(message)
            return
        c = self.c
        bg_map = {"blue": c["blue"], "green": c["green"]}
        bg = bg_map.get(color, c["blue"])
        toast = tk.Frame(self, bg=bg, cursor="none")
        toast.place(x=0, y=0, relwidth=1)
        tk.Label(toast, text=message, font=self.F["small"],
                 bg=bg, fg="white", pady=6, cursor="none").pack()
        self.after(duration, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _show_error_popup(self, message: str):
        """Full-screen modal overlay showing an error message with an OK button."""
        c = self.c

        overlay = tk.Frame(self, bg=c["bg"], cursor="none")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)

        # Semi-transparent-feel dark card centred on screen
        card = tk.Frame(overlay, bg=c["surface"], cursor="none")
        card.place(relx=0.5, rely=0.5, anchor="center")

        # Error icon
        tk.Label(card, text="⚠", font=(self.F["title"][0], 32),
                 bg=c["surface"], fg=c["accent"],
                 cursor="none", pady=(16)).pack(pady=(20, 4))

        # Title
        tk.Label(card, text="Upload Failed", font=self.F["bodyb"],
                 bg=c["surface"], fg=c["fg"],
                 cursor="none").pack(padx=28)

        # Message body — wrap at 360 px so it stays inside the card
        tk.Label(card, text=message, font=self.F["small"],
                 bg=c["surface"], fg=c["fg2"],
                 wraplength=340, justify="center",
                 cursor="none").pack(padx=28, pady=(6, 20))

        _line(card, c)

        # OK button
        ok_btn = tk.Label(card, text="OK", font=self.F["bodyb"],
                          bg=c["surface"], fg=c["accent"],
                          padx=0, pady=12, cursor="none")
        ok_btn.pack(fill="x")

        def _dismiss():
            overlay.destroy()

        ok_btn.bind("<ButtonRelease-1>", lambda e: _dismiss() if _tap_ok() else None)
        _hover(ok_btn, c["surface"], c["surface2"])

    # ── Settings ──────────────────────────────────────────────────────────────
    def _show_settings(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_main, title="Settings")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner, _bscroll_inner = make_touch_scroll(outer, c["bg"])

        wrap = tk.Frame(inner, bg=c["bg"], cursor="none")
        wrap.pack(fill="x", padx=28, pady=16)
        card = tk.Frame(wrap, bg=c["surface"], cursor="none")
        card.pack(fill="x")

        def row(icon_name, label, sublabel=None, right_fn=None,
                cmd=None, last=False, fallback="•", icon_size=(28,28)):
            f = tk.Frame(card, bg=c["surface"], cursor="none")
            f.pack(fill="x")
            ic = _png_label(f, icon_name, self.tv, fallback, self.F["im"],
                            c["surface2"], c["fg2"], size=icon_size,
                            padx=10, pady=6)
            ic.pack(side="left", padx=(14,10), pady=10)
            txt = tk.Frame(f, bg=c["surface"], cursor="none")
            txt.pack(side="left", fill="x", expand=True, pady=10)
            tk.Label(txt, text=label, font=self.F["body"],
                     bg=c["surface"], fg=c["fg"], anchor="w",
                     cursor="none").pack(anchor="w")
            if sublabel:
                tk.Label(txt, text=sublabel, font=self.F["small"],
                         bg=c["surface"], fg=c["fg3"], anchor="w",
                         cursor="none").pack(anchor="w")
            if right_fn:
                right_fn(f).pack(side="right", padx=14, pady=10)
            if cmd:
                _row_hover(f, c["surface"], c["surface2"])
                for w in [f, ic, txt]:
                    w.bind("<ButtonRelease-1>", lambda e, fn=cmd: fn() if _tap_ok() else None)
            if not last:
                _line(card, c, padx=54)

        def chev(p):
            return tk.Label(p, text="›", font=self.F["im"],
                            bg=c["surface"], fg=c["fg3"], cursor="none")

        def dark_tog(p):
            f  = tk.Frame(p, bg=c["surface"], cursor="none")
            sv = tk.StringVar(value="Dark" if self.theme=="dark" else "Light")
            tk.Label(f, textvariable=sv, font=self.F["small"],
                     bg=c["surface"], fg=c["fg2"], cursor="none").pack(
                     side="left", padx=(0,8))
            tog = Toggle(f, c, initial=self.theme=="dark", on_color=c["accent"])
            def cb(val):
                self.theme = "dark" if val else "light"
                sv.set("Dark" if val else "Light")
                self._show_settings()
            tog.set_cb(cb); tog.pack(side="left")
            return f

        def lt_tog(p):
            f = tk.Frame(p, bg=c["surface"], cursor="none")
            tog = Toggle(f, c, variable=self.live_transcription,
                         initial=self.live_transcription.get(), on_color=c["green"])
            tog.pack(side="left")
            return f

        def lang_right(p):
            f = tk.Frame(p, bg=c["surface"], cursor="none")
            tk.Label(f, text=self.language.get(), font=self.F["small"],
                     bg=c["surface"], fg=c["fg2"], cursor="none").pack(side="left")
            tk.Label(f, text="›", font=self.F["im"],
                     bg=c["surface"], fg=c["fg3"], cursor="none").pack(side="left")
            return f

        def sounds_tog(p):
            f  = tk.Frame(p, bg=c["surface"], cursor="none")
            sv = tk.StringVar(value="On" if self.system_sounds.get() else "Off")
            tk.Label(f, textvariable=sv, font=self.F["small"],
                     bg=c["surface"], fg=c["fg2"], cursor="none").pack(
                     side="left", padx=(0,8))
            tog = Toggle(f, c, variable=self.system_sounds,
                         initial=self.system_sounds.get(), on_color=c["green"])
            def cb(val):
                self.system_sounds.set(val)
                sv.set("On" if val else "Off")
            tog.set_cb(cb); tog.pack(side="left")
            return f

        _ssid = _wifi_current_ssid()
        _wlabel = ("Network:  " + _ssid) if _ssid else "Connect to Network"
        _wsub   = _ssid if _ssid else "No network connected"
        row("Wifi", _wlabel, _wsub, right_fn=chev,
            cmd=self._show_wifi, fallback="⊕")
        row("DarkMode",   "Display Mode",             right_fn=dark_tog,  fallback="◑")
        row("Transcribe", "Live Transcription",        right_fn=lt_tog,    fallback="✦")
        row("Language",   "Language",
            sublabel=self.language.get(), right_fn=lang_right,
            cmd=self._show_language, fallback="◎")
        row("SoundOn",    "System Sounds",             right_fn=sounds_tog, fallback="◁")
        row("Info",       "About",
            sublabel="App version, device info, and more",
            right_fn=chev, cmd=self._show_about, last=True, fallback="ⓘ")


    # ── WiFi screen ───────────────────────────────────────────────────────────
    def _show_wifi(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="Wi-Fi")
        _line(self.frame, c)

        sbar = tk.Frame(self.frame, bg=c["surface"], height=32, cursor="none")
        sbar.pack(fill="x")
        sbar.pack_propagate(False)
        self._wifi_status = tk.Label(sbar, text="Scanning…",
                                     font=self.F["small"], bg=c["surface"],
                                     fg=c["fg2"], cursor="none")
        self._wifi_status.pack(side="left", padx=14)
        ref = tk.Label(sbar, text="⟳  Refresh", font=self.F["small"],
                       bg=c["surface"], fg=c["blue"], cursor="none")
        ref.pack(side="right", padx=14)
        ref.bind("<ButtonRelease-1>", lambda e: self._show_wifi() if _tap_ok() else None)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        cv, inner, _bscroll_inner = make_touch_scroll(outer, c["bg"])
        self._wifi_inner   = inner
        self._wifi_cv      = cv
        self._wifi_c       = c
        self._wifi_bind_fn = _bscroll_inner

        def _scan():
            current = _wifi_current_ssid()
            nets    = _wifi_scan()
            self.after(0, lambda: self._wifi_render(nets, current))
        threading.Thread(target=_scan, daemon=True).start()

    def _wifi_render(self, nets, current):
        if not (hasattr(self, "_wifi_inner") and self._wifi_inner.winfo_exists()):
            return
        c     = self._wifi_c
        inner = self._wifi_inner

        for w in inner.winfo_children():
            w.destroy()

        if hasattr(self, "_wifi_status") and self._wifi_status.winfo_exists():
            if current:
                self._wifi_status.configure(text="Connected: " + current,
                                            fg=c["green"])
            else:
                self._wifi_status.configure(text="Not connected", fg=c["fg2"])

        pad = tk.Frame(inner, bg=c["bg"], cursor="none")
        pad.pack(fill="x", padx=16, pady=8)

        if current:
            cc = tk.Frame(pad, bg=c["surface"], cursor="none")
            cc.pack(fill="x", pady=(0, 10))

            tk.Label(cc, text="CONNECTED", font=self.F["section"],
                     bg=c["surface"], fg=c["fg3"],
                     anchor="w", padx=14, pady=5,
                     cursor="none").pack(fill="x")
            _line(cc, c)

            row = tk.Frame(cc, bg=c["surface"], cursor="none")
            row.pack(fill="x", padx=14, pady=8)
            tk.Label(row, text="✓", font=self.F["bodyb"],
                     bg=c["surface"], fg=c["green"],
                     cursor="none").pack(side="left", padx=(0, 10))
            tk.Label(row, text=current, font=self.F["bodyb"],
                     bg=c["surface"], fg=c["fg"],
                     cursor="none").pack(side="left")

            dbtn = tk.Label(cc, text="Disconnect",
                            font=self.F["small"],
                            bg=c["accent2"], fg="white",
                            pady=7, cursor="none")
            dbtn.pack(fill="x", padx=14, pady=(0, 10))

            def _do_disconnect(btn=dbtn):
                btn.configure(text="Disconnecting…", bg=c["fg3"])
                def _t():
                    _wifi_disconnect()
                    self.after(0, self._show_wifi)
                threading.Thread(target=_t, daemon=True).start()

            dbtn.bind("<ButtonRelease-1>", lambda e: _do_disconnect() if _tap_ok() else None)

        ac = tk.Frame(pad, bg=c["surface"], cursor="none")
        ac.pack(fill="x")

        tk.Label(ac, text="AVAILABLE NETWORKS", font=self.F["section"],
                 bg=c["surface"], fg=c["fg3"],
                 anchor="w", padx=14, pady=5,
                 cursor="none").pack(fill="x")
        _line(ac, c)

        if not nets:
            tk.Label(ac, text="No networks found. Tap Refresh to scan again.",
                     font=self.F["body"], bg=c["surface"], fg=c["fg2"],
                     wraplength=380, justify="center", pady=16,
                     cursor="none").pack()
            return

        for i, net in enumerate(nets):
            is_cur = net["ssid"] == current
            last   = i == len(nets) - 1

            f = tk.Frame(ac, bg=c["surface"], cursor="none")
            f.pack(fill="x")

            tk.Label(f, text=_signal_bars(net["signal"]),
                     font=self.F["tiny"], bg=c["surface"], fg=c["blue"],
                     padx=8, cursor="none").pack(side="left")

            nf = tk.Frame(f, bg=c["surface"], cursor="none")
            nf.pack(side="left", fill="x", expand=True, pady=9)
            tk.Label(nf, text=net["ssid"],
                     font=self.F["body"],
                     bg=c["surface"],
                     fg=c["green"] if is_cur else c["fg"],
                     anchor="w", cursor="none").pack(anchor="w")
            if net["secured"]:
                tk.Label(nf, text="Secured",
                         font=self.F["tiny"], bg=c["surface"],
                         fg=c["fg3"], anchor="w",
                         cursor="none").pack(anchor="w")

            if is_cur:
                tk.Label(f, text="✓", font=self.F["bodyb"],
                         bg=c["surface"], fg=c["green"],
                         padx=12, cursor="none").pack(side="right")
            else:
                def _tap_net(e, n=net):
                    self._wifi_pick(n)
                _row_hover(f, c["surface"], c["surface2"])
                f.bind("<ButtonRelease-1>", lambda e, n=net: self._wifi_pick(n) if _tap_ok() else None)
                for child in f.winfo_children():
                    child.bind("<ButtonRelease-1>", lambda e, n=net: self._wifi_pick(n) if _tap_ok() else None)

            if not last:
                _line(ac, c, padx=14)

        if hasattr(self, '_wifi_bind_fn') and self._wifi_bind_fn:
            self._wifi_bind_fn(inner)

    def _wifi_pick(self, net):
        if not net["secured"]:
            self._wifi_connect_bg(net["ssid"], "")
            return
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME", "connection", "show"],
                capture_output=True, text=True, timeout=5)
            saved = [l.strip() for l in r.stdout.splitlines()]
            if net["ssid"] in saved:
                self._wifi_connect_bg(net["ssid"], "")
                return
        except Exception:
            pass
        self._show_wifi_password(net)

    def _wifi_connect_bg(self, ssid, password):
        self._show_toast("Connecting to " + ssid + "…",
                         color="blue", duration=6000)
        def _t():
            ok, msg = _wifi_connect(ssid, password)
            col = "green" if ok else "accent"
            self.after(0, lambda: self._show_toast(msg, color=col, duration=4000))
            self.after(0, self._show_wifi)
        threading.Thread(target=_t, daemon=True).start()

    def _show_wifi_password(self, net):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_wifi, title="Password")
        _line(self.frame, c)

        sub = tk.Frame(self.frame, bg=c["surface"], cursor="none")
        sub.pack(fill="x")
        tk.Label(sub,
                 text='Joining network:  ' + net["ssid"],
                 font=self.F["small"], bg=c["surface"], fg=c["fg2"],
                 pady=7, cursor="none").pack()
        _line(self.frame, c)

        self._pw_result = tk.Label(self.frame, text="",
                                   font=self.F["small"],
                                   bg=c["bg"], fg=c["fg2"],
                                   cursor="none")
        self._pw_result.pack(fill="x", padx=14, pady=(3, 0))

        kb_wrap = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        kb_wrap.pack(fill="both", expand=True)

        def _submit(pw):
            self._pw_result.configure(text="Connecting…", fg=c["fg2"])
            self.update_idletasks()
            def _t():
                ok, msg = _wifi_connect(net["ssid"], pw)
                def _done():
                    if ok:
                        self._show_toast(msg, color="green", duration=3000)
                        self._show_wifi()
                    else:
                        if (hasattr(self, "_pw_result")
                                and self._pw_result.winfo_exists()):
                            self._pw_result.configure(
                                text="Failed: " + msg, fg=c["accent"])
                self.after(0, _done)
            threading.Thread(target=_t, daemon=True).start()

        kb = TouchKeyboard(kb_wrap, c, self.F,
                           on_submit=_submit,
                           on_cancel=self._show_wifi,
                           placeholder="Password for " + net["ssid"])
        kb.pack(fill="both", expand=True)


    # ── Language screen ───────────────────────────────────────────────────────
    def _show_language(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="Language")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner, _bscroll_inner = make_touch_scroll(outer, c["bg"])

        wrap = tk.Frame(inner, bg=c["bg"], cursor="none")
        wrap.pack(fill="x", padx=28, pady=16)
        card = tk.Frame(wrap, bg=c["surface"], cursor="none")
        card.pack(fill="x")

        for i, lang in enumerate(LANGUAGES):
            last = i == len(LANGUAGES)-1
            f    = tk.Frame(card, bg=c["surface"], cursor="none")
            f.pack(fill="x")
            lbl  = tk.Label(f, text=lang, font=self.F["body"],
                            bg=c["surface"], fg=c["fg"], anchor="w",
                            padx=18, pady=12, cursor="none")
            lbl.pack(side="left", fill="x", expand=True)
            if lang == self.language.get():
                tk.Label(f, text="✓", font=self.F["im"],
                         bg=c["surface"], fg=c["green"],
                         padx=14, cursor="none").pack(side="right")
            def sel(l=lang):
                self.language.set(l)
                self._show_language()
            _row_hover(f, c["surface"], c["surface2"])
            for w in [f, lbl]:
                w.bind("<ButtonRelease-1>", lambda e, fn=sel: fn() if _tap_ok() else None)
            if not last:
                _line(card, c, padx=18)

    # ── About screen ──────────────────────────────────────────────────────────
    def _show_about(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="About")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner, bind_fn = make_touch_scroll(outer, c["bg"])

        pad = tk.Frame(inner, bg=c["bg"], cursor="none")
        pad.pack(fill="x", padx=20, pady=14)

        id_card = tk.Frame(pad, bg=c["surface"], cursor="none")
        id_card.pack(fill="x", pady=(0, 12))

        app_row = tk.Frame(id_card, bg=c["surface"], cursor="none")
        app_row.pack(fill="x", padx=18, pady=18)

        mic_ic = _png_label(app_row, "Mic", self.tv, "🎙", self.F["il"],
                            c["accent"], "white", size=(44,44), padx=0, pady=0)
        mic_ic.pack(side="left")

        inf = tk.Frame(app_row, bg=c["surface"], cursor="none")
        inf.pack(side="left", padx=14)
        tk.Label(inf, text=APP_NAME, font=self.F["title"],
                 bg=c["surface"], fg=c["fg"], cursor="none").pack(anchor="w")
        tk.Label(inf, text=f"Version {VERSION_STRING}", font=self.F["body"],
                 bg=c["surface"], fg=c["fg2"], cursor="none").pack(anchor="w")

        def _info_row(parent, label, value, last=False):
            f = tk.Frame(parent, bg=c["surface"], cursor="none")
            f.pack(fill="x")
            tk.Label(f, text=label, font=self.F["small"],
                     bg=c["surface"], fg=c["fg3"],
                     anchor="w", padx=18, pady=9, cursor="none").pack(side="left")
            tk.Label(f, text=value, font=self.F["small"],
                     bg=c["surface"], fg=c["fg"],
                     anchor="e", padx=18, cursor="none").pack(side="right")
            if not last:
                _line(parent, c, padx=18)

        import platform, os as _os
        try:
            with open("/proc/device-tree/model") as _f:
                hw = _f.read().strip().rstrip("\x00")
        except Exception:
            hw = platform.machine() or "Unknown"
        try:
            mem_kb = int(open("/proc/meminfo").readline().split()[1])
            mem_str = f"{mem_kb // 1024} MB"
        except Exception:
            mem_str = "Unknown"
        try:
            with open("/proc/uptime") as _f:
                secs = int(float(_f.read().split()[0]))
            h, m = divmod(secs // 60, 60)
            uptime_str = f"{h}h {m}m"
        except Exception:
            uptime_str = "Unknown"

        dev_card = tk.Frame(pad, bg=c["surface"], cursor="none")
        dev_card.pack(fill="x", pady=(0, 12))
        tk.Label(dev_card, text="DEVICE", font=self.F["section"],
                 bg=c["surface"], fg=c["fg3"],
                 anchor="w", padx=18, pady=7, cursor="none").pack(fill="x")
        _line(dev_card, c)
        _info_row(dev_card, "Hardware",  hw)
        _info_row(dev_card, "Memory",    mem_str)
        _info_row(dev_card, "Uptime",    uptime_str)
        _info_row(dev_card, "OS",        platform.system() + " " + platform.release(),
                  last=True)

        dev_c = tk.Frame(pad, bg=c["surface"], cursor="none")
        dev_c.pack(fill="x", pady=(0, 12))
        tk.Label(dev_c, text="DEVELOPERS", font=self.F["section"],
                 bg=c["surface"], fg=c["fg3"],
                 anchor="w", padx=18, pady=7, cursor="none").pack(fill="x")
        _line(dev_c, c)
        for i, dev in enumerate(DEVELOPERS):
            last = i == len(DEVELOPERS) - 1
            f = tk.Frame(dev_c, bg=c["surface"], cursor="none")
            f.pack(fill="x", padx=18, pady=9)
            tk.Label(f, text=dev, font=self.F["body"],
                     bg=c["surface"], fg=c["fg"],
                     anchor="w", cursor="none").pack(side="left")
            if not last:
                _line(dev_c, c, padx=18)

        leg_c = tk.Frame(pad, bg=c["surface"], cursor="none")
        leg_c.pack(fill="x", pady=(0, 12))
        tk.Label(leg_c, text="LEGAL", font=self.F["section"],
                 bg=c["surface"], fg=c["fg3"],
                 anchor="w", padx=18, pady=7, cursor="none").pack(fill="x")
        _line(leg_c, c)

        tos_row = tk.Frame(leg_c, bg=c["surface"], cursor="none")
        tos_row.pack(fill="x")
        tk.Label(tos_row, text="Terms of Service", font=self.F["body"],
                 bg=c["surface"], fg=c["fg"],
                 anchor="w", padx=18, pady=12, cursor="none").pack(side="left", fill="x", expand=True)
        tk.Label(tos_row, text="›", font=self.F["im"],
                 bg=c["surface"], fg=c["fg3"],
                 padx=14, cursor="none").pack(side="right")
        _row_hover(tos_row, c["surface"], c["surface2"])
        tos_row.bind("<ButtonRelease-1>",
                     lambda e: self._show_tos() if _tap_ok() else None)
        for ch in tos_row.winfo_children():
            ch.bind("<ButtonRelease-1>",
                    lambda e: self._show_tos() if _tap_ok() else None)

        bind_fn(inner)

    # ── Terms of Service screen ───────────────────────────────────────────────
    def _show_tos(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_about, title="Terms of Service")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner, bind_fn = make_touch_scroll(outer, c["bg"])

        pad = tk.Frame(inner, bg=c["bg"], cursor="none")
        pad.pack(fill="x", padx=20, pady=14)

        tos_text = ""
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            tos_path = TOS_FILE if os.path.isabs(TOS_FILE) \
                       else os.path.join(here, TOS_FILE)
            with open(tos_path, encoding="utf-8") as f:
                tos_text = f.read().strip()
        except FileNotFoundError:
            tos_text = (
                f"{APP_NAME} Terms of Service\n\n"
                "No terms of service file found.\n"
                f"Place a file named '{TOS_FILE}' in the same folder as\n"
                "this application to display your terms here."
            )
        except Exception as ex:
            tos_text = f"Could not load terms of service:\n{ex}"

        card = tk.Frame(pad, bg=c["surface"], cursor="none")
        card.pack(fill="x")
        tk.Label(card, text=tos_text,
                 font=self.F["small"], bg=c["surface"], fg=c["fg"],
                 anchor="nw", justify="left",
                 wraplength=420, padx=18, pady=16,
                 cursor="none").pack(fill="x")

        bind_fn(inner)


if __name__ == "__main__":
    app = App()
    app.mainloop()
