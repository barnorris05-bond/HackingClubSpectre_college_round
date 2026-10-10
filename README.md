# Handsfree Backend Lab

An independently runnable FastAPI backend for voice, vision, safety-gated actions, browser orchestration, telemetry, and evaluation. It does not contain Electron, React, or frontend code and communicates through HTTP and WebSocket contracts only.

## Quick start on Windows

Python 3.11+ is supported. Python 3.13.15 was the compatible installed Windows runtime verified for this workspace (the MSYS Python 3.14 executable cannot consume the required Windows wheels). Create an isolated environment and install the small default dependency set:

```powershell
& "C:\Users\Norris\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Run the tests with `python -m pytest`. Optional providers are intentionally separate: install `requirements-optional.txt` only for hardware or browser work you have chosen to verify. No model downloads happen automatically.

The default service binds to loopback. Voice and camera are reported unavailable unless a provider is explicitly supplied/configured. The browser provider is a harmless dry-run adapter; browser permission is disabled by default. Cursor actions are always dry-run in this standalone backend and are disabled until explicitly enabled through the safety API.

When OpenCV is installed and `HANDSFREE_CAMERA_ENABLED=true`, call `POST /api/v1/vision/start` to open the selected camera; startup never opens a camera implicitly.

## Contract and integration

- OpenAPI: `http://127.0.0.1:8000/docs`
- Health: `GET /api/v1/health`
- Readiness: `GET /api/v1/ready`
- Session: `GET /api/v1/session`
- Telemetry: `ws://127.0.0.1:8000/api/v1/telemetry`

See [docs/API_CONTRACT.md](docs/API_CONTRACT.md), [docs/TELEMETRY_SCHEMA.md](docs/TELEMETRY_SCHEMA.md), and [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md).

