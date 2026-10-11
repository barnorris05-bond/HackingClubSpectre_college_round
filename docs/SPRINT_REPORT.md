# Handsfree — sprint verification report

Evidence log for the 60-minute implementation sprint. Every claim below is
backed by a command that was actually run; anything unverified is labelled as
such rather than presented as working.

## 1. Environment

| Item | Value |
| --- | --- |
| OS | Windows 11 |
| Python (`.venv`) | 3.13.15 |
| Node / npm | 24.21.0 / 11.19.0 |
| Screen | 1920 × 1200 (read live by PyAutoGUI) |
| Browsers present | Microsoft Edge, Google Chrome |
| Backend | `http://127.0.0.1:8000` (uvicorn) |
| Frontend | `http://localhost:5173` (Vite 8.3.4 dev server) |

At the start of the sprint the repository contained **only** a working
`backend/app/main.py` (health/status/pause) and an empty frontend. Everything
else in this report was written during the sprint.

## 2. Dependency changes

Installed into `.venv` (all succeeded — nothing failed to install):

```
numpy==2.5.3              opencv-python==5.0.0.93    mediapipe==1.1.0
PyAutoGUI==0.9.54         pillow==12.3.0             playwright==1.63.0
pytest==9.1.1             httpx==0.28.1
```

Two findings worth recording:

1. **MediaPipe 1.1.0 has removed the legacy `mp.solutions` API.** `hasattr(mp,
   "solutions")` returns `False`. The tracker therefore uses
   `mediapipe.tasks.python.vision.FaceLandmarker` in `VIDEO` running mode.
2. **The face landmark model is not bundled in the wheel.** `face_landmarker.task`
   (3,758,596 bytes) was downloaded from Google's model storage into
   `backend/app/vision/models/`. The app reports a structured "vision
   unavailable" state if the file is missing, and still runs.

`backend/requirements.txt` was UTF-16 LE with a BOM and contained only the
original server dependencies. It was rewritten as UTF-8 and now lists the vision
and browser dependencies too. `pip install --dry-run --no-deps --no-index -r
backend/requirements.txt` exits 0 with every requirement satisfied.

`playwright install` was **not** needed: Playwright launches the installed Edge
via `channel="msedge"`.

Frontend: `react@19.3.0`, `react-dom@19.3.0`, `vite@8.3.4`,
`@vitejs/plugin-react@6.1.2`, `typescript@7.0.2`, `@types/react@19.3.0`,
`@types/react-dom@19.3.0`.

## 3. Test results

```
cd backend
..\.venv\Scripts\python.exe -m pytest

115 passed, 1 warning in 8.85s
PYTEST REAL EXIT=0
```

The one warning is from Starlette deprecating httpx's `TestClient`. It is not a
failure and was left alone.

Coverage by file:

| File | Tests | What it pins down |
| --- | --- | --- |
| `test_commands.py` | 40 | Strict exact-match direct click; look-alikes stay browser intents; every structured pattern; determinism |
| `test_mapping.py` | 15 | Neutral subtraction, span mapping, dead zone, clamping, inversion, scale compensation, One Euro behaviour |
| `test_safety.py` | 17 | Every gate precondition and its recorded reason; freeze-on-loss; rejection counting |
| `test_api.py` | 29 | Health/status/pause/e-stop, full voice→click path, dictation isolation, browser routing, settings, evaluation, telemetry |
| `test_pipeline.py` | 14 | Worker lifecycle, tracking loss, calibration gating, camera release, synthetic-source behaviour |

Safety interlock: `backend/tests/conftest.py` sets `dry_run = True` at import
time, so no test can click or move the real pointer even if it were to pass
incorrectly. Tests assert on `ClickDecision` objects and recorded state.

Frontend:

```
cd frontend
npx tsc --noEmit        TYPECHECK REAL EXIT=0
npm run build           BUILD REAL EXIT=0
                        ✓ built in 501ms
                        dist/assets/index-*.js 267.42 kB (gzip 82.87 kB)
```

Exit statuses were captured with `set -o pipefail`; an earlier run reported
`exit=0` from `tail` while the build was in fact failing, which is why the
pipeline status is captured explicitly from here on.

## 4. Verified against real hardware / real services

| Behaviour | Evidence |
| --- | --- |
| MediaPipe FaceLandmarker loads and infers | Model created in 3.12 s; 5.7 ms average per frame on synthetic input |
| OpenCV camera capture | `read ok: True shape: (480, 640, 3)` |
| Vision worker loop | 19.7–24.9 fps sustained, 3.4–6.7 ms inference per frame |
| Camera resource release | `camera status after stop: stopped` |
| PyAutoGUI screen access | `Size(width=1920, height=1200)` |
| Playwright drives real Edge | `msedge OK title: Example Domain` |
| Browser agent navigation | `goto https://example.com` → `{"ok": true, "url": "https://example.com/", "title": "Example Domain"}` |
| Browser agent page read | title `Example Domain`, 1 link, real body text extracted |
| Browser agent link click | `click_target "Learn more"` → strategy `role=link`, navigated to `https://www.iana.org/help/example-domains` |
| Voice → browser navigation | `"open example.com"` → kind `navigate`, executed `true`, page loaded |
| Voice → search | `"search for mechanical keyboards"` → `https://duckduckgo.com/?q=mechanical+keyboards` |
| WebSocket live telemetry | Received `history` (18 events), then a live `('safety', 'cursor_control_enabled')` frame after an API call |
| Frontend ↔ backend | Dashboard renders live `GET /api/status` 200 repeatedly; pointer shows `0,0 of 1920×1200` |
| Frontend renders | Overview + Live Control screenshots; no console errors (one benign StrictMode WebSocket reconnect warning) |

## 5. Verified only with mocks or synthetic input

| Behaviour | How it was verified | Why not on hardware |
| --- | --- | --- |
| Head-pose → cursor geometry | `test_mapping.py`, pure function over synthetic landmarks | No real face was available (see blocker) |
| Calibration completion | Request/cancel/countdown-gating states | Completion requires a detected face |
| Tracking-loss freeze | Synthetic pointer injection: position retained, `tracking_lost` recorded | Same |
| Safety gate allow/deny paths | `test_safety.py` + `POST /api/vision/pointer` injection, `dry_run` on | Deliberately never actuates the real pointer in tests |
| App start without a vision stack | `_available` forced off; app keeps serving and reports the reason | Simulated dependency failure |

## 6. Blocker: no usable camera image

This is the one significant blocker and it is honest to state it plainly.

```
read ok: True  shape: (480, 640, 3)
brightness mean: 22.9   min 22   max 23   std 0.3
```

The camera opens and returns frames, but every frame is a **flat, near-uniform
grey field** (standard deviation 0.3 across 921,600 pixels). There is no scene in
it, so the landmarker correctly finds zero faces. Consequences:

- Face landmark detection never ran against a real face.
- Calibration correctly stayed in "Waiting for a face in view…" and never
  completed.
- The head → cursor mapping was therefore never exercised against live movement.
- No OS click was ever triggered by a *real* uttered command through the
  microphone; the click path was exercised through the API and synthetic pointer
  with the system armed.

Most likely causes, in order: the camera is covered or in privacy/shutter mode, a
virtual camera device is in use, or another application holds the device. This is
a device/environment problem, not a code path that failed.

What **is** proven about the vision code: the landmarker loads, infers in
milliseconds, the worker sustains ~20 fps, tracking-loss is detected, the cursor
freezes instead of jumping, and the gate refuses clicks while tracking is absent.

## 7. Not measured (and not claimed)

- End-to-end voice latency. `voice_end_to_end_ms` is only recorded when the
  client sends a speech-final timestamp; no microphone session was run, so the
  Evaluation screen correctly shows "No measurements recorded yet". No sub-80 ms
  figure is claimed anywhere.
- Task completion time, selection-error rate and accidental-activation counts
  against a human baseline. The mechanism to record them (`/api/evaluation/task/
  start|finish`, plus rejection counting) is implemented and unit-tested, but no
  real sessions were performed.
- MediaPipe's per-frame cost under a real face (a face-present frame is more
  expensive than an empty one). Measured figures are for empty/noise frames.

## 8. Manual steps required from the user

1. **Camera**: uncover the webcam / disable any privacy shutter / close other apps
   that may hold the device, then confirm the Live Control preview shows a real
   scene.
2. **Microphone permission**: the first time you press *Start listening*, the
   browser prompts for microphone access. Web Speech recognition requires Chrome
   or Edge on `localhost` (or HTTPS); other browsers show a clear unsupported
   message and manual text entry still works.
3. **Recalibration** is expected once a real face is visible.

## 9. Note on concurrent edits

While this sprint was running, another agent working in the same checkout added
an Electron shell (`frontend/electron/`) plus a `DynamicIsland` HUD component,
`lucide-react`, and rewired `App.tsx`/`styles.css`. Those changes were left in
place. At one point `DynamicIsland.tsx` failed `tsc` (an unused `React` import, an
unused prop, and a comparison against `clicks.last.ts` which the API does not
expose — it exposes `age_s`); the other agent corrected them before further
changes were needed. The build and typecheck results in §3 were re-run **after**
those edits and reflect the combined state.

## 10. Status summary

**Implemented and verified**

- FastAPI backend; `/health`, `/api/status` and pause still work as before.
- Centralised state model governing pause and safety for both pipelines.
- Deterministic voice classification with strict exact-match direct clicks.
- Safety-gated click and scroll dispatch recording `tracking_lost`,
  `pointer_unstable`, `paused`, `cooldown`, `not_calibrated`,
  `cursor_control_disabled`, `emergency_stop`, `cursor_stale`,
  `low_tracking_quality`, `cursor_backend_unavailable`.
- Dictation mode fully separated from command handling.
- Playwright browser agent: navigate, read, click by role/text, type, scroll,
  history, tabs, screenshot.
- Telemetry: JSONL log with rotation, ring buffer, WebSocket fan-out, counters by
  kind and message, measured duration series.
- Evaluation from real events only, with explicit "not measured" states.
- Frontend with Overview, Live Control, Browser Agent, Evaluation and Settings.

**Partially implemented / partially verified**

- Head tracking: pipeline, calibration, smoothing, dead zone, clamping and
  freeze-on-loss are implemented and tested — but never against a real face.
- Real OS pointer actuation: implemented and gated, never executed against the
  desktop (tests are dry-run; the live server ships with cursor control off).

**Deferred**

- Dwell-to-activate as an alternative confirmation mechanism (deliberate voice
  activation is the implemented mechanism).
- Head-pose adaptation to changing lighting (roll and distance-adaptation exist;
  lighting adaptation does not).
- Human-factors evaluation runs, and therefore any accuracy/latency comparison
  against mouse and keyboard.

## 11. Recommended next priority

**Get one real face through the full loop.** Everything downstream is built,
tested and instrumented; the single missing link is a working camera image. Once
a face is detected, in this order: complete a calibration, confirm the cursor
tracks, enable the OS pointer, then run a timed task session in both input modes
so the Evaluation screen fills with genuine measurements.
