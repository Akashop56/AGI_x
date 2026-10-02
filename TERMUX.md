# Brahma Evo — Headless on Android (Termux)

Brahma Evo no longer needs a desktop or a display. On Android the Python process
is the **brain** (Gemini Live session, memory, dynamic skill execution) and the
**Brahma Connect Android app is the body**: it shows the UI, streams microphone
audio and plays Brahma's voice.

```
┌──────────────────────────── Android phone ────────────────────────────┐
│  Brahma Connect app                                                   │
│    • chat + HUD (ui_event / chat_message)                             │
│    • microphone  ──► event{kind:"audio_in"}   PCM16 16 kHz mono       │
│    • speaker     ◄── event{kind:"audio_out"}  PCM16 24 kHz mono       │
│    • notifications / screen state ──► event{kind:"notification"}      │
│                              │  WebSocket  ws://<host>:8765/ws        │
├──────────────────────────────┼────────────────────────────────────────┤
│  Termux: python main.py      ▼                                        │
│    • Brahma Connect gateway (FastAPI + WebSocket, port 8765)          │
│    • Mobile Connect dashboard (port 8000, QR/browser login)           │
│    • Gemini Live brain + tool/router + memory (unchanged logic)       │
└───────────────────────────────────────────────────────────────────────┘
```

## 1. Install

```bash
pkg install git
git clone https://github.com/titechprabhasolutions/Brahma---personal.git
cd Brahma---personal
bash scripts/setup_termux.sh
```

The script installs `python`, `clang`, `rust`, native image/XML libs and
[Termux:API](https://f-droid.org/packages/com.termux.api/), then
`pip install -r requirements.txt` — a deliberately small, Qt-free set.

## 2. Give it a Gemini API key

Any of these work:

```bash
python main.py --set-key AIza...     # writes ~/BrahmaAI/config/api_keys.json
export GEMINI_API_KEY=AIza...        # or put it in your shell profile
```

You can also paste the key from the phone: open `http://<phone-ip>:8000`,
log in with the dashboard key, and use **Settings → API key**
(`POST /api/settings/api-key`, authenticated).

## 3. Run

```bash
python main.py                     # foreground
termux-wake-lock                   # keep Android from sleeping the process
nohup python main.py > brahma.log 2>&1 &
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--set-key <KEY>` | store the Gemini API key and exit |
| `--audio-mode auto\|phone\|local\|none` | where mic/speaker live (default `auto`: phone when paired, otherwise local hardware, otherwise none) |
| `--skip-update` | do not run the git auto-update at boot |
| `--version` | print the bundled version |

Ports:

* **8765** — Brahma Connect gateway (`/health`, `/gateway/*`, `/ws`), bound to `0.0.0.0`
* **8000** — Mobile Connect dashboard (`/health`, `/login`, `/api/*`, `/ws`)

## 4. Pair the app

1. Install the companion app from `brahma-connect-android/` (Android Studio or `./gradlew assembleDebug`).
2. Grant **Camera**, **Notifications** and **Microphone** when asked.
3. The app discovers `_BRAHMA._tcp.local.` over mDNS — the Python gateway
   advertises it via Zeroconf automatically. If discovery is blocked on your
   network (AP isolation), type the host and port `8765` manually.
4. Approve the pairing request:
   * from the dashboard UI, or
   * from the gateway API: `curl http://<phone-ip>:8765/gateway/pending` then
     `curl -X POST http://<phone-ip>:8765/gateway/pending/<id>/approve`
5. The app stores its device secret and reconnects automatically. Voice starts
   as soon as the socket authenticates.

## Protocol additions (all additive — old clients ignore them)

Every message keeps the standard envelope (`type`, `request_id`, `timestamp`,
`payload`). The body uses `event` messages:

| Direction | `payload.kind` | Fields |
| --- | --- | --- |
| phone → brain | `audio_in` | `format:"pcm16"`, `sample_rate:16000`, `channels:1`, `data` (base64) |
| brain → phone | `audio_out` | `format:"pcm16"`, `sample_rate:24000`, `channels:1`, `data` (base64) |
| brain → phone | `speak` | `text` (platform TTS fallback) |
| brain → phone | `ui_event` | `name` (`state`, `task`, `hud_operation`, `hud_deliverable`, `attention`, `meeting`, `audio_level`, …), `data` |
| brain → phone | `screen_request` | `request_id` |
| phone → brain | `screen_frame` | `request_id`, `data` (base64 JPEG; empty means "not available") |
| phone → brain | `notification` / `screen_state` | `app`, `title`, `text` |
| phone → brain | `barge_in` | stop playback, resume listening |

Chat text keeps using `chat_message` with `payload.role` (`user` / `assistant`)
and `payload.text`, so the app's existing chat UI works unchanged.

## What runs where

| Capability | Desktop (old) | Headless Android |
| --- | --- | --- |
| UI / HUD | PyQt6 window | Android app (`ui_event`) |
| Microphone / speaker | sounddevice | Android app over the gateway |
| Attention monitoring | Windows notification DB | app pushes `notification` events |
| Screen / camera capture | mss / OpenCV | delegated to the app (falls back to text) |
| pyautogui / pyperclip workflows | pyautogui | disabled with a clear message; clipboard uses Termux:API |
| Dashboard (port 8000) | FastAPI | unchanged |
| Gateway (port 8765) | FastAPI | unchanged, starts first |
| Brain, memory, skills | Gemini Live + `memory/` + `features/` | **untouched** |

## Troubleshooting

* **Gateway says "port 8765 already in use"** — another Brahma instance is
  running; stop it with `pkill -f main.py`.
* **App never finds the gateway** — check that the phone and Termux are on the
  same Wi-Fi and that client isolation is off; use manual host/port otherwise.
* **No voice** — microphone permission missing, or `--audio-mode none`.
  `python main.py --audio-mode phone` forces the phone path.
* **`ModuleNotFoundError: sounddevice / pyautogui / mss`** — these are optional
  desktop extras. The server runs without them; install
  `requirements-optional.txt` only if you want the desktop paths.
* **`pip install` fails on a compiled package** — install the Termux package
  first (`pkg install python-numpy`, `pkg install python-cryptography`, …), or
  skip it: it is almost certainly in `requirements-optional.txt`.
