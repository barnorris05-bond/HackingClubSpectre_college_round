import math
from time import monotonic

from app.models.session import ServiceStatus, TrackingStatus
from app.models.vision import CalibrationStatus, VisionStatusResponse
from app.providers.vision.base import VisionProvider
from app.services.session_manager import SessionManager
from app.services.telemetry_service import TelemetryService


class VisionService:
    def __init__(
        self,
        provider: VisionProvider,
        session: SessionManager,
        telemetry: TelemetryService,
        stability_radius_px: float = 20.0,
        stability_window_ms: int = 200,
    ):
        self.provider = provider
        self.session = session
        self.telemetry = telemetry
        self.stability_radius_px = stability_radius_px
        self.stability_window_ms = stability_window_ms
        self.camera_initialized = False
        self.calibration_status = CalibrationStatus.NOT_CALIBRATED
        self.last_error: str | None = None
        self._samples: list[dict[str, float]] = []
        self._last_position: tuple[float, float, float] | None = None
        self._stable_since: float | None = None
        self._tracking_reset_generation = session.tracking_reset_generation

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
        self._reset_stability()
        await self.session.set_tracking(TrackingStatus.LOST, None)
        await self.session.set_service_status("vision", ServiceStatus.STOPPED)

    async def sample(self) -> None:
        if not self.camera_initialized:
            return
        if self.session.tracking_reset_generation != self._tracking_reset_generation:
            self._reset_stability()
            self._tracking_reset_generation = self.session.tracking_reset_generation
        if self.session.state.paused or self.session.state.emergency_stopped:
            self._reset_stability()
            await self.session.set_tracking(TrackingStatus.LOST, None)
            return
        started = monotonic()
        try:
            result = await self.provider.sample()
            sample_time = result.timestamp if result.timestamp is not None else monotonic()
            valid_timestamp = math.isfinite(sample_time)
            valid_confidence = result.confidence is not None and math.isfinite(result.confidence) and 0 <= result.confidence <= 1
            valid_coordinates = (
                result.x is not None
                and result.y is not None
                and math.isfinite(result.x)
                and math.isfinite(result.y)
                and 0 <= result.x <= 1
                and 0 <= result.y <= 1
            )
            if result.face_detected and valid_timestamp and valid_confidence and valid_coordinates:
                target_stable = self._update_stability(result.x, result.y, sample_time, result.frame_width, result.frame_height)
                await self.session.set_tracking(TrackingStatus.TRACKING, result.confidence, result.x, result.y, target_stable=target_stable)
            else:
                self._reset_stability()
                await self.session.set_tracking(TrackingStatus.LOST, result.confidence if valid_confidence else None)
            self.telemetry  # keep service dependency explicit for integrations
        except Exception as exc:
            self.last_error = str(exc)
            self._reset_stability()
            await self.session.set_tracking(TrackingStatus.LOST, None)
        _ = (monotonic() - started) * 1000

    async def calibrate(self, points: list[dict[str, float]]) -> tuple[bool, str]:
        self.calibration_status = CalibrationStatus.IN_PROGRESS
        self._reset_stability()
        await self.telemetry.publish("vision.calibration", self.session.state.session_id, {"status": "in_progress"})
        if not self.camera_initialized:
            self.calibration_status = CalibrationStatus.INVALID
            return False, "Camera is unavailable"
        if len(points) != 4:
            self.calibration_status = CalibrationStatus.INVALID
            return False, "Exactly four valid calibration points are required"
        if any(set(point) != {"x", "y"} for point in points):
            self.calibration_status = CalibrationStatus.INVALID
            await self.session.set_calibrated(False)
            return False, "Calibration points must contain x and y"
        if any(not math.isfinite(point[axis]) for point in points for axis in ("x", "y")):
            self.calibration_status = CalibrationStatus.INVALID
            await self.session.set_calibrated(False)
            return False, "Calibration points must be finite"
        self._samples = points
        self.calibration_status = CalibrationStatus.COMPLETE
        await self.session.set_calibrated(True)
        return True, "Calibration complete"

    async def reset_calibration(self) -> None:
        self._samples.clear()
        self.calibration_status = CalibrationStatus.NOT_CALIBRATED
        await self.session.set_calibrated(False)

    def _reset_stability(self) -> None:
        self._last_position = None
        self._stable_since = None
        self.session.state.target_stable = False

    def _update_stability(self, x: float, y: float, sample_time: float, frame_width: int | None, frame_height: int | None) -> bool:
        valid_frame_size = (
            isinstance(frame_width, (int, float))
            and not isinstance(frame_width, bool)
            and isinstance(frame_height, (int, float))
            and not isinstance(frame_height, bool)
            and math.isfinite(frame_width)
            and math.isfinite(frame_height)
            and frame_width > 0
            and frame_height > 0
        )
        if not valid_frame_size:
            self._reset_stability()
            return False
        if self._last_position is None:
            self._last_position = (x, y, sample_time)
            self._stable_since = sample_time
            return False
        previous_x, previous_y, previous_time = self._last_position
        elapsed = sample_time - previous_time
        if not math.isfinite(elapsed) or elapsed <= 0 or elapsed > self.stability_window_ms / 1000:
            self._stable_since = sample_time
        else:
            distance_px = math.hypot((x - previous_x) * frame_width, (y - previous_y) * frame_height)
            if distance_px > self.stability_radius_px:
                self._stable_since = sample_time
        self._last_position = (x, y, sample_time)
        return self._stable_since is not None and sample_time - self._stable_since >= self.stability_window_ms / 1000

    def status(self) -> VisionStatusResponse:
        state = self.session.state
        target = {"x": state.target_x, "y": state.target_y} if state.target_x is not None and state.target_y is not None else None
        return VisionStatusResponse(provider=self.provider.name, status=state.vision_status, camera_initialized=self.camera_initialized, face_detected=state.face_detected, tracking_status=state.tracking_status, confidence=state.tracking_confidence, calibrated=state.calibrated, calibration_status=self.calibration_status, target=target, last_error=self.last_error)

    async def shutdown(self) -> None:
        await self.stop()

