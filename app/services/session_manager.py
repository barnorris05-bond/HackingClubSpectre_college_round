from datetime import datetime, timezone
from uuid import uuid4

from app.models.events import EventType
from app.models.session import SessionLifecycle, SessionState, ServiceStatus, TrackingStatus
from app.services.telemetry_service import TelemetryService


class SessionManager:
    def __init__(self, telemetry: TelemetryService):
        now = datetime.now(timezone.utc)
        self.telemetry = telemetry
        self.state = SessionState(session_id=str(uuid4()), started_at=now, updated_at=now)
        self.tracking_reset_generation = 0

    def snapshot(self) -> SessionState:
        return self.state.model_copy(deep=True)

    async def _publish(self, event_type: EventType, data: dict) -> None:
        self.state.updated_at = datetime.now(timezone.utc)
        await self.telemetry.publish(event_type, self.state.session_id, data)

    async def start(self) -> None:
        self.state.lifecycle = SessionLifecycle.ACTIVE
        await self._publish(EventType.SESSION_STATE, {"state": self.state.model_dump(mode="json")})

    async def pause(self) -> SessionState:
        self.state.paused = True
        self._invalidate_tracking_stability()
        await self._publish(EventType.SESSION_PAUSED, {"reason": "explicit_pause"})
        return self.snapshot()

    async def resume(self) -> SessionState:
        if not self.state.emergency_stopped:
            self.state.paused = False
        await self._publish(EventType.SESSION_RESUMED, {"emergency_stopped": self.state.emergency_stopped})
        return self.snapshot()

    async def emergency_stop(self) -> SessionState:
        self.state.emergency_stopped = True
        self.state.paused = True
        self.state.cursor_control_enabled = False
        self._invalidate_tracking_stability()
        await self._publish(EventType.SESSION_EMERGENCY_STOP, {"latched": True})
        return self.snapshot()

    async def recover(self) -> SessionState:
        self.state.emergency_stopped = False
        self.state.paused = False
        self._invalidate_tracking_stability()
        await self._publish(EventType.SESSION_STATE, {"recovered": True, "state": self.state.model_dump(mode="json")})
        return self.snapshot()

    async def set_cursor_permission(self, enabled: bool) -> SessionState:
        self.state.cursor_control_enabled = enabled and not self.state.emergency_stopped and not self.state.paused
        await self._publish(EventType.CURSOR_STATE, {"enabled": self.state.cursor_control_enabled})
        return self.snapshot()

    async def set_browser_permission(self, enabled: bool) -> SessionState:
        self.state.browser_permission_enabled = enabled and not self.state.emergency_stopped and not self.state.paused
        await self._publish(EventType.BROWSER_STATE, {"permission_enabled": self.state.browser_permission_enabled})
        return self.snapshot()

    async def set_service_status(self, service: str, status: ServiceStatus, warning: str | None = None) -> None:
        setattr(self.state, f"{service}_status", status)
        if warning and warning not in self.state.active_warnings:
            self.state.active_warnings.append(warning)
        await self._publish(EventType.SESSION_STATE, {"service": service, "status": status, "warning": warning})

    async def set_tracking(self, status: TrackingStatus, confidence: float | None, x: float | None = None, y: float | None = None, target_stable: bool = False) -> None:
        self.state.tracking_status = status
        self.state.face_detected = status == TrackingStatus.TRACKING
        self.state.tracking_confidence = confidence
        self.state.target_x, self.state.target_y = x, y
        self.state.target_stable = target_stable if status == TrackingStatus.TRACKING else False
        event_type = EventType.VISION_TRACKING_LOST if status != TrackingStatus.TRACKING else EventType.VISION_STATE
        await self._publish(event_type, {"tracking_status": status, "confidence": confidence, "target_stable": self.state.target_stable, "target": {"x": x, "y": y} if x is not None and y is not None else None})

    async def set_calibrated(self, value: bool) -> None:
        self.state.calibrated = value
        self._invalidate_tracking_stability()
        await self._publish(EventType.VISION_CALIBRATION, {"status": "complete" if value else "invalid", "calibrated": value})

    def _invalidate_tracking_stability(self) -> None:
        self.state.target_stable = False
        self.tracking_reset_generation += 1

