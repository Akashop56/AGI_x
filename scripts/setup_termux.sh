#!/data/data/com.termux/files/usr/bin/bash
# Brahma Evo — Android / Termux headless setup
#
#   bash scripts/setup_termux.sh
#
# Installs the system packages and Python dependencies the headless brain and
# the Brahma Connect gateway (port 8765) need. No Qt, no desktop automation.
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '\033[1;36m[Brahma]\033[0m %s\n' "$1"; }

if [ ! -d "/data/data/com.termux" ]; then
    say "Warning: this does not look like Termux. Continuing anyway."
fi

say "Updating package lists..."
pkg update -y

say "Installing system packages (compilers + native libs)..."
pkg install -y python clang rust binutils libjpeg-turbo zlib libxml2 libxslt termux-api git

# numpy is much faster to install from the Termux repo than from source.
pkg install -y python-numpy || say "python-numpy not packaged; pip will build it."

say "Updating pip tooling..."
python -m pip install --upgrade pip wheel setuptools

say "Installing Python requirements (headless core)..."
python -m pip install -r requirements.txt

say "Verifying key imports..."
python - <<'PY'
import importlib
missing = []
for name in ("fastapi", "uvicorn", "cryptography", "zeroconf", "google.genai", "edge_tts"):
    try:
        importlib.import_module(name)
    except Exception as exc:  # pragma: no cover - setup helper
        missing.append(f"{name} ({exc})")
if missing:
    print("Missing:", ", ".join(missing))
    raise SystemExit(1)
print("All core modules import cleanly.")
PY

say "Setup complete."
cat <<'EOF'

Next steps
----------
1. Give the app a Gemini key (any one of these):
     python main.py --set-key AIza...
     export GEMINI_API_KEY=AIza...
     # or paste it in the phone dashboard (Settings → API key)

2. Start the headless server:
     python main.py
   Keep it alive with:
     termux-wake-lock && nohup python main.py > brahma.log 2>&1 &

3. In the Brahma Connect Android app: discover "Brahma Connect" via mDNS
   (or type the phone-visible IP and port 8765), then approve the pair request.

Ports
-----
  8765  Brahma Connect gateway (WebSocket + pairing API) — the phone's door
  8000  Mobile Connect dashboard (QR login, chat, file uploads)

Optional extras (desktop-only skills, local mic/speaker):
     pip install -r requirements-optional.txt
EOF
