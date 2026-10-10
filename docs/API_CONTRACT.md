# HTTP API contract

All routes are under `/api/v1`. Responses use JSON and FastAPI publishes the complete OpenAPI schema at `/docs`.

## Lifecycle and safety

`GET /health` returns `{ "status": "ok" }`. `GET /ready` reports `ready`, `safe`, and per-service status; optional hardware being unavailable is reported rather than fabricated as ready.

`GET /session` returns the authoritative session state. `POST /session/pause`, `/session/resume`, `/session/emergency-stop`, and `/session/recover` mutate it. Emergency stop is latched until recovery; resume does not clear it. Recovery does not enable cursor or browser permission.

Permissions are explicit: `POST /cursor/permission` and `POST /browser/permission` accept `{ "enabled": true|false }`. Pause, emergency stop, missing tracking, invalid calibration, and disabled permission block protected actions.

## Voice, vision, browser, and evaluation

- `GET /voice/status`
- `POST /voice/commands` with `{ "transcript": "click", "execute": true }`
- `GET /vision/status`
- `POST /vision/start` and `POST /vision/stop` (camera activation is explicit)
- `POST /vision/calibration` with four `{ "x": number, "y": number }` points
- `POST /vision/calibration/reset`
- `GET /browser/status`
- `POST /browser/requests` with `{ "request": "search for laptops", "execute": false }`
- `POST /cursor/move` with `{ "x": 100, "y": 200 }` (dry-run adapter only)
- `GET /evaluation/summary`

Command results always expose `received`, `processing`, `completed`, `blocked`, or `failed` through the result status and corresponding telemetry events. A blocked result includes a stable `reason_code`, such as `SESSION_PAUSED`, `EMERGENCY_STOP`, `CURSOR_CONTROL_DISABLED`, `TRACKING_UNAVAILABLE`, or `BROWSER_PERMISSION_DISABLED`.

The lifecycle order for a protected request is `command.received` -> `command.processing` -> one of `command.completed`, `command.blocked`, or `command.failed`. Browser planning emits `browser.action` with `phase: plan_created` and remains `processing` until an explicit execution request.

Error responses use `{ "error": { "code", "message", "subsystem", "recoverable", "request_id", "details" } }`.

