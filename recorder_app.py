"""
Voice Recorder
==============
Versioning: MAJOR.MINOR.REVISION
  MAJOR    — complete overhaul or large new feature set
  MINOR    — new small features or notable improvements
  REVISION — bug fixes and small tweaks

*** Update ALL three version variables on every single change. ***

Dependencies (install once):
    pip install vosk pyaudio

Vosk model (download once, place folder next to this script):
    Small English model (~40 MB):
    https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    Extract so the folder is:  vosk-model-small-en-us-0.15/
    Or set VOSK_MODEL_PATH below to any other Vosk model folder.
"""

VERSION_MAJOR    = 1
VERSION_MINOR    = 3
VERSION_REVISION = 0
VERSION_STRING   = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_REVISION}"

APP_NAME   = "Voice Recorder"
DEVELOPERS = ["Matthew Gentle", "Ryan Parker"]
TOS_FILE   = "tos.txt"

# Path to your Vosk model folder (relative to this script, or absolute).
VOSK_MODEL_PATH = "vosk-model-small-en-us-0.15"

# Audio settings — 16 kHz mono is what Vosk expects.
SAMPLE_RATE  = 16000
CHUNK_SIZE   = 4000
CHANNELS     = 1
AUDIO_FORMAT = None  # set at runtime after importing pyaudio

import tkinter as tk
from tkinter import scrolledtext, messagebox
import tkinter.font as tkfont
import datetime, os, platform, math, wave, struct, json, queue, threading

# ── Optional imports — graceful degradation if not installed ──────────────────
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

# ── Theme palettes ─────────────────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg":        "#1e1e20",
        "surface":   "#2a2a2d",
        "surface2":  "#3a3a3e",
        "fg":        "#f5f5f7",
        "fg2":       "#a1a1a6",
        "fg3":       "#58585e",
        "accent":    "#c0392b",
        "accent2":   "#992020",
        "green":     "#30d158",
        "blue":      "#3a8ef6",
        "sep":       "#3a3a3e",
        "wave":      "#3a8ef6",
        "tog_off":   "#48484e",
    },
    "light": {
        "bg":        "#f0f0f5",
        "surface":   "#ffffff",
        "surface2":  "#e8e8ee",
        "fg":        "#1c1c1e",
        "fg2":       "#6e6e73",
        "fg3":       "#aeaeb2",
        "accent":    "#c0392b",
        "accent2":   "#992020",
        "green":     "#28a745",
        "blue":      "#1a6ed8",
        "sep":       "#d8d8de",
        "wave":      "#1a6ed8",
        "tog_off":   "#bbbbc2",
    },
}

LANGUAGES = [
    "English (US)", "English (UK)", "Español", "Français",
    "Deutsch", "Português", "Italiano", "日本語",
    "中文 (简体)", "한국어", "Русский", "العربية",
]

ICONS = {
    "mic":        ("\ue029", "🎙"),
    "stop":       ("\ue047", "⏹"),
    "settings":   ("\ue8b8", "⚙"),
    "bluetooth":  ("\ue1a7", "⊕"),
    "brightness": ("\ue3a6", "◑"),
    "chat":       ("\ue0b7", "✦"),
    "language":   ("\ue894", "◎"),
    "volume":     ("\ue050", "◁"),
    "info":       ("\ue88e", "ⓘ"),
    "back":       ("\ue5c4", "←"),
    "chevron":    ("\ue5cf", "›"),
    "check":      ("\ue876", "✓"),
    "upload":     ("\uf09b", "↑"),
}


def get_icon(name):
    families = tkfont.families()
    has_mat = any("Material Symbols" in f or "Material Icons" in f for f in families)
    return ICONS[name][0] if has_mat else ICONS[name][1]


def _hover(w, c1, c2):
    w.bind("<Enter>", lambda e: w.configure(bg=c2))
    w.bind("<Leave>", lambda e: w.configure(bg=c1))


def _row_hover(f, c1, c2):
    def _in(e):
        f.configure(bg=c2)
        for c in f.winfo_children():
            if c.winfo_class() == "Frame":
                _row_hover(c, c1, c2)
            else:
                c.configure(bg=c2)

    def _out(e):
        f.configure(bg=c1)
        for c in f.winfo_children():
            if c.winfo_class() == "Frame":
                pass
            else:
                c.configure(bg=c1)

    f.bind("<Enter>", _in)
    f.bind("<Leave>", _out)


def _line(p, c, padx=0):
    f = tk.Frame(p, bg=c["sep"], height=1)
    f.pack(fill="x", padx=padx)


def _line_top(p, c):
    f = tk.Frame(p, bg=c["sep"], height=1)
    f.place(x=0, y=0, relwidth=1)


def _rrect(cv, x1, y1, x2, y2, r, fill):
    d = 2 * r
    cv.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, fill=fill, outline="")
    cv.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, fill=fill, outline="")
    cv.create_arc(
        x1, y2 - d, x1 + d, y2, start=180, extent=90, fill=fill, outline=""
    )
    cv.create_arc(
        x2 - d, y2 - d, x2, y2, start=270, extent=90, fill=fill, outline=""
    )
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")


def _lerp_color(c1, c2, t):
    def _parse(c):
        return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)

    r1, g1, b1 = _parse(c1)
    r2, g2, b2 = _parse(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Custom Toggle Switch ──────────────────────────────────────────────────────
class Toggle(tk.Canvas):
    def __init__(self, parent, c, variable=None, initial=False, on_color=None):
        super().__init__(
            parent, width=42, height=24, bg=parent["bg"], highlightthickness=0
        )
        self.c = c
        self.on_color = on_color or c["accent"]
        self.state = initial
        self.var = variable
        self.cb = None
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def set_cb(self, cb):
        self.cb = cb

    def _toggle(self, e):
        self.state = not self.state
        if self.var:
            self.var.set(self.state)
        if self.cb:
            self.cb(self.state)
        self._draw()

    def _draw(self):
        self.delete("all")
        bg = self.on_color if self.state else self.c["tog_off"]
        _rrect(self, 2, 2, 40, 22, 10, bg)
        x = 29 if self.state else 13
        self.create_oval(x - 8, 12 - 8, x + 8, 12 + 8, fill="white", outline="")


# ════════════════════════════════════════════════════════════════════════════════
class AudioRecorder:
# ════════════════════════════════════════════════════════════════════════════════
    """Runs on a background thread. Captures mic audio, feeds Vosk, and pushes
    transcription text + raw PCM frames into thread-safe queues for the UI."""

    def __init__(self, transcript_queue: queue.Queue, level_queue: queue.Queue):
        self._tq    = transcript_queue   # receives {"partial": str} or {"text": str}
        self._lq    = level_queue        # receives float 0-1 amplitude level
        self._stop  = threading.Event()
        self._thread = None
        self._frames = []                # raw PCM frames for WAV saving
        self._model  = None
        self._rec    = None
        self._pa     = None
        self._stream = None
        self.error   = None              # set if initialisation fails

    def _load_model(self):
        if not VOSK_OK:
            self.error = "vosk not installed.\nRun: pip install vosk"
            return False
        if not PYAUDIO_OK:
            self.error = "pyaudio not installed.\nRun: pip install pyaudio"
            return False
        here = os.path.dirname(os.path.abspath(__file__))
        model_path = VOSK_MODEL_PATH if os.path.isabs(VOSK_MODEL_PATH) \
                     else os.path.join(here, VOSK_MODEL_PATH)
        if not os.path.isdir(model_path):
            self.error = (
                f"Vosk model not found at:\n{model_path}\n\n"
                "Download a model from https://alphacephei.com/vosk/models\n"
                "and extract it next to this script."
            )
            return False
        try:
            self._model = Model(model_path)
            self._rec   = KaldiRecognizer(self._model, SAMPLE_RATE)
            self._rec.SetWords(True)
        except Exception as e:
            self.error = f"Failed to load Vosk model:\n{e}"
            return False
        return True

    def start(self):
        if not self._load_model():
            return False
        try:
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            self.error = f"Could not open microphone:\n{e}"
            return False
        self._frames = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
        self._stream = None
        self._pa     = None

    def get_frames(self):
        return list(self._frames)

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                break

            self._frames.append(data)

            # Compute RMS amplitude for waveform visualisation
            samples = struct.unpack(f"{len(data)//2}h", data)
            rms = math.sqrt(sum(s*s for s in samples) / len(samples)) / 32768
            try:
                self._lq.put_nowait(min(1.0, rms * 6))
            except queue.Full:
                pass

            # Feed Vosk
            if self._rec.AcceptWaveform(data):
                result = json.loads(self._rec.Result())
                text = result.get("text", "").strip()
                if text:
                    try:
                        self._tq.put_nowait({"final": text})
                    except queue.Full:
                        pass
            else:
                partial = json.loads(self._rec.PartialResult())
                text = partial.get("partial", "").strip()
                if text:
                    try:
                        self._tq.put_nowait({"partial": text})
                    except queue.Full:
                        pass

        # Flush final result
        if self._rec:
            result = json.loads(self._rec.FinalResult())
            text = result.get("text", "").strip()
            if text:
                try:
                    self._tq.put_nowait({"final": text})
                except queue.Full:
                    pass


def save_wav(frames, path):
    """Write raw PCM frames to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))


# ════════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
# ════════════════════════════════════════════════════════════════════════════════

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x540")
        self.minsize(700, 420)
        self.resizable(True, True)

        self.theme              = "dark"
        self.language           = tk.StringVar(value="English (US)")
        self.live_transcription = tk.BooleanVar(value=True)
        self.system_sounds      = tk.BooleanVar(value=True)
        self.is_recording       = False
        self.recordings         = []

        # Waveform state
        self._wave_idx  = 0
        self._wave_job  = None
        self._mic_level = 0.0          # live amplitude from mic thread
        self._vol_history = [0.0] * 40 # Store 40 volume points

        # Transcript accumulation
        self._transcript_final   = ""  # committed sentences
        self._transcript_partial = ""  # in-progress word

        # Thread-safe queues filled by AudioRecorder
        self._tq = queue.Queue(maxsize=50)
        self._lq = queue.Queue(maxsize=10)
        self._audio_rec = None

        self._init_fonts()
        self.configure(bg=self.c["bg"])
        self.frame = tk.Frame(self, bg=self.c["bg"])
        self.frame.pack(fill="both", expand=True)

        self._show_main()
        self._clock_tick()

    @property
    def c(self):
        return THEMES[self.theme]

    def _init_fonts(self):
        av = list(tkfont.families())
        def pick(*ns):
            for n in ns:
                if n in av: return n
            return "TkDefaultFont"
        body = pick("Outfit","Nunito","Poppins","SF Pro Display",
                    "Helvetica Neue","Segoe UI","Ubuntu","Helvetica")
        sym  = pick("Material Symbols Rounded","Material Icons",
                    "Material Icons Round","Segoe MDL2 Assets")
        self._sym = sym
        self.F = {
            "clock":   (body, 36),
            "date":    (body, 15),
            "title":   (body, 20, "bold"),
            "body":    (body, 13),
            "bodyb":   (body, 13, "bold"),
            "small":   (body, 11),
            "tiny":    (body, 9),
            "section": (body, 9, "bold"),
            "btn":     (body, 14, "bold"),
            "trans":   (body, 12),
            "ix":      (sym, 40),
            "il":      (sym, 24),
            "im":      (sym, 19),
            "is":      (sym, 15),
        }

    def _clear(self, cancel_wave=True):
        if cancel_wave:
            self._wave_job = None
        for w in self.frame.winfo_children():
            w.destroy()
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
                lbl.configure(text=self._time_str if attr == "_clk" else self._date_str)
        self.after(1000, self._clock_tick)

    def _header(self, parent, back=None, title=None, actions=False):
        c = self.c
        bar = tk.Frame(parent, bg=c["bg"])
        bar.pack(fill="x", padx=20, pady=(14, 0))
        left = tk.Frame(bar, bg=c["bg"])
        left.pack(side="left", fill="y")
        if back:
            b = tk.Label(left, text=get_icon("back"), font=self.F["il"],
                         bg=c["surface"], fg=c["fg"], cursor="hand2",
                         padx=10, pady=2)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e: back())
            _hover(b, c["surface"], c["surface2"])
            if title:
                tk.Label(left, text=title, font=self.F["title"],
                         bg=c["bg"], fg=c["fg"], padx=12).pack(side="left")
        else:
            self._clk = tk.Label(left, text=getattr(self, "_time_str", ""),
                                 font=self.F["clock"], bg=c["bg"], fg=c["fg"])
            self._clk.pack(side="left")
        right = tk.Frame(bar, bg=c["bg"])
        right.pack(side="right", fill="y")
        if not back:
            self._dat = tk.Label(right, text=getattr(self, "_date_str", ""),
                                 font=self.F["date"], bg=c["bg"], fg=c["fg2"])
            self._dat.pack(side="left", padx=(0, 12))
        if actions:
            tk.Label(right, text=get_icon("upload"), font=self.F["il"],
                     bg=c["bg"], fg=c["fg3"]).pack(side="left", padx=4)
            g = tk.Label(right, text=get_icon("settings"), font=self.F["il"],
                         bg=c["bg"], fg=c["fg"], cursor="hand2", padx=4)
            g.pack(side="left")
            g.bind("<Button-1>", lambda e: self._show_settings())
            _hover(g, c["bg"], c["surface2"])

    # ── Main screen ───────────────────────────────────────────────────────────
    def _show_main(self):
        self._clear(cancel_wave=False)
        c = self.c
        self._header(self.frame, actions=True)
        _line(self.frame, c)
        center = tk.Frame(self.frame, bg=c["bg"])
        center.pack(fill="both", expand=True)
        sz = 180
        self._main_cv = tk.Canvas(center, width=sz, height=sz,
                                  bg=c["bg"], highlightthickness=0)
        self._main_cv.place(relx=0.5, rely=0.44, anchor="center")
        self._draw_record_btn(self._main_cv, sz)
        self._main_cv.bind("<Button-1>", lambda e: self._start_recording())
        self._main_cv.configure(cursor="hand2")
        bot = tk.Frame(self.frame, bg=c["surface"], height=56)
        bot.pack(fill="x", side="bottom")
        bot.pack_propagate(False)
        _line_top(bot, c)
        if self.recordings:
            rec = self.recordings[-1]
            row = tk.Frame(bot, bg=c["surface"])
            row.pack(fill="x", padx=20, pady=10)
            tk.Label(row, text=get_icon("mic"), font=self.F["im"],
                     bg=c["surface2"], fg=c["fg2"], padx=6, pady=2).pack(side="left")
            inf = tk.Frame(row, bg=c["surface"])
            inf.pack(side="left", padx=10)
            tk.Label(inf, text=rec["name"], font=self.F["bodyb"],
                     bg=c["surface"], fg=c["fg"]).pack(anchor="w")
            tk.Label(inf, text=rec["timestamp"], font=self.F["small"],
                     bg=c["surface"], fg=c["fg2"]).pack(anchor="w")
        else:
            tk.Label(bot, text="No recordings yet", font=self.F["body"],
                     bg=c["surface"], fg=c["fg3"]).pack(expand=True)

    def _draw_record_btn(self, cv, sz):
        cv.delete("all")
        c   = self.c
        pad = 6
        glow = _lerp_color(c["accent"], c["bg"], 0.65)
        cv.create_oval(pad-4, pad-4, sz-pad+4, sz-pad+4,
                        fill="", outline=glow, width=6)
        cv.create_oval(pad, pad, sz-pad, sz-pad, fill=c["accent"], outline="")
        cv.create_text(sz//2, sz//2 - 16, text=get_icon("mic"),
                        font=self.F["ix"], fill="white")
        cv.create_text(sz//2, sz//2 + 46, text="Start Recording",
                        font=self.F["btn"], fill="white")

    # ── Recording screen ──────────────────────────────────────────────────────
    def _start_recording(self):
        # Reset transcript state
        self._transcript_final   = ""
        self._transcript_partial = ""
        self._vol_history = [0.0] * 40 # Clear history for new recording
        while not self._tq.empty():
            try: self._tq.get_nowait()
            except queue.Empty: break
        while not self._lq.empty():
            try: self._lq.get_nowait()
            except queue.Empty: break

        # Start audio recorder
        self._audio_rec = AudioRecorder(self._tq, self._lq)
        ok = self._audio_rec.start()
        if not ok:
            messagebox.showerror(
                "Microphone Error",
                self._audio_rec.error or "Unknown error starting recorder."
            )
            return

        self.is_recording = True
        self._clear(cancel_wave=False)
        c = self.c
        self._header(self.frame)
        _line(self.frame, c)

        # Transcript bar pinned to the bottom
        trans_bar = tk.Frame(self.frame, bg=c["surface"], height=180)
        trans_bar.pack(side="bottom", fill="x")
        trans_bar.pack_propagate(False)
        _line_top(trans_bar, c)
        self._lt = tk.Label(trans_bar, text="Listening…",
                            font=self.F["trans"], bg=c["surface"], fg=c["fg2"],
                            wraplength=700, justify="center")
        self._lt.pack(expand=True, padx=20, pady=8)

        # Centre area
        mid = tk.Frame(self.frame, bg=c["bg"])
        mid.pack(fill="both"    , expand=True)

        ssz = 110
        self._stop_cv = tk.Canvas(mid, width=ssz, height=ssz,
                                  bg=c["bg"], highlightthickness=0)
        self._stop_cv.pack(pady=(28, 4))
        _rrect(self._stop_cv, 6, 6, ssz-6, ssz-6, 20, c["accent"])
        sq = 28
        self._stop_cv.create_rectangle(ssz//2-sq//2, ssz//2-sq//2,
                                       ssz//2+sq//2, ssz//2+sq//2,
                                       fill="white", outline="")
        self._stop_cv.bind("<Button-1>", lambda e: self._stop_recording())
        self._stop_cv.configure(cursor="hand2")

        tk.Label(mid, text="Recording", font=self.F["bodyb"],
                 bg=c["bg"], fg=c["fg"]).pack(pady=(0, 10))

        self._wc = tk.Canvas(mid, width=640, height=70,
                             bg=c["bg"], highlightthickness=0)
        self._wc.pack(pady=(0, 8))
        self._wave_idx  = 0
        self._mic_level = 0.0
        self._wave_tick()

        # Poll the transcript queue
        self._poll_transcript()

    def _poll_transcript(self):
        if not self.is_recording:
            return

        while True:
            try:
                self._mic_level = self._lq.get_nowait()
            except queue.Empty:
                break

        while True:
            try:
                msg = self._tq.get_nowait()
            except queue.Empty:
                break
            if "final" in msg:
                self._transcript_final += (" " if self._transcript_final else "") + msg["final"]
                self._transcript_partial = ""
            elif "partial" in msg:
                self._transcript_partial = msg["partial"]

        if hasattr(self, "_lt") and self._lt.winfo_exists():
            display = self._transcript_final
            if self._transcript_partial:
                display += (" " if display else "") + self._transcript_partial + "…"
            self._lt.configure(text=display if display else "Listening…")

        self.after(80, self._poll_transcript)

    def _wave_tick(self):
        """Modified visualizer: appends current volume, shifts, and draws bars."""
        if not (hasattr(self, "_wc") and self._wc.winfo_exists()):
            return
        if not self.is_recording:
            return
            
        # 1. Update list: Append current level
        self._vol_history.append(self._mic_level)
        
        # 2. Shift/Delete: Delete number at index 40 to maintain 40 items
        if len(self._vol_history) > 40:
            self._vol_history.pop(0)

        ww, wh = 640, 70
        self._wc.delete("all")
        
        wc_hex = self.c["wave"]
        
        # 3. Display as Bars
        bar_count = len(self._vol_history)
        gap = ww / bar_count
        bar_w = gap * 0.7 # 70% width for bars, 30% gap
        
        for i, val in enumerate(self._vol_history):
            x = i * gap + (gap / 2)
            # Center bars vertically
            # Scale val (0-1) to canvas height
            amp = max(2, val * wh * 0.9) 
            
            self._wc.create_line(x, wh/2 - amp/2, x, wh/2 + amp/2,
                                 fill=wc_hex, width=bar_w, capstyle="round")

        self._wave_job = self.after(50, self._wave_tick)

    def _stop_recording(self):
        self.is_recording = False
        self._wave_job    = None

        if self._audio_rec:
            self._audio_rec.stop()

        now = datetime.datetime.now()
        try:    name = now.strftime("Recording %b %-d, %I:%M %p")
        except: name = now.strftime("Recording %b %d, %I:%M %p")
        ts   = now.strftime("%H%M%S")
        here = os.path.dirname(os.path.abspath(__file__))
        wav_path = os.path.join(here, f"recording_{now.strftime('%Y%m%d')}_{ts}.wav")
        frames = self._audio_rec.get_frames() if self._audio_rec else []
        if frames:
            try:
                save_wav(frames, wav_path)
            except Exception as e:
                print(f"Warning: could not save WAV: {e}")
                wav_path = None
        else:
            wav_path = None

        transcript = self._transcript_final.strip()

        self.recordings.append({
            "name":       name,
            "timestamp":  now.strftime("%H:%M"),
            "wav":        wav_path,
            "transcript": transcript,
        })
        self._audio_rec = None
        self._show_main()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _show_settings(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_main, title="Settings")
        _line(self.frame, c)
        outer  = tk.Frame(self.frame, bg=c["bg"])
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=c["bg"], highlightthickness=0, bd=0)
        sb     = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner  = tk.Frame(canvas, bg=c["bg"])
        win    = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win, width=canvas.winfo_width())
        inner.bind("<Configure>", _cfg)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 60), "units"))
        wrap = tk.Frame(inner, bg=c["bg"])
        wrap.pack(fill="x", padx=28, pady=16)
        card = tk.Frame(wrap, bg=c["surface"])
        card.pack(fill="x")

        def row(icon_name, label, sublabel=None, right_fn=None, cmd=None, last=False):
            f = tk.Frame(card, bg=c["surface"], cursor="hand2" if cmd else "arrow")
            f.pack(fill="x")
            ic = tk.Label(f, text=get_icon(icon_name), font=self.F["im"],
                          bg=c["surface2"], fg=c["fg2"], padx=10, pady=6)
            ic.pack(side="left", padx=(14, 10), pady=10)
            txt = tk.Frame(f, bg=c["surface"])
            txt.pack(side="left", fill="x", expand=True, pady=10)
            tk.Label(txt, text=label, font=self.F["body"],
                      bg=c["surface"], fg=c["fg"], anchor="w").pack(anchor="w")
            if sublabel:
                tk.Label(txt, text=sublabel, font=self.F["small"],
                          bg=c["surface"], fg=c["fg3"], anchor="w").pack(anchor="w")
            if right_fn:
                right_fn(f).pack(side="right", padx=14, pady=10)
            if cmd:
                _row_hover(f, c["surface"], c["surface2"])
                for w in [f, ic, txt]:
                    w.bind("<Button-1>", lambda e, fn=cmd: fn())
            if not last:
                _line(card, c, padx=54)

        chev = lambda p: tk.Label(p, text=get_icon("chevron"),
                                  font=self.F["im"], bg=c["surface"], fg=c["fg3"])

        def dark_tog(parent):
            f  = tk.Frame(parent, bg=c["surface"])
            sv = tk.StringVar(value="Dark" if self.theme == "dark" else "Light")
            tk.Label(f, textvariable=sv, font=self.F["small"],
                      bg=c["surface"], fg=c["fg2"]).pack(side="left", padx=(0, 8))
            tog = Toggle(f, c, initial=self.theme == "dark", on_color=c["accent"])
            def cb(val):
                self.theme = "dark" if val else "light"
                sv.set("Dark" if val else "Light")
                self._show_settings()
            tog.set_cb(cb)
            tog.pack(side="left")
            return f

        def lt_tog(parent):
            f    = tk.Frame(parent, bg=c["surface"])
            tog = Toggle(f, c, variable=self.live_transcription,
                          initial=self.live_transcription.get(), on_color=c["green"])
            tog.pack(side="left")
            return f

        def lang_right(parent):
            f = tk.Frame(parent, bg=c["surface"])
            tk.Label(f, text=self.language.get(), font=self.F["small"],
                      bg=c["surface"], fg=c["fg2"]).pack(side="left")
            tk.Label(f, text=get_icon("chevron"), font=self.F["im"],
                      bg=c["surface"], fg=c["fg3"]).pack(side="left")
            return f

        def sounds_tog(parent):
            f  = tk.Frame(parent, bg=c["surface"])
            sv = tk.StringVar(value="On" if self.system_sounds.get() else "Off")
            tk.Label(f, textvariable=sv, font=self.F["small"],
                      bg=c["surface"], fg=c["fg2"]).pack(side="left", padx=(0, 8))
            tog = Toggle(f, c, variable=self.system_sounds,
                          initial=self.system_sounds.get(), on_color=c["green"])
            def cb(val):
                self.system_sounds.set(val)
                sv.set("On" if val else "Off")
            tog.set_cb(cb)
            tog.pack(side="left")
            return f

        row("bluetooth",  "Connect to New Device",
            "Connect to a new device via Bluetooth", right_fn=chev)
        row("brightness", "Display Mode",               right_fn=dark_tog)
        row("chat",        "Live Transcription",        right_fn=lt_tog)
        row("language",    "Language",
            sublabel=self.language.get(),
            right_fn=lang_right, cmd=self._show_language)
        row("volume",      "System Sounds",             right_fn=sounds_tog)
        row("info",        "About",
            sublabel="App version, device info, and more",
            right_fn=chev, cmd=self._show_about, last=True)

    # ... [Language and About screens remain same] ...

    def _show_language(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="Language")
        _line(self.frame, c)
        outer  = tk.Frame(self.frame, bg=c["bg"])
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=c["bg"], highlightthickness=0, bd=0)
        sb     = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner  = tk.Frame(canvas, bg=c["bg"])
        win    = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                    lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        wrap = tk.Frame(inner, bg=c["bg"])
        wrap.pack(fill="x", padx=28, pady=16)
        card = tk.Frame(wrap, bg=c["surface"])
        card.pack(fill="x")
        for i, lang in enumerate(LANGUAGES):
            last = i == len(LANGUAGES) - 1
            f    = tk.Frame(card, bg=c["surface"], cursor="hand2")
            f.pack(fill="x")
            lbl  = tk.Label(f, text=lang, font=self.F["body"],
                            bg=c["surface"], fg=c["fg"], anchor="w",
                            padx=18, pady=12)
            lbl.pack(side="left", fill="x", expand=True)
            if lang == self.language.get():
                tk.Label(f, text=get_icon("check"), font=self.F["im"],
                          bg=c["surface"], fg=c["green"], padx=14).pack(side="right")
            def sel(l=lang):
                self.language.set(l)
                self._show_language()
            _row_hover(f, c["surface"], c["surface2"])
            for w in [f, lbl]:
                w.bind("<Button-1>", lambda e, fn=sel: fn())
            if not last:
                _line(card, c, padx=18)

    def _show_about(self):
        self._clear()
        c = self.c
        self._header(self.frame, back=self._show_settings, title="About")
        _line(self.frame, c)
        outer  = tk.Frame(self.frame, bg=c["bg"])
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=c["bg"], highlightthickness=0, bd=0)
        sb     = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner  = tk.Frame(canvas, bg=c["bg"])
        win    = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                    lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        pad = tk.Frame(inner, bg=c["bg"])
        pad.pack(fill="x", padx=28, pady=16)

        id_c = tk.Frame(pad, bg=c["surface"])
        id_c.pack(fill="x", pady=(0, 14))
        row  = tk.Frame(id_c, bg=c["surface"])
        row.pack(fill="x", padx=18, pady=16)
        tk.Label(row, text=get_icon("mic"), font=self.F["il"],
                  bg=c["accent"], fg="white", padx=10, pady=6).pack(side="left")
        inf = tk.Frame(row, bg=c["surface"])
        inf.pack(side="left", padx=16)
        tk.Label(inf, text=APP_NAME, font=self.F["bodyb"],
                  bg=c["surface"], fg=c["fg"]).pack(anchor="w")
        tk.Label(inf, text=f"Version {VERSION_STRING}",
                  font=self.F["small"], bg=c["surface"], fg=c["fg2"]).pack(anchor="w")

        dc = tk.Frame(pad, bg=c["surface"])
        dc.pack(fill="x", pady=(0, 14))
        tk.Label(dc, text="DEVELOPERS", font=self.F["section"],
                  bg=c["surface"], fg=c["fg3"], anchor="w",
                  padx=18, pady=(10, 2)).pack(fill="x")
        _line(dc, c)
        for i, dev in enumerate(DEVELOPERS):
            tk.Label(dc, text=dev, font=self.F["body"],
                      bg=c["surface"], fg=c["fg"], anchor="w",
                      padx=18, pady=10).pack(fill="x")
            if i < len(DEVELOPERS) - 1:
                _line(dc, c, padx=18)

if __name__ == "__main__":
    app = App()
    app.mainloop()