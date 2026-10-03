"""ui.py — Headless Brahma Evo interface layer.

The desktop Qt front-end is gone.  On Android the companion app is the body:
it renders the UI, captures voice and plays Brahma's voice.  Everything the
brain used to push at widgets is now published as a JSON event on a small,
thread-safe bus:

    brain / actions ──► HeadlessUI.publish(event) ──► listeners
                                             ├── Brahma Connect gateway (phone)
                                             └── dashboard / local log

The class deliberately keeps the *exact* public surface the rest of the code
base grew up with (``write_log``, ``set_state``, ``begin_task_workspace`` …).
That way main.py, the agent loop and every dynamic skill keep working without
touching the LLM, memory or skill-execution logic.

Run ``python main.py`` on Termux and this module needs no display, no Qt and no
desktop-only dependency at all.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque

from core.user_paths import get_api_keys_path, get_user_data_dir

BASE_DIR = Path(__file__).resolve().parent

CONFIG_DIR = get_user_data_dir() / "config"
API_KEYS_FILE = get_api_keys_path()
APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.json"
DISCORD_SETTINGS_FILE = CONFIG_DIR / "discord_settings.json"
LOG_DIR = get_user_data_dir() / "logs"
LOG_FILE = LOG_DIR / "brahma_headless.log"
REPO_SETTINGS_FILE = BASE_DIR / "config" / "app_settings.json"

# Settings that used to be owned by the Qt launcher. They are kept so existing
# config files (and the settings bridge) keep round-tripping cleanly.
_DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "startup_animation_enabled": False,
    "last_boot_stamp": 0,
    "boot_sequence_played": True,
    "show_workspace_on_startup": False,
    "launcher_pos": [0, 0],
    "developer_mode_enabled": False,
    "developer_mode_workspace": "",
    "audio_mode": "auto",          # auto | phone | local | none
    "phone_voice_enabled": True,   # stream Gemini voice to the companion app
    "muted": False,
    "greeting_enabled": True,
}

_DEFAULT_DISCORD_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "bot_token": "",
    "channel_id": "",
}


def _default_app_settings() -> dict:
    return dict(_DEFAULT_APP_SETTINGS)


def _default_discord_settings() -> dict:
    return dict(_DEFAULT_DISCORD_SETTINGS)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class _NullSignal:
    """Stand-in for the old ``pyqtSignal`` attributes on ``MainWindow``.

    Some actions (circuit_assembler) probe ``player._win._circuit_hud_sig``
    before falling back to the documented ``show_circuit_hud`` API. Emitting
    here is a harmless no-op so those probes keep working.
    """

    def emit(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None

    def connect(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None


class _WindowShim:
    """Minimal headless replacement for the old ``MainWindow`` instance."""

    def __init__(self, ui: "HeadlessUI"):
        self._ui = ui
        self._ready = False
        self._muted = False
        self._current_file: str | None = None
        self._command_bar = _NullSignal()
        self._log_sig = _NullSignal()

    def set_meeting_mode(self, *args: Any, **kwargs: Any) -> None:
        self._ui.set_meeting_mode(*args, **kwargs)

    def show_daily_briefing(self, text: str) -> None:
        self._ui.show_daily_briefing(text)

    def hide_daily_briefing(self) -> None:
        self._ui.publish("daily_briefing_hide", {})

    def schedule_daily_briefing_hide(self, *args: Any, **kwargs: Any) -> None:
        return None

    def notify_phone_connected(self) -> None:
        self._ui.notify_phone_connected()

    def set_scanning(self, enabled: bool, text: str = "") -> None:
        self._ui.set_scanning(enabled, text)

    def set_muted_state(self, muted: bool, *, wakeword: bool = False) -> None:
        self._ui.set_muted_state(muted, wakeword=wakeword)

    def set_audio_level(self, level: float) -> None:
        self._ui.set_audio_level(level)

    def submit_command(self, text: str, source: str = "external") -> None:
        self._ui.submit_external_command(text, source=source)


class _RootShim:
    """Blocking run loop used as the process' main thread on Termux."""

    def __init__(self, ui: "HeadlessUI"):
        self._ui = ui

    def mainloop(self) -> None:
        self._ui.run_forever()

    def quit(self) -> None:  # pragma: no cover - parity with Qt API
        self._ui.stop()


class HeadlessUI:
    """Drop-in, display-free replacement for the old ``BrahmaUI`` class."""

    def __init__(self, face_path: str | None = None, size=None, *, show_immediately: bool = True):
        self.face_path = face_path
        self._listeners: list[Callable[[dict], None]] = []
        self._listeners_lock = threading.Lock()
        self._buffer: Deque[dict] = deque(maxlen=400)
        self._settings_cache: dict | None = None
        self._audio_level = 0.0
        self._last_audio_emit = 0.0
        self._state = "BOOTING"
        self._muted = bool(self._load_app_settings().get("muted", False))
        self._wakeword_listening = False
        self._current_file: str | None = None
        self._ready = False
        self._stopping = threading.Event()
        self._boot_steps: list[dict] = []
        self._event_sink: Callable[[dict], None] | None = None
        self._brahma_connect_service = None
        self._phone_connected = False

        # Callback hooks the brain installs (same names as the Qt version).
        self.on_text_command: Callable[[str, str], None] | None = None
        self.on_attention_action: Callable[[dict, str], None] | None = None
        self.on_remote_clicked: Callable[..., Any] | None = None
        self.on_chat_event: Callable[[dict], None] | None = None
        self.on_external_command: Callable[[str, str], None] | None = None

        self._win = _WindowShim(self)
        self.root = _RootShim(self)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._log("SYS: Brahma Evo headless interface online.")
        self._ready = self._api_key_present()
        self._win._ready = self._ready
        if self._ready:
            self.publish("api_key_ready", {"source": "startup"})

    # ── event bus ────────────────────────────────────────────────────────────

    def subscribe(self, listener: Callable[[dict], None]) -> None:
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[dict], None]) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def set_event_sink(self, sink: Callable[[dict], None] | None) -> None:
        """Single transport hook (used by the Brahma Connect bridge)."""
        self._event_sink = sink

    def publish(self, name: str, data: dict | None = None) -> None:
        event = {
            "name": name,
            "data": data or {},
            "ts": time.time(),
        }
        self._buffer.append(event)
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:  # never let a UI sink kill the brain
                print(f"[UI] listener error ({name}): {exc}")
        if self._event_sink is not None:
            try:
                self._event_sink(event)
            except Exception as exc:
                print(f"[UI] event sink error ({name}): {exc}")

    def recent_events(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 400))
        return list(self._buffer)[-limit:]

    # ── logging ──────────────────────────────────────────────────────────────

    def _log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        try:
            print(line)
        except Exception:
            pass
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d ')}{line}\n")
        except Exception:
            pass

    def write_log(self, text: str) -> None:
        text = "" if text is None else str(text)
        if not text:
            return
        self._log(text)
        role, body = self._classify_log(text)
        payload = {"role": role, "text": text, "message": body}
        self.publish("log", payload)
        if self.on_chat_event is not None:
            try:
                self.on_chat_event({"role": role, "text": text})
            except Exception:
                pass
        if role in {"user", "assistant"}:
            self.publish("chat", payload)

    @staticmethod
    def _classify_log(text: str) -> tuple[str, str]:
        for prefix, role in (("You:", "user"), ("Brahma Evo:", "assistant"), ("Brahma:", "assistant")):
            if text.startswith(prefix):
                return role, text[len(prefix):].strip()
        lowered = text.lower()
        if lowered.startswith(("err:", "error:")):
            return "error", text
        if text.startswith(("SYS:", "THINKING:", "PHONE:")):
            return "system", text
        return "system", text

    # ── state / telemetry ────────────────────────────────────────────────────

    def set_state(self, state: str, detail: str | None = None) -> None:
        self._state = str(state or "").upper() or "IDLE"
        data = {"state": self._state}
        if detail:
            data["detail"] = str(detail)
        self.publish("state", data)

    @property
    def state(self) -> str:
        return self._state

    def set_audio_level(self, level: float) -> None:
        try:
            value = max(0.0, min(1.0, float(level)))
        except Exception:
            return
        self._audio_level = value
        now = time.monotonic()
        if now - self._last_audio_emit < 0.1:  # ~10 fps is plenty for a phone
            return
        self._last_audio_emit = now
        self.publish("audio_level", {"level": round(value, 3)})

    def set_scanning(self, enabled: bool, text: str = "") -> None:
        self.publish("scanning", {"enabled": bool(enabled), "text": text or ""})

    # ── mute ────────────────────────────────────────────────────────────────

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self.set_muted_state(bool(value))

    def set_muted(self, muted: bool) -> None:
        self.set_muted_state(bool(muted))

    def set_muted_state(self, muted: bool, *, wakeword: bool = False) -> None:
        muted = bool(muted)
        changed = muted != self._muted
        self._muted = muted
        self._wakeword_listening = muted
        self._win._muted = muted
        if changed or wakeword:
            self.publish("muted", {"muted": muted, "wakeword": bool(wakeword)})
        try:
            settings = self._load_app_settings()
            if settings.get("muted") != muted:
                settings["muted"] = muted
                self._save_app_settings(settings)
        except Exception:
            pass

    # ── app settings ─────────────────────────────────────────────────────────

    def _load_app_settings(self) -> dict:
        if self._settings_cache is not None:
            return dict(self._settings_cache)
        settings = _default_app_settings()
        for candidate in (APP_SETTINGS_FILE, REPO_SETTINGS_FILE):
            if candidate.exists():
                settings.update(_read_json(candidate))
                break
        self._settings_cache = dict(settings)
        return dict(settings)

    def _save_app_settings(self, settings: dict) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            APP_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")
        except Exception as exc:
            self._log(f"ERR: could not save app settings: {exc}")
        self._settings_cache = dict(settings)

    def get_app_setting(self, key: str, default: Any = None) -> Any:
        return self._load_app_settings().get(key, default)

    def set_app_setting(self, key: str, value: Any) -> None:
        settings = self._load_app_settings()
        settings[key] = value
        self._save_app_settings(settings)
        self.publish("settings_changed", {"key": key, "value": value})

    def _load_discord_settings(self) -> dict:
        settings = _default_discord_settings()
        settings.update(_read_json(DISCORD_SETTINGS_FILE))
        if (settings.get("bot_token") or "").strip():
            settings["enabled"] = True
        return settings

    def _save_discord_settings(self, settings: dict) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DISCORD_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")

    # ── phone / connect plumbing ─────────────────────────────────────────────

    def set_brahma_connect_service(self, service) -> None:
        self._brahma_connect_service = service

    @property
    def brahma_connect_service(self):
        return self._brahma_connect_service

    def notify_phone_connected(self) -> None:
        self._phone_connected = True
        self.publish("phone_connected", {})

    @property
    def phone_connected(self) -> bool:
        return self._phone_connected

    def submit_external_command(self, text: str, source: str = "external") -> None:
        handler = self.on_external_command or self.on_text_command
        if handler is None:
            self._log(f"SYS: No command handler attached; dropped '{str(text)[:60]}'.")
            return
        try:
            handler(text, source)
        except TypeError:
            try:
                handler(text)  # type: ignore[misc]
            except Exception as exc:
                self._log(f"ERR: external command failed: {exc}")
        except Exception as exc:
            self._log(f"ERR: external command failed: {exc}")

    # ── task workspace ──────────────────────────────────────────────────────

    def begin_task_workspace(self, command: str, plan: list | str | None = None, source: str = "local") -> None:
        self.publish("task", {
            "action": "start",
            "command": command or "",
            "plan": plan or [],
            "source": source or "local",
        })

    def update_task_workspace(self, *, title: str | None = None, command: str | None = None,
                              plan: list | str | None = None, status: str | None = None,
                              output: str | None = None, percent: int | None = None,
                              footer: str | None = None, source: str | None = None) -> None:
        payload: dict[str, Any] = {"action": "update"}
        for key, value in (
            ("title", title), ("command", command), ("plan", plan), ("status", status),
            ("output", output), ("percent", percent), ("footer", footer), ("source", source),
        ):
            if value is not None:
                payload[key] = value
        self.publish("task", payload)

    def finish_task_workspace(self, result: str, status: str = "Task completed.", percent: int = 100) -> None:
        self.publish("task", {
            "action": "finish",
            "result": result or "Done.",
            "status": status or "Task completed.",
            "percent": percent,
        })

    def clear_task_workspace(self) -> None:
        self.publish("task", {"action": "clear"})

    # ── HUD / content ───────────────────────────────────────────────────────

    def show_hud_operation(self, title: str, step: str, sources: list | None = None,
                           tool: str | None = None, **kwargs: Any) -> None:
        payload = {"title": title, "step": step, "sources": sources or [], "tool": tool or ""}
        payload.update(kwargs)
        self.publish("hud_operation", payload)

    def show_hud_deliverable(self, title: str, summary: str = "", bullets: list | None = None,
                             file_path: str | None = None, kind: str = "result",
                             actions: list | None = None, data: dict | None = None, **kwargs: Any) -> None:
        payload = {
            "title": title,
            "summary": summary or "",
            "bullets": bullets or [],
            "file_path": file_path or "",
            "kind": kind or "result",
            "actions": actions or [],
            "data": data or {},
        }
        payload.update(kwargs)
        self.publish("hud_deliverable", payload)

    def show_content(self, title: str, body: str) -> None:
        """Universal rich content presenter (headless: structured event)."""
        body = str(body or "")
        bullets = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped and stripped[0] in "-*•>123456789":  # noqa: SIM118 - char check
                cleaned = stripped.lstrip("-*•>0123456789. ").strip()
                if cleaned:
                    bullets.append(cleaned)
        self.show_hud_deliverable(
            title=title,
            summary=body if not bullets else "",
            bullets=bullets[:8],
            kind="deliverable",
        )

    def show_daily_briefing(self, text: str) -> None:
        self.publish("daily_briefing", {"text": str(text or "")})

    def hide_daily_briefing(self) -> None:
        self.publish("daily_briefing", {"text": "", "hide": True})

    def schedule_daily_briefing_hide(self, *args: Any, **kwargs: Any) -> None:
        return None

    def show_circuit_hud(self, circuit_data: dict) -> None:
        self.publish("circuit_hud", circuit_data or {})

    def show_attention_alert(self, event: dict) -> None:
        self.publish("attention", event or {})

    def show_call_screening(self, event: dict) -> None:
        self.publish("call_screening", event or {})

    def update_call_screening_transcript(self, speaker: str, text: str) -> None:
        self.publish("call_screening", {"action": "transcript", "speaker": speaker, "text": text})

    def hide_call_screening(self) -> None:
        self.publish("call_screening", {"action": "hide"})

    def set_meeting_mode(self, enabled: bool, title: str = "", summary: str = "",
                         answer: str = "", speech: str = "") -> None:
        self.publish("meeting", {
            "enabled": bool(enabled),
            "title": title,
            "summary": summary,
            "answer": answer,
            "speech": speech,
        })

    # ── boot sequence ───────────────────────────────────────────────────────

    def boot_add_step(self, text: str) -> None:
        self._boot_steps.append({"text": text, "status": "pending"})
        self.publish("boot", {"action": "add_step", "text": text})

    def boot_set_step_status(self, text: str, status: str) -> None:
        for step in self._boot_steps:
            if step["text"] == text:
                step["status"] = status
        self.publish("boot", {"action": "step_status", "text": text, "status": status})

    def boot_set_progress(self, percent: int, tip: str | None = None) -> None:
        payload = {"action": "progress", "percent": int(percent)}
        if tip:
            payload["tip"] = tip
        self.publish("boot", payload)

    # ── voice helpers used by actions ───────────────────────────────────────

    def speak_async(self, text: str) -> None:
        """Publish a line for the companion app to speak.

        The brain routes real TTS through the live Gemini session (see
        BrahmaLive.speak); this is the fire-and-forget path used by actions
        that hold a ``player`` reference.
        """
        text = (text or "").strip()
        if not text:
            return
        self.publish("speak", {"text": text})

    def start_speaking(self) -> None:
        self.set_state("SPEAKING")

    def stop_speaking(self) -> None:
        if not self.muted:
            self.set_state("LISTENING")

    # ── screen / clipboard bridges ─────────────────────────────────────────

    def capture_screen_bytes(self) -> bytes:
        """Return a screen frame (desktop) or ask the companion app for one."""
        screen = self._capture_screen_desktop()
        if screen:
            return screen
        return self._request_phone_screen()

    def _capture_screen_desktop(self) -> bytes:
        try:
            from PIL import ImageGrab  # noqa: WPS433 - desktop only

            img = ImageGrab.grab()
            import io

            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=70)
            return buf.getvalue()
        except Exception:
            return b""

    def _request_phone_screen(self) -> bytes:
        service = self._brahma_connect_service
        if service is None:
            return b""
        try:
            from core.phone_body import request_screen_frame
            return request_screen_frame(service, timeout=6.0)
        except Exception:
            return b""

    @property
    def current_file(self) -> str | None:
        return self._current_file

    # ── lifecycle ───────────────────────────────────────────────────────────

    def show_main(self) -> None:
        self.publish("window", {"visible": True})

    def hide_main(self) -> None:
        self.publish("window", {"visible": False})

    def show_confirm(self, title: str, detail: str = "") -> None:
        self.publish("confirm", {"action": "show", "title": title, "detail": detail})

    def hide_confirm(self) -> None:
        self.publish("confirm", {"action": "hide"})

    def show_memory_inspector(self) -> None:
        self.publish("memory_inspector", {"action": "show"})

    def hide_memory_inspector(self) -> None:
        self.publish("memory_inspector", {"action": "hide"})

    # ── API key onboarding ─────────────────────────────────────────────────

    @staticmethod
    def _api_key_present() -> bool:
        return bool(_read_gemini_api_key())

    def set_api_key(self, key: str) -> None:
        key = (key or "").strip()
        if not key:
            return
        API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = _read_json(API_KEYS_FILE)
        data["gemini_api_key"] = key
        API_KEYS_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        self._ready = True
        self._win._ready = True
        self.publish("api_key_ready", {"source": "runtime"})

    def wait_for_api_key(self) -> None:
        """Block until a Gemini API key exists. The gateway keeps serving."""
        announced = False
        while not self._api_key_present():
            if not announced:
                self._log(
                    "SYS: No Gemini API key found. Add it with "
                    "'python main.py --set-key <KEY>', the GEMINI_API_KEY env var, "
                    "or the phone dashboard. Waiting..."
                )
                announced = True
            time.sleep(1.0)
        self._ready = True
        self._win._ready = True
        self.publish("api_key_ready", {"source": "wait"})

    def run_forever(self) -> None:
        """Blocking main loop: keep the process alive, handle ^C cleanly."""
        self._log("SYS: Headless server running. Press Ctrl+C to stop.")
        try:
            while not self._stopping.wait(0.5):
                pass
        except KeyboardInterrupt:
            self._log("SYS: Interrupt received, shutting down.")
        finally:
            self.stop()

    def stop(self) -> None:
        if not self._stopping.is_set():
            self._stopping.set()
            self.publish("shutdown", {})


def _read_gemini_api_key() -> str:
    """Look for the key in every place a Termux user can realistically set."""
    env_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env_key:
        return env_key
    for candidate in (API_KEYS_FILE,):
        key = (_read_json(candidate).get("gemini_api_key") or "").strip()
        if key:
            return key
    return ""


# Aliases kept so existing imports (`from ui import BrahmaUI`) keep working.
BrahmaUI = HeadlessUI
UI = HeadlessUI

__all__ = [
    "BrahmaUI",
    "HeadlessUI",
    "UI",
    "APP_SETTINGS_FILE",
    "CONFIG_DIR",
    "DISCORD_SETTINGS_FILE",
    "LOG_FILE",
]
