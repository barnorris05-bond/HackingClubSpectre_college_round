## Last Updated
2026-10-11 ~01:00 — cleanup verified: only user's own server/window remain; nothing of mine left alive.

## Last Completed
- Fixed `_playwright` crash (detached Playwright methods → bound wrappers in `src/browser.js` + comment).
- Proved Zen can't be Playwright-driven (headless launch hung 25s+, no output — no Juggler protocol in forks): firefox-engine now skips straight to bundled Chromium with a clear warn; brand/fallback launches wrapped in 60s `withTimeout` (+ `launchTimeoutMs` opt, used by tests).
- Reordered `startServer` (control page listens FIRST, browser launches after, launch failure stays up with error broadcast to page) — this was why :8787 never opened during the Zen hang.
- Tests: 65/65 green (45 old + 20 new incl. hang-timeout regression test).
- Real e2e: headless bundled server on :8788 served `/` (200, 18KB) and `/api/state` (live example.com snapshot, 1 element); default-path headed run reached `bundled Chromium (dedicated profile)` after Zen-skip.
- Cleanup: stopped my :8788 test server; killed 9 orphaned ms-playwright chromes stranded by my `Stop-Process -Force` kills; removed `$TEMP\vb-zen-smoke`, `$TEMP\vb-zen-probe`. Verified via parent-PID/port attribution that PID 31780 (`node server.js` on :8787, VS Code terminal parent) + its 8 chrome children + all zen.exe are the USER's — killed nothing there.

## In Progress
- Nothing. User runs their own server (PID 31780 on :8787) with a working controlled window.

## Next Steps
1. User flow (unchanged): local JEV at :8000 must be up for commands to decide; mic page needs Chrome/Edge.
2. Optional follow-ups: force confirmations when `browser.live`; `test:win` + `run.ps1` for PowerShell; graceful shutdown of owned browser on SIGINT already exists — document not to `-Force` kill node (strands profile-locking children; use Ctrl+C).
3. Phase 3 extension (live Zen tabs) still scoped, unbuilt.

## Blockers
- None. Watch item: `MODEL = "jev-1.13.0"` still sent to local JEV; fine unless it validates names.

## File Pointers
- `src/browser.js` — bound launchers, firefox skip, `withTimeout`, `ownsBrowser` close-guard
- `src/server.js` — listen-first order, launch try/catch with page broadcast
- `src/browser-detect.js` — detection/probe/process helpers
- `test/unit/browser-detect.test.js`, `test/unit/browser-modes.test.js` — 20 tests
