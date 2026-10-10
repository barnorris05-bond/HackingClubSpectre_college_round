from time import monotonic

from app.models.session import ServiceStatus, TrackingStatus
from app.models.vision import CalibrationStatus, VisionStatusResponse
from app.providers.vision.base import VisionProvider
from app.services.session_manager import SessionManager
from app.services.telemetry_service import TelemetryService


class VisionService:
    def __init__(self, provider: VisionProvider, session: SessionManager, telemetry: TelemetryService):
        self.provider = provider
        self.session = session
        self.telemetry = telemetry
        self.camera_initialized = False
        self.calibration_status = CalibrationStatus.NOT_CALIBRATED
        self.last_error: str | None = None
        self._samples: list[dict[str, float]] = []

    async def initialize(self) -> None:
        available, reason = await self.provider.availability()
        if not available:
            self.last_error = reason
            await self.session.set_service_status("vision", ServiceStatus.UNAVAILABLE, reason)
            return
        try:
            await self.provider.initialize()
            self.camera_initialized = True
            await self.session.set_service_status("vision", ServiceStatus.AVAILABLE)
        except Exception as exc:
            self.last_error = str(exc)
            await self.session.set_service_status("vision", ServiceStatus.ERROR, self.last_error)

    async def start(self) -> tuple[bool, str]:
        await self.initialize()
        if self.camera_initialized:
            return True, "Vision provider initialized"
        return False, self.last_error or "Vision provider unavailable"

    async def stop(self) -> None:
        await self.provider.shutdown()
        self.camera_initialized = False
        await self.session.set_service_status("vision", ServiceStatus.STOPPED)

    async def sample(self) -> None:
        if not self.camera_initialized:
            return
        started = monotonic()
        try:
            result = await self.provider.sample()
            if result.face_detected and result.confidence is not None and 0 <= result.confidence <= 1:
                await self.session.set_tracking(TrackingStatus.TRACKING, result.confidence, result.x, result.y)
            else:
                await self.session.set_tracking(TrackingStatus.LOST, result.confidence)
            self.telemetry  # keep service dependency explicit for integrations
        except Exception as exc:
            self.last_error = str(exc)
            await self.session.set_tracking(TrackingStatus.LOST, None)
        _ = (monotonic() - started) * 1000

    async def calibrate(self, points: list[dict[str, float]]) -> tuple[bool, str]:
        self.calibration_status = CalibrationStatus.IN_PROGRESS
        await self.telemetry.publish("vision.calibration", self.session.state.session_id, {"status": "in_progress"})
        if not self.camera_initialized:
            self.calibration_status = CalibrationStatus.INVALID
            return False, "Camera is unavailable"
        if len(points) != 4:
            self.calibration_status = CalibrationStatus.INVALID
            return False, "Exactly four valid calibration points are required"
        if any(set(point) != {"x", "y"} for point in points):
            self.calibration_status = CalibrationStatus.INVALID
            return False, "Calibration points must contain x and y"
        self._samples = points
        self.calibration_status = CalibrationStatus.COMPLETE
        await self.session.set_calibrated(True)
        return True, "Calibration complete"

    async def reset_calibration(self) -> None:
        self._samples.clear()
        self.calibration_status = CalibrationStatus.NOT_CALIBRATED
        await self.session.set_calibrated(False)

    def status(self) -> VisionStatusResponse:
        state = self.session.state
        target = {"x": state.target_x, "y": state.target_y} if state.target_x is not None and state.target_y is not None else None
        return VisionStatusResponse(provider=self.provider.name, status=state.vision_status, camera_initialized=self.camera_initialized, face_detected=state.face_detected, tracking_status=state.tracking_status, confidence=state.tracking_confidence, calibrated=state.calibrated, calibration_status=self.calibration_status, target=target, last_error=self.last_error)

    async def shutdown(self) -> None:
        await self.stop()

