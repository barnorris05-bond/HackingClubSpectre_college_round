# Telemetry schema

Connect to `ws://127.0.0.1:8000/api/v1/telemetry`. The first message is a session snapshot. Subsequent messages are event envelopes:

```json
{
  "type": "command.blocked",
  "timestamp": "2026-10-10T10:00:00Z",
  "session_id": "uuid",
  "sequence": 12,
  "data": {
    "command_id": "uuid",
    "status": "blocked",
    "reason_code": "SESSION_PAUSED"
  }
}
```

`timestamp` is UTC ISO 8601. `sequence` increases for published events. Per-client queues are bounded; slow clients lose the oldest queued telemetry rather than blocking safety operations. Disconnecting does not alter session or safety state.

Supported event types include `session.state`, `session.paused`, `session.resumed`, `session.emergency_stop`, `voice.state`, `voice.transcript`, `voice.error`, `command.received`, `command.processing`, `command.completed`, `command.blocked`, `command.failed`, `vision.state`, `vision.tracking_lost`, `vision.calibration`, `cursor.state`, `browser.state`, `browser.action`, `evaluation.updated`, `system.warning`, and `system.error`.

Raw audio, camera frames, credentials, and secrets are never sent through this channel.

