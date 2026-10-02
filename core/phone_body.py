"""core/phone_body.py — the companion app as Brahma's body.

The brain (Gemini Live) needs ears, a mouth and eyes.  On a desktop those were
the local microphone, speakers and screen grabber.  On Android the phone owns
all of them, so this bridge moves three streams over the existing Brahma
Connect WebSocket gateway (port 8765):

    phone ➜ brain    audio_in    base64 PCM16, 16 kHz mono  (microphone)
    brain ➜ phone    audio_out   base64 PCM16, 24 kHz mono  (Gemini voice)
    phone ➜ brain    screen_frame / notification / device_state events
    brain ➜ phone    ui_event / chat_message (headless UI surface)

Everything is additive to the existing protocol: messages use the standard
``event`` envelope, so older clients simply ignore them.  No LLM, memory or
skill-execution code is touched by this module.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Any, Callable, Optional

import json

# Audio contract shared with the Kotlin client (BrahmaAudioEngine.kt).
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1


def _b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def _unb64(data: str) -> bytes:
    try:
        return base64.b64decode(data or "", validate=False)
    except Exception:
        return b""


class PhoneBodyBridge:
    """Bridges headless UI events and audio between brain and companion app."""

    def __init__(self, ui, *, enabled: bool = True):
        self.ui = ui
        self.enabled = bool(enabled)
        self.service = None
        self._lock = threading.Lock()
        self._device_ids: set[str] = set()
        self._screen_waiters: dict[str, dict[str, Any]] = {}
        self._audio_sink: Optional[Callable[[dict], None]] = None
        self._audio_in_handler: Optional[Callable[[bytes, int], None]] = None
        self._barge_in_handler: Optional[Callable[[], None]] = None
        self._notification_handler: Optional[Callable[[dict], None]] = None

    # ── wiring ──────────────────────────────────────────────────────────────

    def attach(self, service) -> None:
        """Attach to a BrahmaConnectService instance and start relaying."""
        if service is None:
            return
        self.service = service
        gateway = getattr(service, "gateway", None)
        if gateway is None:
            return
        gateway.on_device_event = self._on_device_event
        gateway.on_device_connected = self._on_device_connected
        gateway.on_device_disconnected = self._on_device_disconnected
        gateway.on_chat_message = self._on_chat_message
        try:
            self.ui.set_event_sink(self._publish_ui_event)
        except Exception:
            pass

    def set_audio_in_handler(self, handler: Callable[[bytes, int], None]) -> None:
        """handler(pcm_bytes, sample_rate) — called for phone microphone audio."""
        self._audio_in_handler = handler

    def set_barge_in_handler(self, handler: Callable[[], None]) -> None:
        self._barge_in_handler = handler

    def set_notification_handler(self, handler: Callable[[dict], None]) -> None:
        self._notification_handler = handler

    # ── gateway callbacks ───────────────────────────────────────────────────

    def _on_device_connected(self, device_id: str, record: dict | None = None) -> None:
        with self._lock:
            self._device_ids.add(str(device_id))
        try:
            self.ui.notify_phone_connected()
            self.ui.write_log(f"SYS: Companion device connected ({device_id}).")
        except Exception:
            pass

    def _on_device_disconnected(self, device_id: str) -> None:
        with self._lock:
            self._device_ids.discard(str(device_id))

    def _on_chat_message(self, text: str, payload: dict | None = None) -> None:
        """Text typed in the companion app becomes a brain command."""
        text = (text or "").strip()
        if not text:
            return
        try:
            self.ui.submit_external_command(text, source="phone")
        except Exception as exc:
            try:
                self.ui.write_log(f"ERR: phone command failed: {exc}")
            except Exception:
                pass

    def _on_device_event(self, device_id: str, payload: dict) -> None:
        payload = payload or {}
        kind = str(payload.get("kind") or payload.get("type") or "").lower()
        with self._lock:
            self._device_ids.add(str(device_id))

        if kind == "audio_in":
            pcm = _unb64(str(payload.get("data") or ""))
            if not pcm:
                return
            rate = int(payload.get("sample_rate") or INPUT_SAMPLE_RATE)
            handler = self._audio_in_handler
            if handler is None:
                return
            try:
                handler(pcm, rate)
            except Exception as exc:
                print(f"[PhoneBody] audio_in handler failed: {exc}")
            return

        if kind == "barge_in":
            handler = self._barge_in_handler
            if handler is not None:
                try:
                    handler()
                except Exception as exc:
                    print(f"[PhoneBody] barge_in handler failed: {exc}")
            return

        if kind == "screen_frame":
            request_id = str(payload.get("request_id") or "")
            with self._lock:
                waiter = self._screen_waiters.pop(request_id, None)
            if waiter is not None:
                waiter["data"] = _unb64(str(payload.get("data") or ""))
                waiter["event"].set()
            return

        if kind in {"notification", "device_state", "screen_state"}:
            handler = self._notification_handler
            if handler is not None:
                try:
                    handler(payload)
                except Exception as exc:
                    print(f"[PhoneBody] notification handler failed: {exc}")
            return

        # Anything else is surfaced for the UI / logs.
        try:
            self.ui.publish("phone_event", payload)
        except Exception:
            pass

    # ── brain ➜ phone ───────────────────────────────────────────────────────

    @property
    def has_device(self) -> bool:
        with self._lock:
            return bool(self._device_ids)

    def _broadcast(self, payload: dict[str, Any]) -> bool:
        service = self.service
        if service is None or not self.enabled:
            return False
        try:
            return bool(service.broadcast_event(payload))
        except Exception as exc:
            print(f"[PhoneBody] broadcast failed: {exc}")
            return False

    def _publish_ui_event(self, event: dict) -> None:
        """Transport hook installed on the headless UI."""
        if not self.enabled:
            return
        name = str(event.get("name") or "")
        data = event.get("data") or {}

        # Chat lines use the message type the app already renders natively.
        role = data.get("role")
        if name == "chat" and role in {"user", "assistant"}:
            self.send_chat_message(role, str(data.get("text") or data.get("message") or ""))
            return

        self._broadcast({"kind": "ui_event", "name": name, "data": data})

    def send_chat_message(self, role: str, text: str) -> bool:
        if not (text or "").strip():
            return False
        service = self.service
        if service is None or not self.enabled:
            return False
        try:
            return bool(service.broadcast_chat_message({"role": role, "text": text}))
        except Exception as exc:
            print(f"[PhoneBody] chat broadcast failed: {exc}")
            return False

    def deliver_audio(self, pcm: bytes, sample_rate: int = OUTPUT_SAMPLE_RATE) -> bool:
        """Send one chunk of Gemini voice to the phone."""
        if not pcm:
            return False
        return self._broadcast({
            "kind": "audio_out",
            "format": "pcm16",
            "sample_rate": int(sample_rate),
            "channels": CHANNELS,
            "data": _b64(pcm),
        })

    def request_screen_frame(self, timeout: float = 6.0) -> bytes:
        """Ask the phone for a screenshot and wait briefly for the answer."""
        service = self.service
        if service is None or not self.enabled or not self.has_device:
            return b""
        request_id = f"screen-{int(time.time() * 1000)}-{threading.get_ident()}"
        waiter = {"event": threading.Event(), "data": b""}
        with self._lock:
            self._screen_waiters[request_id] = waiter
        sent = self._broadcast({"kind": "screen_request", "request_id": request_id, "format": "jpeg"})
        if not sent:
            with self._lock:
                self._screen_waiters.pop(request_id, None)
            return b""
        got = waiter["event"].wait(max(0.5, float(timeout)))
        with self._lock:
            self._screen_waiters.pop(request_id, None)
        return waiter["data"] if got else b""

    def stop(self) -> None:
        self.enabled = False
        gateway = getattr(self.service, "gateway", None) if self.service else None
        if gateway is not None:
            for attr in ("on_device_event", "on_device_connected", "on_device_disconnected"):
                try:
                    setattr(gateway, attr, None)
                except Exception:
                    pass
        service = self.service
        if service is not None:
            # Leave on_chat_message unset; main.py re-binds it on restart.
            pass


# ── module level helpers (UI shim uses these without holding a bridge) ──────

def request_screen_frame(service, timeout: float = 6.0) -> bytes:
    gateway = getattr(service, "gateway", None)
    bridge = getattr(gateway, "_phone_body", None) if gateway is not None else None
    if bridge is None:
        return b""
    return bridge.request_screen_frame(timeout=timeout)


def attach_bridge(service, bridge: PhoneBodyBridge) -> None:
    """Register a bridge on the gateway so helpers can find it."""
    gateway = getattr(service, "gateway", None)
    if gateway is not None:
        setattr(gateway, "_phone_body", bridge)


def decode_event_payload(raw: Any) -> dict:
    """Small helper used by tests and the gateway to normalise payloads."""
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
