from enum import StrEnum

from pydantic import BaseModel, Field


class CalibrationStatus(StrEnum):
    NOT_CALIBRATED = "not_calibrated"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    INVALID = "invalid"


class VisionStatusResponse(BaseModel):
    provider: str
    status: str
    camera_initialized: bool
    face_detected: bool
    tracking_status: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    calibrated: bool
    calibration_status: CalibrationStatus
    target: dict[str, float] | None = None
    last_error: str | None = None

