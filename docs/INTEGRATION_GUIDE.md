# Frontend integration guide

The Electron/React frontend should treat this service as a local HTTP/WebSocket process and must not import Python modules. During development, start the backend on `127.0.0.1:8000` and allow the frontend's configured origin in `HANDSFREE_ALLOWED_ORIGINS`. In production, launch the packaged backend as a local child process and wait for `/api/v1/ready` before enabling controls.

```javascript
const base = "http://127.0.0.1:8000/api/v1";
const session = await fetch(`${base}/session`).then(r => r.json());
await fetch(`${base}/session/pause`, {method: "POST"});
await fetch(`${base}/session/resume`, {method: "POST"});
await fetch(`${base}/session/emergency-stop`, {method: "POST"});

let ws = new WebSocket("ws://127.0.0.1:8000/api/v1/telemetry");
ws.onmessage = ({data}) => {
  const event = JSON.parse(data);
  // Render by event.type and inspect event.data.status/reason_code.
};
ws.onclose = () => setTimeout(() => {/* reconnect and re-read /session */}, 1000);
```

Render `command.blocked` distinctly from `command.completed`; never infer success from a request HTTP response alone. Show `ready: false` or an individual service's `unavailable` state instead of enabling a control that depends on unavailable hardware. Keep window behavior and IPC in Electron; the backend owns safety, state, and action authorization.

