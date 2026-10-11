"""Centralised session state: the single source of truth for pause, safety,
cursor position, tracking health, voice mode and calibration.

Every subsystem reads and writes through this object, which is why the pause
switch and emergency stop are able to gate *both* input pipelines.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Literal

from app.config import Settings, settings as default_settings
from app.telemetry.log import Telemetry

VisionStatus = Literal[
    "not_initialized",
    "starting",
    "running",
    "unavailable",
    "error",
    "stopped",
]
VoiceMode = Literal["idle", "dictation"]
CalibrationState = Literal["idle", "countdown", "sampling", "ready", "failed"]


@dataclass
class PointerSample:
    t: float
    nx: float
    ny: float
    screen_x: int
    screen_y: int


@dataclass
class ClickRecord:
    ts: float
    allowed: bool
    reason: str
    button: str
    nx: float
    ny: float
    screen_x: int
    screen_y: int
    trigger: str


class ControlState:
    def __init__(
        self,
        telemetry: Telemetry,
        settings: Settings | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._settings = settings or default_settings
        self._lock = threading.RLock()

        # -- the safety switches. Every pipeline checks these. -------------
        self.paused = True  # default paused: nothing acts until asked to
        self.emergency_stop = False
        self.cursor_control_enabled = bool(self._settings.enable_os_cursor)
        self.pointer_injection_enabled = False

        # -- status --------------------------------------------------------
        self.camera_status: VisionStatus = "not_initialized"
        self.camera_detail: str | None = None
        self.vision_available = True
        self.vision_unavailable_reason: str | None = None
        self.cursor_backend_available = False
        self.cursor_backend_detail: str | None = None
        self.tracking = False
        self.tracking_reason = "not_started"
        self.tracking_quality = 0.0
        self.faces_detected = 0

        # -- calibration ---------------------------------------------------
        self.calibration_state: CalibrationState = "idle"
        self.calibrated = False
        self.calibration_progress = 0.0
        self.calibrated_at: str | None = None
        self.calibration_detail: str | None = None
        self.neutral: tuple[float, float] | None = None

        # -- pointer -------------------------------------------------------
        self.cursor_nx = 0.5
        self.cursor_ny = 0.5
        self.cursor_screen_x = 0
        self.cursor_screen_y = 0
        self.cursor_valid = False
        self.cursor_updated_at: float | None = None
        self.screen_size: tuple[int, int] = (0, 0)
        self.trail: Deque[PointerSample] = deque(maxlen=240)

        # -- clicks --------------------------------------------------------
        self.last_click: ClickRecord | None = None
        self.click_count = 0
        self.rejected_click_count = 0
        self.last_reject_reason: str | None = None

        # -- voice ---------------------------------------------------------
        self.voice_mode: VoiceMode = "idle"
        self.dictation_text = ""
        self.last_command: str | None = None
        self.last_command_kind: str | None = None
        self.last_command_at: str | None = None
        self.last_command_executed: bool | None = None
        self.last_command_result: dict[str, Any] | None = None
        self.last_transcript: str | None = None

        # -- browser -------------------------------------------------------
        self.browser_status = "not_started"
        self.browser_detail: str | None = None
        self.browser_url: str | None = None

        # -- friction log --------------------------------------------------
        self.events_count = 0
        self._last_updated: str | None = None

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paused": self.paused,
                "emergency_stop": self.emergency_stop,
                "cursor_control_enabled": self.cursor_control_enabled,
                "pointer_injection_enabled": self.pointer_injection_enabled,
                "camera": {
                    "status": self.camera_status,
                    "detail": self.camera_detail,
                    "available": self.vision_available,
                    "unavailable_reason": self.vision_unavailable_reason,
                },
                "tracking": {
                    "active": self.tracking,
                    "reason": self.tracking_reason,
                    "quality": round(self.tracking_quality, 3),
                    "faces": self.faces_detected,
                    "quality_note": (
                        "0-1 heuristic from detection presence and pointer "
                        "steadiness. MediaPipe does not expose a calibrated "
                        "probability of correctness, so this is not one."
                    ),
                },
                "calibration": {
                    "state": self.calibration_state,
                    "calibrated": self.calibrated,
                    "progress": round(self.calibration_progress, 3),
                    "calibrated_at": self.calibrated_at,
                    "detail": self.calibration_detail,
                    "neutral": self.neutral,
                },
                "cursor": {
                    "nx": round(self.cursor_nx, 5),
                    "ny": round(self.cursor_ny, 5),
                    "screen_x": self.cursor_screen_x,
                    "screen_y": self.cursor_screen_y,
                    "valid": self.cursor_valid,
                    "age_ms": (
                        None
                        if self.cursor_updated_at is None
                        else int((time.monotonic() - self.cursor_updated_at) * 1000)
                    ),
                    "backend_available": self.cursor_backend_available,
                    "backend_detail": self.cursor_backend_detail,
                    "screen_size": list(self.screen_size),
                },
                "clicks": {
                    "executed": self.click_count,
                    "rejected": self.rejected_click_count,
                    "last_reject_reason": self.last_reject_reason,
                    "last": (
                        None
                        if self.last_click is None
                        else {**asdict(self.last_click), "age_s": round(
                            time.monotonic() - self.last_click.ts, 2
                        )}
                    ),
                },
                "voice": {
                    "mode": self.voice_mode,
                    "dictation_length": len(self.dictation_text),
                    "dictation_text": self.dictation_text,
                    "last_transcript": self.last_transcript,
                    "last_command": self.last_command,
                    "last_command_kind": self.last_command_kind,
                    "last_command_at": self.last_command_at,
                    "last_command_executed": self.last_command_executed,
                    "last_command_result": self.last_command_result,
                },
                "browser": {
                    "status": self.browser_status,
                    "detail": self.browser_detail,
                    "url": self.browser_url,
                },
                "last_updated": self._last_updated,
                "server_time": self._now(),
            }

    def _touch(self) -> None:
        self._last_updated = self._now()

    # ------------------------------------------------------------------ #
    # safety switches
    # ------------------------------------------------------------------ #
    def set_paused(self, paused: bool, source: str = "api") -> dict[str, Any]:
        with self._lock:
            if self.paused == paused:
                self._touch()
                return self.snapshot()
            self.paused = paused
            if paused:
                self.tracking = False
                self.tracking_reason = "paused"
            self._touch()
        self._telemetry.emit(
            "safety",
            "paused" if paused else "resumed",
            data={"source": source},
        )
        return self.snapshot()

    def set_emergency_stop(self, active: bool, source: str = "api") -> dict[str, Any]:
        with self._lock:
            self.emergency_stop = active
            if active:
                # Emergency stop is stronger than pause: it also disarms the OS
                # cursor and latches pause. Clearing the stop alone must never
                # resume browser actions or pointer control.
                self.cursor_control_enabled = False
                self.paused = True
                self.tracking = False
                self.tracking_reason = "emergency_stop"
            self._touch()
        self._telemetry.emit(
            "safety",
            "emergency_stop_on" if active else "emergency_stop_cleared",
            data={"source": source},
        )
        return self.snapshot()

    def set_cursor_control(self, enabled: bool, source: str = "api") -> dict[str, Any]:
        with self._lock:
            if enabled and self.emergency_stop:
                self._telemetry.emit(
                    "safety",
                    "cursor_enable_blocked",
                    reason="emergency_stop",
                    data={"source": source},
                )
                return self.snapshot()
            self.cursor_control_enabled = enabled
            self._touch()
        self._telemetry.emit(
            "safety",
            "cursor_control_enabled" if enabled else "cursor_control_disabled",
            data={"source": source},
        )
        return self.snapshot()

    # ------------------------------------------------------------------ #
    # vision
    # ------------------------------------------------------------------ #
    def set_camera_status(
        self,
        status: VisionStatus,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            self.camera_status = status
            self.camera_detail = detail
            self._touch()

    def set_vision_availability(self, available: bool, reason: str | None) -> None:
        with self._lock:
            self.vision_available = available
            self.vision_unavailable_reason = reason
            if not available:
                self.camera_status = "unavailable"
            self._touch()

    def set_cursor_backend(self, available: bool, detail: str | None) -> None:
        with self._lock:
            self.cursor_backend_available = available
            self.cursor_backend_detail = detail
            self._touch()

    def set_tracking(
        self,
        active: bool,
        reason: str,
        quality: float | None = None,
        faces: int | None = None,
    ) -> None:
        with self._lock:
            changed = self.tracking != active or reason != self.tracking_reason
            self.tracking = active
            self.tracking_reason = reason
            if quality is not None:
                self.tracking_quality = max(0.0, min(1.0, quality))
            if faces is not None:
                self.faces_detected = faces
            self._touch()
        if changed:
            self._telemetry.emit(
                "tracking",
                "tracking_present" if active else "tracking_lost",
                reason=reason,
                data={"faces": faces, "quality": None if quality is None else round(quality, 3)},
            )

    # ------------------------------------------------------------------ #
    # pointer
    # ------------------------------------------------------------------ #
    def set_screen_size(self, width: int, height: int) -> None:
        with self._lock:
            self.screen_size = (width, height)
            self.cursor_screen_x = min(self.cursor_screen_x, max(width - 1, 0))
            self.cursor_screen_y = min(self.cursor_screen_y, max(height - 1, 0))

    def record_pointer(
        self,
        nx: float,
        ny: float,
        screen_x: int,
        screen_y: int,
    ) -> None:
        """Record a *valid* pointer observation. Never call this with a guess."""
        with self._lock:
            self.cursor_nx = nx
            self.cursor_ny = ny
            self.cursor_screen_x = screen_x
            self.cursor_screen_y = screen_y
            self.cursor_valid = True
            self.cursor_updated_at = time.monotonic()
            self.trail.append(
                PointerSample(time.monotonic(), nx, ny, screen_x, screen_y)
            )
            self._touch()

    def freeze_pointer(self, reason: str) -> None:
        """Keep the last valid position but mark it stale.

        Tracking loss must never move the cursor; it only stops updating it.
        """
        with self._lock:
            valid_recent = (
                self.cursor_updated_at is not None
                and (time.monotonic() - self.cursor_updated_at)
                <= self._settings.tracking_loss_grace_s
            )
            self.cursor_valid = valid_recent
            self._touch()

    def pointer_stability(self) -> dict[str, Any]:
        """Real displacement of the pointer over the configured stability window."""
        with self._lock:
            now = time.monotonic()
            window = self._settings.stability_window_s
            samples = [s for s in self.trail if now - s.t <= window]
        if len(samples) < 2:
            return {
                "samples": len(samples),
                "radius_px": None,
                "stable": False,
                "detail": "not enough pointer samples yet",
            }
        cx = sum(s.screen_x for s in samples) / len(samples)
        cy = sum(s.screen_y for s in samples) / len(samples)
        radius = max(
            math.hypot(s.screen_x - cx, s.screen_y - cy) for s in samples
        )
        return {
            "samples": len(samples),
            "radius_px": round(radius, 1),
            "threshold_px": self._settings.stability_max_radius_px,
            "stable": radius <= self._settings.stability_max_radius_px,
            "detail": f"max deviation {radius:.1f}px over {window:.2f}s",
        }

    # ------------------------------------------------------------------ #
    # clicks
    # ------------------------------------------------------------------ #
    def record_click(self, record: ClickRecord) -> None:
        with self._lock:
            self.last_click = record
            if record.allowed:
                self.click_count += 1
            else:
                self.rejected_click_count += 1
                self.last_reject_reason = record.reason
            self._touch()

    def reset_evaluation(self) -> None:
        with self._lock:
            self.click_count = 0
            self.rejected_click_count = 0
            self.last_reject_reason = None
            self.last_click = None
            self.trail.clear()
            self.events_count = 0

    # ------------------------------------------------------------------ #
    # voice / dictation
    # ------------------------------------------------------------------ #
    def set_voice_mode(self, mode: VoiceMode, source: str = "api") -> dict[str, Any]:
        with self._lock:
            self.voice_mode = mode
            self._touch()
        self._telemetry.emit(
            "voice",
            f"mode_{mode}",
            data={"source": source},
        )
        return self.snapshot()

    def set_dictation(self, text: str) -> None:
        with self._lock:
            self.dictation_text = text
            self._touch()

    def append_dictation(self, text: str) -> str:
        with self._lock:
            text = text.strip()
            if not text:
                return self.dictation_text
            if self.dictation_text and not self.dictation_text.endswith((" ", "\n")):
                self.dictation_text += " "
            self.dictation_text += text
            self._touch()
            return self.dictation_text

    def record_command(
        self,
        transcript: str,
        kind: str,
        detail: dict[str, Any] | None = None,
        executed: bool | None = None,
    ) -> None:
        with self._lock:
            self.last_transcript = transcript
            self.last_command = transcript
            self.last_command_kind = kind
            self.last_command_at = self._now()
            self.last_command_executed = executed
            self.last_command_result = detail
            self._touch()
