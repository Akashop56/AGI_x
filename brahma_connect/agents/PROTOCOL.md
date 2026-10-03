# Brahma Connect Protocol

Brahma Connect uses a small JSON protocol so Brahma, companion apps, and
device agents can communicate consistently over WebSocket.

## Required envelope

Every message must contain:

- `type`
- `request_id`
- `timestamp`
- `payload`

Example:

```json
{
  "type": "hello",
  "request_id": "c5b6f5d6b2f8438f8b7c2b4b27a4f6c1",
  "timestamp": "2026-08-08T10:00:00+00:00",
  "payload": {}
}
```

## Core message types

- `hello`
- `pair_request`
- `pair_approved`
- `authenticate`
- `device_online`
- `device_offline`
- `capabilities`
- `execute`
- `result`
- `event`
- `error`
- `ping`
- `pong`
- `file_transfer`
- `screen_capture`
- `chat_message`

## Expected flow

1. Agent connects and sends `hello`.
2. The headless gateway immediately sends `pair_approved` for loopback or trusted local-network peers; other peers receive a pairing request for explicit approval.
3. The agent sends `authenticate` with the persistent secret from `pair_approved`.
4. The gateway marks the device online and publishes capabilities.
5. Brahma sends `execute` requests.
6. The agent replies with `result` or `error`.

Local auto-approval is enabled by default for the Termux/headless deployment and
can be disabled with `auto_approve_local: false` in the gateway configuration.
The trust decision uses the WebSocket peer IP, not a hostname supplied by the
client.

## Headless body events (Brahma Evo on Android/Termux)

The Python process runs headless and the Android app is the body. All of it
uses the standard `event` envelope, so older agents ignore these messages.

| Direction | `payload.kind` | Fields |
| --- | --- | --- |
| phone ➜ brain | `audio_in` | `format`, `sample_rate` (16000), `channels`, `data` (base64 PCM16) |
| brain ➜ phone | `audio_out` | `format`, `sample_rate` (24000), `channels`, `data` (base64 PCM16) |
| brain ➜ phone | `speak` | `text` — platform TTS fallback when the live session is down |
| brain ➜ phone | `ui_event` | `name`, `data` — headless UI (state, task, HUD, attention, meeting…) |
| brain ➜ phone | `screen_request` | `request_id` |
| phone ➜ brain | `screen_frame` | `request_id`, `data` (base64 JPEG; empty = unavailable) |
| phone ➜ brain | `notification` / `screen_state` | `app`, `title`, `text` |
| phone ➜ brain | `barge_in` | interrupt playback and listen |

Chat text continues to use `chat_message` with `payload.role` and
`payload.text`, which is what the Android client already renders.

## Notes

- Pairing offers expire.
- Devices can be revoked or forgotten.
- Capability checks happen before routing commands.
- File transfer and screen capture are modeled as capabilities, not special cases.
