# Handsfree Notch Lab

A standalone Windows desktop application prototype combining:
- **Compact Floating Dynamic Island:** Positioned at the top-centre of your primary screen.
- **Siri-Inspired Liquid Glass Wave:** Dynamic activation morph with multi-layered canvas orb and specular optics.
- **VoiceOS-Inspired Contextual HUD:** Mode switching between Voice, Dictation stream demo, and Region Selection.
- **Restrained Blue Context-Selection Trail:** Canvas-powered quadratic cursor trail with bounding box capture.
- **Emergency Stop Safety Interlock:** Instant safety state with distinct warm-red styling.

---

## 1. Project Architecture

- **Electron 34:** Frameless transparent window (`backgroundColor: '#00000000'`), `alwaysOnTop: 'screen-saver'`, `skipTaskbar: true`.
- **React 19 & TypeScript:** State machine architecture managing typed visual states (`idle`, `activating`, `listening`, `dictation_demo`, `context_selecting`, `processing_demo`, `response_demo`, `emergency_stop`).
- **Liquid Glass Surface System:** CSS backdrop blur, specular lighting gradient responding to cursor coordinates, warm Handsfree palette (`#171719`, `#C65D46`, `#FAF7F2`, `#69A7FF`).
- **Web Audio API:** User-initiated microphone visualizer with `AnalyserNode` and graceful fallback simulation mode.
- **Secure Preload Bridge:** `contextIsolation: true`, restricted typed IPC channels (`notch:resize`, `notch:enter-context-selection`, `notch:exit-context-selection`).

---

## 2. Quickstart

### Prerequisites
- Node.js (v20+ or v24+)
- npm (v10+)

### Development Mode
```powershell
npm run build
npm start
```

Or run the electron development build:
```powershell
npm run electron:dev
```

---

## 3. Keyboard Shortcuts & Controls

- **Activation:** Click the notch capsule or press **Ctrl+Shift+Space** / **Cmd+Shift+Space**.
- **Cancel / Escape:** Press **ESC** to collapse from expanded or context-selection mode.
- **HUD Diagnostics:** Toggle the `HUD Info` button inside the expanded notch for state and pipeline telemetry.
- **Emergency Stop:** Click the `Stop` button in the secondary pill row to disarm all simulated assistants immediately.

---

## 4. Acceptance Criteria & Honest Scope Disclosure

1. **Launch Experience:** Opens directly into the compact 224x48px capsule. No dashboard or landing page.
2. **Transparent Overlay:** Stays above other open applications (Chrome, code editors) without black rectangular borders.
3. **Audio Reactivity:** Hardware microphone access is requested strictly upon user click; if denied or idle, clearly labeled simulation mode runs.
4. **Context Selection:** Screen drag reveals the blue cursor glow trail and confirms coordinate bounding boxes.
5. **Honest Scope:** Demonstrative prototype; no external cloud AI or OS dictation text injection is performed in this standalone frontend lab.
