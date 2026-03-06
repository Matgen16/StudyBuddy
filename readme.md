# StudyBuddy

**UC ITExpo Project**

StudyBuddy is a portable lecture recorder built on a Raspberry Pi 5. It captures audio through an I2S MEMS microphone, transcribes speech in real time on-device, and sends recordings to a server that extracts structured notes, key terms, assignments, and schedule items using a local LLM.

---

## Hardware (Client)

The client is designed to run on a **Raspberry Pi 5** with:

- **3.5" touchscreen** (480×320) — the UI is fixed to this resolution and runs fullscreen with the cursor hidden
- **IMS I2S MEMS microphone** — connected via the Pi's I2S interface for audio capture

### Enabling the I2S Microphone on Raspberry Pi OS

Add the following to `/boot/firmware/config.txt`:

```
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Then reboot. Verify the mic is detected:

```bash
arecord -l
```

You may also need to set it as the default capture device in `/etc/asound.conf`:

```
pcm.!default {
  type asym
  capture.pcm "mic"
}
pcm.mic {
  type plug
  slave {
    pcm "hw:1,0"
  }
}
```

---

## Requirements

### Server

```
flask
flask-cors
openai-whisper
requests
```

```bash
pip install flask flask-cors openai-whisper requests
```

Whisper also requires [ffmpeg](https://ffmpeg.org/download.html):

```bash
# Ubuntu/Debian (including Raspberry Pi OS)
sudo apt install ffmpeg
```

### Ollama (Local LLM)

Install Ollama from [https://ollama.com](https://ollama.com), then pull the model:

```bash
ollama pull llama3.2:3b
```

### Client (Raspberry Pi 5)

```
pyaudio
vosk
Pillow
```

```bash
pip install pyaudio vosk Pillow
```

PyAudio also requires PortAudio:

```bash
sudo apt install portaudio19-dev
```

The client uses [Vosk](https://alphacephei.com/vosk/models) for on-device live transcription. Download the small English model and place it next to `recorder_app.py`:

```bash
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
```

The expected folder name is `vosk-model-small-en-us-0.15`.

---

## Running

Start Ollama in a separate terminal on the server machine:

```bash
ollama serve
```

**Start the server:**

```bash
cd Server
python3 server.py
```

Open `http://localhost:5000` in a browser to access the web interface.

**Start the client on the Raspberry Pi:**

```bash
cd Client
python3 recorder_app.py
```

The app will launch fullscreen on the 3.5" display. Make sure the server IP is configured in `recorder_app.py` before running.

---

## Developers

- Matthew Gentle
- Ryan Parker