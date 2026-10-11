# Handsfree — Developer & Teammate Handoff Guide

Welcome to **Handsfree: Voice and Webcam-Based Browsing**.
This document is designed for the incoming developer or team member picking up the project directly from GitHub. It summarizes the architecture, verified working capabilities, demo instructions, known gaps, and pre-push status.

---

## 1. System Architecture Overview

Handsfree is a Windows-first desktop application providing hands-free computing through two complementary pipelines governed by a strict central safety gate:

```
[Webcam 640x480 @ 30fps]                [Microphone / Web Speech API]
           │                                          │
           ▼                                          ▼
[MediaPipe FaceLandmarker]              [Deterministic Classifier]
  (468 landmarks, 4-8ms)                   ├─ Direct Click (Exact phrase match)
           │                               ├─ Browser Intent (Regex pattern match)
           ▼                               └─ Dictation (Direct text transcription)
[Neutral Calibration & 1€ Filter]                     │
           │                                          │
           ▼                                          ▼
[Safety Gate: Pause / E-Stop / Stability / Lost Tracking / Cooldown]
           │                                          │
           ▼                                          ▼
 [OS Cursor (PyAutoGUI)]                    [Playwright Browser Agent]
  (Off by default for safety)                (Isolated Chromium / Edge)
```

### Core Architecture Principles
1. **Safety Gate Everywhere:** OS pointer movements, OS clicks, and Playwright browser actions ALL pass through `backend/app/control/safety.py` and `backend/app/browser/agent.py`. If paused or under emergency stop, every action is rejected with explicit reason telemetry.
2. **Deterministic Voice Classification:** Spoken input is never passed blindly to an LLM or fuzzy matcher for pointer clicks. Clicks require an exact phrase match (`click`, `left click`, `right click`). Natural language sentences (`click the login button`, `search for news`) are strictly routed to the Playwright browser agent, never the OS pointer.
3. **Dual-Window Electron Shell:** Electron (`frontend/electron/main.cjs`) runs two windows:
   - **Dashboard Window (`800x600`):** Full control interface with 5 screens: Overview, Live Control, Browser Agent, Evaluation, and Settings.
   - **HUD Window (`400x120`, transparent, frameless, always-on-top):** Floating Dynamic Island widget displaying real-time system state (tracking confidence, listening wave, action execution/block reasons, pause/resume).

---

## 2. Quickstart & How to Run

### Prerequisites
- **OS:** Windows 10/11 (64-bit).
- **Python:** 3.13 (virtual environment provided in `.venv/`).
- **Node.js:** v20+ or v24+.
- **Browser:** Microsoft Edge or Google Chrome installed.
- **Hardware:** Webcam and microphone.

### Step 1: Start Backend (FastAPI API Server)
In a dedicated PowerShell terminal:
```powershell
# From the repository root
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```
- API health check: `http://127.0.0.1:8000/health` → `{"status": "ok"}`
- Interactive Swagger docs: `http://127.0.0.1:8000/docs`
- Status snapshot: `http://127.0.0.1:8000/api/status`

### Step 2: Start Desktop Application (Electron + React)
In a second PowerShell terminal:
```powershell
cd frontend
npm run desktop
```
*Note: For browser-only frontend preview without the floating desktop HUD, you can run `npm run dev` and open `http://localhost:5173`.*

---

## 3. Verified Capabilities vs. Deferred / Known Gaps

| Feature / Capability | Status | Evidence / Verification Notes |
|---|---|---|
| **FastAPI REST & WebSocket API** | **Verified** | 117 automated pytest tests passing cleanly in 9.1s. Endpoints bound to `127.0.0.1`. |
| **Centralized Safety Gate** | **Verified** | Unit & regression tests confirm pause & e-stop block both OS clicks and browser tasks. |
| **Electron Startup** | **Verified** | CommonJS entry (`main.cjs` / `preload.cjs`) loads Vite dist cleanly with code 0 (`--test-startup`). |
| **Live Webcam Capture** | **Verified** | Live capture on camera 0 verified with non-uniform image scene (mean 51.9, std 13.7). |
| **MediaPipe FaceLandmarker** | **Verified** | Real face detected with `face_landmarker.task` running on Google TLite XNNPACK. |
| **Dynamic Island HUD** | **Verified** | Accurately renders 9 states; displays coral `Action Blocked (reason)` on rejection; never fakes success. |
| **Playwright Browser Agent** | **Verified** | Playwright installed; handles navigation, typing, searching, and tab management with safety guards. |
| **Evaluation Honesty** | **Verified** | Empty series dynamically report `measured: false`; no fabricated benchmark numbers. |
| **Web Speech Recognition** | **Implemented** | Web Speech API implemented in UI; requires browser microphone permission prompt during demo. |
| **System-wide Desktop Dictation** | **Deferred** | Dictation accumulates in frontend buffer; direct OS keyboard injection into 3rd-party apps deferred. |
| **Offline Speech Recognition** | **Deferred** | Project uses Chrome/Edge Web Speech API; offline Vosk/Whisper models are not integrated. |

---

## 4. Live Demonstration Guide (Step-by-Step)

If demonstrating Handsfree to judges or users:

1. **Camera & Lighting Setup:** Ensure standard ambient room lighting. Ensure the laptop or USB webcam is pointed directly at the user's face.
2. **Launch Applications:** Start backend terminal (`uvicorn`) followed by frontend terminal (`npm run desktop`).
3. **Calibrate Neutral Head Pose:**
   - On the **Live Control** tab, click **Start camera**. Verify live video feed and tracking reticle appear.
   - Click **Start calibration**. Hold head still facing the center of the screen for 5 seconds.
4. **Demonstrate Dynamic Island HUD:**
   - Point out the floating pill in the top-center of the screen.
   - Click **Pause** in the UI or say `"pause"` → pill changes to amber `"Paused"`.
   - Click **Resume** → pill turns green `"Tracking"`.
5. **Demonstrate Safety Gate:**
   - While paused, try saying `"scroll down"` or clicking a browser action.
   - Observe the Dynamic Island displaying coral `"Action Blocked (paused)"`, proving safety enforcement.
6. **Demonstrate Browser Agent:**
   - Switch to the **Browser Agent** tab.
   - Click **Start browser**.
   - Speak or trigger `"search for mechanical keyboards"` or `"open example.com"`.
7. **Enable OS Pointer (Optional / Supervised):**
   - Head over to **Settings** or **Live Control**.
   - Check the box **"Enable OS cursor control"**.
   - Move head gently: cursor tracks head motion via One Euro filter smoothing.
   - Saying `"click"` triggers an OS click only if tracking is stable and face is centered.
   - Press **Emergency stop** to instantly freeze the pointer.

---

## 5. Development & Testing Commands

```powershell
# Run backend pytest suite (all 117 tests)
.\.venv\Scripts\python.exe -m pytest -v

# Run frontend TypeScript typecheck
cd frontend
npm run typecheck

# Run frontend production build
npm run build

# Run Electron headless startup smoke test
node -e "const cp = require('child_process'); const p = cp.spawn(require('electron'), ['.', '--test-startup'], { stdio: 'inherit' }); p.on('exit', c => process.exit(c || 0));"
```

---

## 6. Pre-Push Git Status

- **Branch:** `main`
- **Working Tree:** All code edits are in working directory, clean of temporary test artifacts or corrupted dependencies.
- **Ready for Push:** The user can review changes with `git status` / `git diff` and push to GitHub (`https://github.com/barnorris05-bond/HackingClubSpectre_college_round`).
