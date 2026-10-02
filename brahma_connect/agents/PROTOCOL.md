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
2. Gateway responds with a pairing request or known-device instructions.
3. User approves the device in Brahma.
4. Agent sends `authenticate` with the persistent secret.
5. Gateway marks the device online and publishes capabilities.
6. Brahma sends `execute` requests.
7. Agent replies with `result` or `error`.

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
