"""Headless backend tests.

Covers the pieces the Android app depends on:

* the Brahma Connect gateway (WebSocket, port 8765) pairing + auth flow
* phone text commands reaching the brain callback
* phone audio/event frames reaching the bridge
* headless UI events being broadcast back to a connected phone
* the headless UI facade working without any Qt / display
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi", reason="gateway dependencies are optional")
pytest.importorskip("fastapi.testclient", reason="needs Starlette's TestClient")

from fastapi.testclient import TestClient  # noqa: E402

from brahma_connect.gateway.server import BrahmaGatewayConfig  # noqa: E402
from brahma_connect.gateway.protocol import ProtocolTypes, build_message  # noqa: E402
from brahma_connect.service import BrahmaConnectService  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def service(tmp_path):
    config = BrahmaGatewayConfig(enabled=True, host="127.0.0.1", port=_free_port(), advertise=False)
    svc = BrahmaConnectService(tmp_path, config)
    yield svc
    with contextlib.suppress(Exception):
        svc.stop()


@pytest.fixture()
def client(service):
    return TestClient(service.app)


def _hello_payload(name: str = "Pixel 9") -> dict:
    return {
        "device_name": name,
        "platform": "android",
        "os_version": "15",
        "agent_version": "1.0.0",
        "capabilities": ["device_info", "battery", "flashlight", "volume"],
        "permissions": [],
        "battery": 88,
    }


@contextlib.contextmanager
def _paired_device(client):
    """hello ➜ approve ➜ authenticate, yielding an authenticated socket."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(build_message(ProtocolTypes.HELLO, _hello_payload()))
        reply = ws.receive_json()
        assert reply["type"] == ProtocolTypes.PAIR_REQUEST
        pending_id = reply["payload"]["pending_id"]

        approved = client.post(f"/gateway/pending/{pending_id}/approve").json()
        assert approved["success"] is True
        device_id = approved["device"]["device_id"]
        secret = approved["device_secret"]

        assert ws.receive_json()["type"] == ProtocolTypes.PAIR_APPROVED

        ws.send_json(build_message(ProtocolTypes.AUTHENTICATE, {
            "device_id": device_id,
            "device_secret": secret,
        }))
        assert ws.receive_json()["type"] == ProtocolTypes.DEVICE_ONLINE
        assert ws.receive_json()["type"] == ProtocolTypes.CAPABILITIES
        yield ws, device_id


def test_gateway_pair_authenticate_and_chat(client, service):
    received: list[str] = []
    service.gateway.on_chat_message = lambda text: received.append(text)

    with _paired_device(client) as (ws, _device_id):
        ws.send_json(build_message(ProtocolTypes.CHAT_MESSAGE, {"text": "Hey Brahma, open Spotify"}))
        deadline = time.time() + 3
        while not received and time.time() < deadline:
            time.sleep(0.05)
    assert received == ["Hey Brahma, open Spotify"]


def test_phone_events_reach_the_gateway(client, service):
    events: list[tuple[str, dict]] = []
    service.gateway.on_device_event = lambda device_id, payload: events.append((device_id, payload))

    with _paired_device(client) as (ws, device_id):
        pcm = base64.b64encode(b"\x01\x02" * 160).decode()
        ws.send_json(build_message(ProtocolTypes.EVENT, {
            "kind": "audio_in",
            "format": "pcm16",
            "sample_rate": 16000,
            "data": pcm,
        }))
        deadline = time.time() + 3
        while not events and time.time() < deadline:
            time.sleep(0.05)

    assert events, "gateway did not forward the phone event"
    assert events[0][0] == device_id
    assert events[0][1]["kind"] == "audio_in"


def test_headless_ui_publishes_without_qt():
    import ui

    assert not hasattr(ui, "QApplication")
    headless = ui.HeadlessUI()
    headless.set_state("LISTENING")
    headless.begin_task_workspace("Build a site", ["plan"])
    headless.finish_task_workspace("done")
    headless.show_content("Title", "- one\n- two")
    names = [event["name"] for event in headless.recent_events()]
    assert "state" in names
    assert names.count("task") == 2
    assert "hud_deliverable" in names


class _RealGateway:
    """Runs the gateway on a real TCP port (the Termux production path)."""

    def __init__(self, tmp_path):
        self.port = _free_port()
        config = BrahmaGatewayConfig(enabled=True, host="127.0.0.1", port=self.port, advertise=False)
        self.service = BrahmaConnectService(tmp_path, config)
        self.service.start_background()
        self._wait_until_listening()

    def _wait_until_listening(self, timeout: float = 10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        raise AssertionError(f"gateway did not start on port {self.port}")

    def stop(self):
        with contextlib.suppress(Exception):
            self.service.stop()


def test_real_gateway_round_trip(tmp_path):
    """Websocket on a live port: chat in, UI events + audio out."""
    pytest.importorskip("websockets")
    import websockets

    from core.phone_body import PhoneBodyBridge
    from ui import HeadlessUI

    gateway = _RealGateway(tmp_path)
    try:
        ui = HeadlessUI()
        bridge = PhoneBodyBridge(ui)
        bridge.attach(gateway.service)
        gateway.service.gateway._phone_body = bridge

        async def flow():
            import urllib.request

            async with websockets.connect(f"ws://127.0.0.1:{gateway.port}/ws") as ws:
                hello = build_message(ProtocolTypes.HELLO, _hello_payload())
                await ws.send(json.dumps(hello))
                pair_request = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert pair_request["type"] == ProtocolTypes.PAIR_REQUEST
                pending_id = pair_request["payload"]["pending_id"]

                approve_url = f"http://127.0.0.1:{gateway.port}/gateway/pending/{pending_id}/approve"
                request = urllib.request.Request(approve_url, method="POST", data=b"")
                with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                    approved = json.loads(response.read())

                paired = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert paired["type"] == ProtocolTypes.PAIR_APPROVED

                await ws.send(json.dumps(build_message(ProtocolTypes.AUTHENTICATE, {
                    "device_id": approved["device"]["device_id"],
                    "device_secret": approved["device_secret"],
                })))
                assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == ProtocolTypes.DEVICE_ONLINE
                assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == ProtocolTypes.CAPABILITIES

                # The UI is published from a worker thread, exactly like the brain does.
                def publish_from_thread():
                    ui.write_log("Brahma Evo: Online and listening.")
                    ui.set_state("LISTENING")
                    bridge.deliver_audio(b"\x00\x01" * 240, 24000)

                threading.Thread(target=publish_from_thread, daemon=True).start()

                chat, event_ui, audio = None, None, None
                deadline = time.time() + 8
                while time.time() < deadline and not (chat and event_ui and audio):
                    try:
                        message = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    except asyncio.TimeoutError:
                        continue
                    if message["type"] == ProtocolTypes.CHAT_MESSAGE:
                        chat = message
                    elif message["type"] == ProtocolTypes.EVENT:
                        kind = message["payload"].get("kind")
                        if kind == "ui_event":
                            event_ui = message
                        elif kind == "audio_out":
                            audio = message

                assert chat and chat["payload"]["role"] == "assistant"
                assert event_ui and event_ui["payload"]["name"] == "state"
                assert audio and audio["payload"]["format"] == "pcm16"
                assert len(base64.b64decode(audio["payload"]["data"])) == 480

        asyncio.run(flow())
    finally:
        gateway.stop()

def test_gateway_binds_with_mdns_enabled(tmp_path):
    """mDNS advertisement must never delay the WebSocket listener.

    Regression: Zeroconf setup could block for seconds (or forever on odd
    networks), leaving the phone unable to connect even though the server was
    "running".  With ``advertise=True`` the port must still open promptly.
    """
    port = _free_port()
    config = BrahmaGatewayConfig(enabled=True, host="127.0.0.1", port=port, advertise=True)
    service = BrahmaConnectService(tmp_path, config)
    try:
        service.start_background()
        deadline = time.time() + 5
        listening = False
        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    listening = True
                    break
            time.sleep(0.1)
        assert listening, "gateway did not bind while mDNS advertising was enabled"
    finally:
        with contextlib.suppress(Exception):
            service.stop()
