"""Safety gate for pointer actuation (requirement 4).

Protection against accidental activation uses *deliberate voice activation*
rather than dwell: a click only happens on an isolated, exact-match spoken
command, and even then it must pass every check below. Every attempt - allowed
or rejected - is recorded with a machine-readable reason so the Evaluation
screen reports real selection-error and accidental-activation numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, settings as default_settings
from app.control.state import ClickRecord, ControlState
from app.telemetry.log import Telemetry
from app.vision.cursor import CursorController

# Reason codes surfaced to the UI and counted in telemetry.
REASON_OK = "ok"
REASON_EMERGENCY_STOP = "emergency_stop"
REASON_CURSOR_DISABLED = "cursor_control_disabled"
REASON_PAUSED = "paused"
REASON_BACKEND_UNAVAILABLE = "cursor_backend_unavailable"
REASON_TRACKING_LOST = "tracking_lost"
REASON_NOT_CALIBRATED = "not_calibrated"
REASON_CURSOR_STALE = "cursor_stale"
REASON_POINTER_UNSTABLE = "pointer_unstable"
REASON_LOW_QUALITY = "low_tracking_quality"
REASON_COOLDOWN = "cooldown"


@dataclass
class ClickDecision:
    allowed: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "detail": self.detail}


class ClickDispatcher:
    """Evaluates and performs clicks. The only route to a real pointer click."""

    def __init__(
        self,
        state: ControlState,
        telemetry: Telemetry,
        cursor: CursorController,
        settings: Settings | None = None,
    ) -> None:
        self._state = state
        self._telemetry = telemetry
        self._cursor = cursor
        self._settings = settings or default_settings

    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        ignore_cooldown: bool = False,
        require_stability: bool = True,
        require_quality: bool = True,
    ) -> ClickDecision:
        """Check every precondition. Returns the first failing reason.

        ``require_stability``/``require_quality`` are relaxed for non-click
        actuation such as scrolling, which does not need a rock-steady pointer.
        """
        state = self._state
        settings = self._settings

        if state.emergency_stop:
            return ClickDecision(False, REASON_EMERGENCY_STOP)
        if not state.cursor_control_enabled:
            return ClickDecision(
                False,
                REASON_CURSOR_DISABLED,
                {"hint": "enable cursor control (POST /api/control/cursor)"},
            )
        if state.paused:
            return ClickDecision(False, REASON_PAUSED)
        if not self._cursor.available:
            return ClickDecision(
                False,
                REASON_BACKEND_UNAVAILABLE,
                {"detail": self._cursor.detail},
            )
        if settings.require_calibration_for_click and not state.calibrated:
            return ClickDecision(False, REASON_NOT_CALIBRATED)

        if not state.tracking:
            return ClickDecision(False, REASON_TRACKING_LOST, {"tracking": state.tracking_reason})
        if state.tracking_reason not in ("ok", "grace_period"):
            return ClickDecision(
                False, REASON_TRACKING_LOST, {"tracking": state.tracking_reason}
            )

        age_ms = (
            None
            if state.cursor_updated_at is None
            else (time.monotonic() - state.cursor_updated_at) * 1000.0
        )
        if (
            not state.cursor_valid
            or age_ms is None
            or age_ms > settings.tracking_loss_grace_s * 1000.0
        ):
            return ClickDecision(
                False,
                REASON_CURSOR_STALE,
                {"age_ms": None if age_ms is None else round(age_ms, 1)},
            )

        stability = state.pointer_stability()
        if require_stability and not stability["stable"]:
            return ClickDecision(False, REASON_POINTER_UNSTABLE, stability)

        if require_quality and state.tracking_quality < settings.min_tracking_quality_for_click:
            return ClickDecision(
                False,
                REASON_LOW_QUALITY,
                {
                    "quality": round(state.tracking_quality, 3),
                    "required": settings.min_tracking_quality_for_click,
                },
            )

        if not ignore_cooldown:
            last = state.last_click
            if last is not None and last.allowed:
                remaining = settings.click_cooldown_s - (time.monotonic() - last.ts)
                if remaining > 0:
                    return ClickDecision(
                        False, REASON_COOLDOWN, {"remaining_s": round(remaining, 2)}
                    )

        return ClickDecision(True, REASON_OK, {"stability": stability})

    # ------------------------------------------------------------------ #
    def dispatch(
        self,
        trigger: str = "voice",
        button: str = "left",
        received_at: float | None = None,
        ignore_cooldown: bool = False,
        require_stability: bool = True,
        require_quality: bool = True,
        scroll: int | None = None,
    ) -> ClickDecision:
        """Evaluate then actuate. Records telemetry and measured latency.

        ``scroll`` turns the same gate into a scroll actuation instead of a
        click, so both actions share one safety policy.
        """
        # The lock makes evaluate-then-act atomic against pause/e-stop.
        with self._state.lock:
            decision = self.evaluate(
                ignore_cooldown=ignore_cooldown,
                require_stability=require_stability,
                require_quality=require_quality,
            )
            executed = False
            if decision.allowed:
                if scroll is None:
                    self._cursor.click(button=button)
                else:
                    self._cursor.scroll(scroll)
                executed = True
            snapshot_x = self._state.cursor_nx
            snapshot_y = self._state.cursor_ny
            screen_x = self._state.cursor_screen_x
            screen_y = self._state.cursor_screen_y
            self._state.record_click(
                ClickRecord(
                    ts=time.monotonic(),
                    allowed=decision.allowed,
                    reason=decision.reason,
                    button=button,
                    nx=snapshot_x,
                    ny=snapshot_y,
                    screen_x=screen_x,
                    screen_y=screen_y,
                    trigger=trigger,
                )
            )

        latency_ms = None
        if received_at is not None:
            latency_ms = (time.monotonic() - received_at) * 1000.0
            self._telemetry.record_duration("click_dispatch_ms", latency_ms)

        payload = {
            "trigger": trigger,
            "button": button,
            "scroll": scroll,
            "screen": [screen_x, screen_y],
            "nx": round(snapshot_x, 4),
            "ny": round(snapshot_y, 4),
            "latency_ms": None if latency_ms is None else round(latency_ms, 2),
            "executed": executed,
            **decision.detail,
        }
        if decision.allowed:
            self._telemetry.emit(
                "click" if scroll is None else "scroll",
                "click_executed" if scroll is None else "os_scroll_executed",
                data=payload,
            )
        else:
            self._telemetry.emit(
                "click",
                "click_rejected" if scroll is None else "os_scroll_rejected",
                reason=decision.reason,
                data=payload,
            )
        return decision
