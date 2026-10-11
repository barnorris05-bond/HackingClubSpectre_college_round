#!/usr/bin/env bash
# Launch the voice-browser server with the API key exported server-side.
# Usage: ./run.sh [--port 8787] [--host 127.0.0.1] [--headless] [--cdp ws://...] [--start-url https://...]
#                 [--browser auto|chrome|edge|zen|firefox|chromium] [--cdp-port 9222]
#
# Browser: by default (--browser auto) the OS default browser is detected and driven.
# Chromium browsers (Chrome/Edge) attach to your live tabs when possible; Zen/Firefox
# launch in a dedicated automation profile (Playwright cannot attach to a live Firefox).
# Set BROWSER=chromium (or pass --browser) to force the bundled Chromium window.
# The key is read from the TYPESAFE_API_KEY environment variable, or from a local .env file
# (copy .env.example to .env). It is never sent to the control page in the browser.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -z "${JEV_API_KEY:-}" ] && [ -f .env ]; then
  set -a; . ./.env; set +a
fi
# Local-first: JEV runs at http://127.0.0.1:8000 and needs no key (dummy is sent, ignored).
# Only require a key when TYPESAFE_BASE_URL points away from localhost.
case "${TYPESAFE_BASE_URL:-http://127.0.0.1:8000}" in
  *127.0.0.1*|*localhost*)
    ;;
  *)
    if [ -z "${TYPESAFE_API_KEY:-}" ] && [ -z "${JEV_API_KEY:-}" ]; then
      echo "No API key found for cloud JEV. Set TYPESAFE_API_KEY, or create .env from .env.example (get a key at https://console.typesafe.ai/keys)" >&2
      exit 1
    fi
    ;;
esac

exec node src/server.js "$@"
