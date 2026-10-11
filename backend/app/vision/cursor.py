"""Operating-system cursor control through PyAutoGUI.

This is the only module allowed to move the real pointer. It refuses to act
unless the caller has already passed the safety layer, and it clamps every
coordinate to the screen so a bad mapping can never throw the cursor off-screen.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.config import Settings, settings as default_settings
from app.telemetry.log import Telemetry


class CursorController:
    def __init__(
        self,
        telemetry: Telemetry,
        settings: Settings | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._settings = settings or default_settings
        self._lock = threading.RLock()
        self._pyautogui: Any = None
        self._available = False
        self._detail: str | None = None
        self._screen: tuple[int, int] = (0, 0)
        self._last_move = 0.0
        self._probe()

    # ------------------------------------------------------------------ #
    def _probe(self) -> None:
        try:
            import pyautogui  # imported lazily: it pulls in Pillow and friends

            pyautogui.FAILSAFE = False  # head tracking crosses corners constantly
            pyautogui.PAUSE = 0
            size = pyautogui.size()
            self._pyautogui = pyautogui
            self._screen = (int(size.width), int(size.height))
            self._available = True
            self._detail = (
                f"pyautogui ready, screen {self._screen[0]}x{self._screen[1]}"
            )
        except Exception as exc:  # pragma: no cover - depends on host
            self._available = False
            self._detail = f"pyautogui unavailable: {type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def detail(self) -> str | None:
        return self._detail

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._screen

    def refresh_screen_size(self) -> tuple[int, int]:
        """Re-read the screen size (the resolution can change mid-session)."""
        if not self._available:
            return self._screen
        try:
            size = self._pyautogui.size()
            with self._lock:
                self._screen = (int(size.width), int(size.height))
        except Exception as exc:  # pragma: no cover
            self._telemetry.emit(
                "cursor",
                "screen_size_read_failed",
                reason="screen_query_failed",
                data={"error": f"{type(exc).__name__}: {exc}"},
            )
        return self._screen

    # ------------------------------------------------------------------ #
    def to_screen(self, nx: float, ny: float) -> tuple[int, int]:
        """Map normalised [0,1] coordinates to clamped integer screen pixels."""
        width, height = self._screen or (0, 0)
        if width <= 0 or height <= 0:
            return 0, 0
        cx = min(max(nx, 0.0), 1.0)
        cy = min(max(ny, 0.0), 1.0)
        return int(round(cx * (width - 1))), int(round(cy * (height - 1)))

    def move_to(self, nx: float, ny: float) -> tuple[int, int]:
        """Move the OS pointer. Returns the clamped pixel position."""
        screen_x, screen_y = self.to_screen(nx, ny)
        if not self._available:
            return screen_x, screen_y
        if self._settings.dry_run:
            return screen_x, screen_y
        now = time.monotonic()
        if now - self._last_move < self._settings.move_min_interval_s:
            return screen_x, screen_y
        with self._lock:
            self._last_move = now
            try:
                self._pyautogui.moveTo(screen_x, screen_y, _pause=False)
            except Exception as exc:  # pragma: no cover
                self._detail = f"move failed: {type(exc).__name__}: {exc}"
        return screen_x, screen_y

    def click(self, button: str = "left") -> None:
        if not self._available or self._settings.dry_run:
            return
        with self._lock:
            self._pyautogui.click(button=button)

    def scroll(self, clicks: int) -> None:
        """Scroll the window under the pointer (OS level, not the browser agent)."""
        if not self._available or self._settings.dry_run:
            return
        with self._lock:
            self._pyautogui.scroll(int(clicks))

    def position(self) -> tuple[int, int] | None:
        if not self._available:
            return None
        try:
            point = self._pyautogui.position()
            return int(point.x), int(point.y)
        except Exception:  # pragma: no cover
            return None
