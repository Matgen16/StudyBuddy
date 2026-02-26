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
VERSION_MINOR    = 4
VERSION_REVISION = 1
VERSION_STRING   = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_REVISION}"

APP_NAME   = "Voice Recorder"
DEVELOPERS = ["Matthew Gentle", "Ryan Parker"]
TOS_FILE   = "tos.txt"

VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"
ICONS_DIR       = "icons"   # folder next to this script containing the PNGs

SAMPLE_RATE  = 16000
CHUNK_SIZE   = 4000
CHANNELS     = 1
AUDIO_FORMAT = None

import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont
import datetime, os, math, wave, struct, json, queue, threading

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

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

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
    return ICONS_DIR if os.path.isabs(ICONS_DIR) else os.path.join(here, ICONS_DIR)

def load_icon(name: str, variant: str, size=None):
    """
    Try to load  <ICONS_DIR>/<name><variant>.png  (e.g. MicDark.png).
    Falls back to  <name>.png  if the themed file is absent.
    Returns a PhotoImage or None.
    """
    if not PIL_OK:
        return None
    key = (name, variant, size)
    if key in _icon_cache:
        return _icon_cache[key]
    d = _icons_dir()
    for filename in (f"{name}{variant}.png", f"{name}.png"):
        path = os.path.join(d, filename)
        if os.path.exists(path):
            try:
                img = Image.open(path).convert("RGBA")
                if size:
                    img = img.resize(size, Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                _icon_cache[key] = photo
                return photo
            except Exception as exc:
                print(f"Warning: could not load icon {path}: {exc}")
    _icon_cache[key] = None
    return None

# ── Drawing helpers ────────────────────────────────────────────────────────────
def _hover(w, c1, c2):
    w.bind("<Enter>", lambda e: w.configure(bg=c2))
    w.bind("<Leave>", lambda e: w.configure(bg=c1))

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

# ── Touch-drag scrollable area (no scrollbar) ──────────────────────────────────
def make_touch_scroll(parent, bg):
    """
    Returns (canvas, inner_frame).
    inner_frame is where you add content.
    Scrolling is done via touch/mouse drag — no visible scrollbar.
    """
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0, cursor="none")
    canvas.pack(fill="both", expand=True)

    inner = tk.Frame(canvas, bg=bg, cursor="none")
    win   = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _resize(e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(win, width=canvas.winfo_width())

    inner.bind("<Configure>", _resize)
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

    # Drag-to-scroll state
    _drag = {"y": None, "active": False}

    def _press(e):
        _drag["y"] = e.y_root
        _drag["active"] = False

    def _motion(e):
        if _drag["y"] is None:
            return
        dy = _drag["y"] - e.y_root
        if abs(dy) > 5:
            _drag["active"] = True
        if _drag["active"]:
            canvas.yview_scroll(int(dy / 3), "units")
            _drag["y"] = e.y_root

    def _release(e):
        _drag["y"] = None
        _drag["active"] = False

    def _bind_all(widget):
        widget.bind("<ButtonPress-1>",   _press,   add="+")
        widget.bind("<B1-Motion>",       _motion,  add="+")
        widget.bind("<ButtonRelease-1>", _release, add="+")
        for ch in widget.winfo_children():
            _bind_all(ch)

    canvas.bind("<ButtonPress-1>",   _press)
    canvas.bind("<B1-Motion>",       _motion)
    canvas.bind("<ButtonRelease-1>", _release)

    # Bind newly added children as they appear
    inner.bind("<Map>", lambda e: _bind_all(inner))

    return canvas, inner

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
        self.bind("<Button-1>", self._toggle)
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
    photo = load_icon(icon_name, variant, size=size)
    if photo:
        lbl = tk.Label(parent, image=photo, bg=bg, cursor="none", **kw)
        lbl.image = photo
    else:
        lbl = tk.Label(parent, text=fallback, font=font, bg=bg, fg=fg,
                       cursor="none", **kw)
    return lbl

# ══════════════════════════════════════════════════════════════════════════════
class AudioRecorder:
    """Captures mic audio, feeds Vosk, pushes results into thread-safe queues."""

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

    def get_frames(self): return list(self._frames)

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception: break
            self._frames.append(data)
            samples = struct.unpack(f"{len(data)//2}h", data)
            rms = math.sqrt(sum(s*s for s in samples)/len(samples))/32768
            try: self._lq.put_nowait(min(1.0, rms*6))
            except queue.Full: pass
            if self._rec.AcceptWaveform(data):
                text = json.loads(self._rec.Result()).get("text","").strip()
                if text:
                    try: self._tq.put_nowait({"final": text})
                    except queue.Full: pass
            else:
                text = json.loads(self._rec.PartialResult()).get("partial","").strip()
                if text:
                    try: self._tq.put_nowait({"partial": text})
                    except queue.Full: pass
        if self._rec:
            text = json.loads(self._rec.FinalResult()).get("text","").strip()
            if text:
                try: self._tq.put_nowait({"final": text})
                except queue.Full: pass


def save_wav(frames, path):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE); wf.writeframes(b"".join(frames))

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
        self.config(cursor="none")   # hide cursor at root level

        self.theme              = "dark"
        self.language           = tk.StringVar(value="English (US)")
        self.live_transcription = tk.BooleanVar(value=True)
        self.system_sounds      = tk.BooleanVar(value=True)
        self.is_recording       = False
        self.recordings         = []

        self._wave_job    = None
        self._mic_level   = 0.0
        self._vol_history = [0.0] * 40
        self._transcript_final   = ""
        self._transcript_partial = ""
        self._tq = queue.Queue(maxsize=50)
        self._lq = queue.Queue(maxsize=10)
        self._audio_rec = None

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
    def tv(self): return self.c["variant"]   # "Dark" or "Light"

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
            "clock":   (body, 15),
            "date":    (body, 15),
            "title":   (body, 20, "bold"),
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
        self._time_str = now.strftime("%H:%M")
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
        bar = tk.Frame(parent, bg=c["bg"], cursor="none")
        bar.pack(fill="x", padx=20, pady=(14,0))

        left = tk.Frame(bar, bg=c["bg"], cursor="none")
        left.pack(side="left", fill="y")

        if back:
            b = _png_label(left, "BackArrow", self.tv, "←", self.F["il"],
                           c["surface"], c["fg"], size=(20,20), padx=10, pady=2)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e: back())
            _hover(b, c["surface"], c["surface2"])
            if title:
                tk.Label(left, text=title, font=self.F["title"],
                         bg=c["bg"], fg=c["fg"], padx=12,
                         cursor="none").pack(side="left")
        else:
            self._clk = tk.Label(left, text=getattr(self,"_time_str",""),
                                 font=self.F["clock"], bg=c["bg"], fg=c["fg"],
                                 cursor="none")
            self._clk.pack(side="left")

        right = tk.Frame(bar, bg=c["bg"], cursor="none")
        right.pack(side="right", fill="y")

        if not back:
            self._dat = tk.Label(right, text=getattr(self,"_date_str",""),
                                 font=self.F["date"], bg=c["bg"], fg=c["fg2"],
                                 cursor="none")
            self._dat.pack(side="left", padx=(0,12))

        if actions:
            up = _png_label(right, "Upload", self.tv, "↑", self.F["il"],
                            c["bg"], c["fg3"], size=(18,18), padx=4)
            up.pack(side="left")

            sg = _png_label(right, "SettingsIcon", self.tv, "⚙", self.F["il"],
                            c["bg"], c["fg"], size=(18,18), padx=4)
            sg.pack(side="left")
            sg.bind("<Button-1>", lambda e: self._show_settings())
            _hover(sg, c["bg"], c["surface2"])

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
        self._main_cv.bind("<Button-1>", lambda e: self._start_recording())

        bot = tk.Frame(self.frame, bg=c["surface"], height=56, cursor="none")
        bot.pack(fill="x", side="bottom")
        bot.pack_propagate(False)
        _line_top(bot, c)

        if self.recordings:
            rec = self.recordings[-1]
            row = tk.Frame(bot, bg=c["surface"], cursor="none")
            row.pack(fill="x", padx=20, pady=10)
            mic_ic = _png_label(row, "Mic", self.tv, "🎙", self.F["im"],
                                c["surface2"], c["fg2"], size=(16,16), padx=6, pady=2)
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
        photo = load_icon("Mic", self.tv, size=(52,52))
        if photo:
            cv.create_image(sz//2, sz//2, image=photo, anchor="center")
            cv._mic_ref = photo
        else:
            cv.create_text(sz//2, sz//2, text="🎙",
                           font=self.F["il"], fill="white")

    # ── Recording screen ──────────────────────────────────────────────────────
    def _start_recording(self):
        self._transcript_final = self._transcript_partial = ""
        self._vol_history = [0.0]*40
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

        # Transcript panel pinned to bottom
        trans_bar = tk.Frame(self.frame, bg=c["surface"], height=180, cursor="none")
        trans_bar.pack(side="bottom", fill="x")
        trans_bar.pack_propagate(False)
        _line_top(trans_bar, c)
        self._lt = tk.Label(trans_bar, text="Listening…",
                            font=self.F["trans"], bg=c["surface"], fg=c["fg2"],
                            wraplength=700, justify="center", cursor="none")
        self._lt.pack(expand=True, padx=20, pady=8)

        mid = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        mid.pack(fill="both", expand=True)

        # Stop button
        ssz = 110
        self._stop_cv = tk.Canvas(mid, width=ssz, height=ssz,
                                  bg=c["bg"], highlightthickness=0, cursor="none")
        self._stop_cv.pack(pady=(28,4))
        _rrect(self._stop_cv, 6, 6, ssz-6, ssz-6, 20, c["accent"])
        stop_ph = load_icon("Stop", self.tv, size=(36,36))
        if stop_ph:
            self._stop_cv.create_image(ssz//2, ssz//2, image=stop_ph, anchor="center")
            self._stop_cv._stop_ref = stop_ph
        else:
            sq = 28
            self._stop_cv.create_rectangle(
                ssz//2-sq//2, ssz//2-sq//2, ssz//2+sq//2, ssz//2+sq//2,
                fill="white", outline="")
        self._stop_cv.bind("<Button-1>", lambda e: self._stop_recording())

        tk.Label(mid, text="Recording", font=self.F["bodyb"],
                 bg=c["bg"], fg=c["fg"], cursor="none").pack(pady=(0,10))

        self._wc = tk.Canvas(mid, width=640, height=70,
                             bg=c["bg"], highlightthickness=0, cursor="none")
        self._wc.pack(pady=(0,8))
        self._mic_level = 0.0
        self._wave_tick()
        self._poll_transcript()

    def _poll_transcript(self):
        if not self.is_recording: return
        while True:
            try:    self._mic_level = self._lq.get_nowait()
            except queue.Empty: break
        while True:
            try:    msg = self._tq.get_nowait()
            except queue.Empty: break
            if "final" in msg:
                self._transcript_final += (" " if self._transcript_final else "") + msg["final"]
                self._transcript_partial = ""
            elif "partial" in msg:
                self._transcript_partial = msg["partial"]
        if hasattr(self,"_lt") and self._lt.winfo_exists():
            d = self._transcript_final
            if self._transcript_partial:
                d += (" " if d else "") + self._transcript_partial + "…"
            self._lt.configure(text=d if d else "Listening…")
        self.after(80, self._poll_transcript)

    def _wave_tick(self):
        if not (hasattr(self,"_wc") and self._wc.winfo_exists()): return
        if not self.is_recording: return
        self._vol_history.append(self._mic_level)
        if len(self._vol_history) > 40: self._vol_history.pop(0)
        ww, wh = 640, 70
        self._wc.delete("all")
        col = self.c["wave"]
        gap = ww / len(self._vol_history)
        bw  = gap * 0.7
        for i, val in enumerate(self._vol_history):
            x   = i*gap + gap/2
            amp = max(2, val*wh*0.9)
            self._wc.create_line(x, wh/2-amp/2, x, wh/2+amp/2,
                                 fill=col, width=bw, capstyle="round")
        self._wave_job = self.after(50, self._wave_tick)

    def _stop_recording(self):
        self.is_recording = False
        self._wave_job    = None
        if self._audio_rec: self._audio_rec.stop()
        now = datetime.datetime.now()
        try:    name = now.strftime("Recording %b %-d, %I:%M %p")
        except: name = now.strftime("Recording %b %d, %I:%M %p")
        ts   = now.strftime("%H%M%S")
        here = os.path.dirname(os.path.abspath(__file__))
        wav_path = os.path.join(here, f"recording_{now.strftime('%Y%m%d')}_{ts}.wav")
        frames = self._audio_rec.get_frames() if self._audio_rec else []
        if frames:
            try:   save_wav(frames, wav_path)
            except Exception as e:
                print(f"Warning: could not save WAV: {e}"); wav_path = None
        else:
            wav_path = None
        self.recordings.append({
            "name": name, "timestamp": now.strftime("%H:%M"),
            "wav": wav_path, "transcript": self._transcript_final.strip(),
        })
        self._audio_rec = None
        self._show_main()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _show_settings(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_main, title="Settings")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner = make_touch_scroll(outer, c["bg"])

        wrap = tk.Frame(inner, bg=c["bg"], cursor="none")
        wrap.pack(fill="x", padx=28, pady=16)
        card = tk.Frame(wrap, bg=c["surface"], cursor="none")
        card.pack(fill="x")

        # ── Settings row factory ─────────────────────────────────────────────
        def row(icon_name, label, sublabel=None, right_fn=None,
                cmd=None, last=False, fallback="•", icon_size=(18,18)):
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
                    w.bind("<Button-1>", lambda e, fn=cmd: fn())
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
            # Use SoundOn or SoundOff icon based on current state
            sound_icon = "SoundOn" if self.system_sounds.get() else "SoundOff"
            tog = Toggle(f, c, variable=self.system_sounds,
                         initial=self.system_sounds.get(), on_color=c["green"])
            def cb(val):
                self.system_sounds.set(val)
                sv.set("On" if val else "Off")
            tog.set_cb(cb); tog.pack(side="left")
            return f

        # Use icon names matching filenames (without Dark/Light suffix)
        row("Bluetooth",  "Connect to New Device",
            "Connect to a new device via Bluetooth", right_fn=chev, fallback="⊕")
        row("DarkMode",   "Display Mode",             right_fn=dark_tog,  fallback="◑")
        row("Transcribe", "Live Transcription",        right_fn=lt_tog,    fallback="✦")
        row("Language",   "Language",
            sublabel=self.language.get(), right_fn=lang_right,
            cmd=self._show_language, fallback="◎")
        row("SoundOn",    "System Sounds",             right_fn=sounds_tog, fallback="◁")
        row("Info",       "About",
            sublabel="App version, device info, and more",
            right_fn=chev, cmd=self._show_about, last=True, fallback="ⓘ")

    # ── Language screen ───────────────────────────────────────────────────────
    def _show_language(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="Language")
        _line(self.frame, c)

        outer = tk.Frame(self.frame, bg=c["bg"], cursor="none")
        outer.pack(fill="both", expand=True)
        canvas, inner = make_touch_scroll(outer, c["bg"])

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
                w.bind("<Button-1>", lambda e, fn=sel: fn())
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
        canvas, inner = make_touch_scroll(outer, c["bg"])

        pad = tk.Frame(inner, bg=c["bg"], cursor="none")
        pad.pack(fill="x", padx=28, pady=16)

        id_c = tk.Frame(pad, bg=c["surface"], cursor="none")
        id_c.pack(fill="x", pady=(0,14))
        row  = tk.Frame(id_c, bg=c["surface"], cursor="none")
        row.pack(fill="x", padx=18, pady=16)

        mic_ic = _png_label(row, "Mic", self.tv, "🎙", self.F["il"],
                            c["accent"], "white", size=(20,20), padx=10, pady=6)
        mic_ic.pack(side="left")

        inf = tk.Frame(row, bg=c["surface"], cursor="none")
        inf.pack(side="left", padx=16)
        tk.Label(inf, text=APP_NAME, font=self.F["bodyb"],
                 bg=c["surface"], fg=c["fg"], cursor="none").pack(anchor="w")
        tk.Label(inf, text=f"Version {VERSION_STRING}", font=self.F["small"],
                 bg=c["surface"], fg=c["fg2"], cursor="none").pack(anchor="w")

        dc = tk.Frame(pad, bg=c["surface"], cursor="none")
        dc.pack(fill="x", pady=(0,14))
        tk.Label(dc, text="DEVELOPERS", font=self.F["section"],
                 bg=c["surface"], fg=c["fg3"], anchor="w",
                 padx=18, pady=10, cursor="none").pack(fill="x")
        _line(dc, c)
        for i, dev in enumerate(DEVELOPERS):
            tk.Label(dc, text=dev, font=self.F["body"],
                     bg=c["surface"], fg=c["fg"], anchor="w",
                     padx=18, pady=10, cursor="none").pack(fill="x")
            if i < len(DEVELOPERS)-1:
                _line(dc, c, padx=18)


if __name__ == "__main__":
    app = App()
    app.mainloop()
