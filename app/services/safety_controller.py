from dataclasses import dataclass

from app.models.commands import CommandKind
from app.models.session import SessionState, TrackingStatus


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason_code: str | None = None
    message: str = "Allowed"


class SafetyController:
    """The single authority for protected cursor and browser actions."""

    def __init__(self, min_confidence: float = 0.7, stability_radius_px: float = 20.0):
        self.min_confidence = min_confidence
        self.stability_radius_px = stability_radius_px

    def authorize(self, state: SessionState, kind: CommandKind, *, target_stable: bool = False, valid_command: bool = True) -> SafetyDecision:
        if state.emergency_stopped:
            return SafetyDecision(False, "EMERGENCY_STOP", "Emergency stop is latched")
        if state.paused:
            return SafetyDecision(False, "SESSION_PAUSED", "Session is paused")
        if not valid_command:
            return SafetyDecision(False, "INVALID_COMMAND", "Command is not valid for this action")
        if kind == CommandKind.DIRECT_CLICK:
            if not state.cursor_control_enabled:
                return SafetyDecision(False, "CURSOR_CONTROL_DISABLED", "Cursor control is disabled")
            if state.tracking_status != TrackingStatus.TRACKING:
                return SafetyDecision(False, "TRACKING_UNAVAILABLE", "Stable face tracking is required")
            if state.tracking_confidence is None or state.tracking_confidence < self.min_confidence:
                return SafetyDecision(False, "LOW_TRACKING_CONFIDENCE", "Tracking confidence is below the safety threshold")
            if not state.calibrated:
                return SafetyDecision(False, "CALIBRATION_INVALID", "Valid calibration is required")
            if not target_stable:
                return SafetyDecision(False, "TARGET_UNSTABLE", "Target moved beyond the stability threshold")
        elif kind == CommandKind.BROWSER and not state.browser_permission_enabled:
            return SafetyDecision(False, "BROWSER_PERMISSION_DISABLED", "Browser-agent permission is disabled")
        return SafetyDecision(True)

