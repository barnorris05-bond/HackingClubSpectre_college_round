from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SESSION_STATE = "session.state"
    SESSION_PAUSED = "session.paused"
    SESSION_RESUMED = "session.resumed"
    SESSION_EMERGENCY_STOP = "session.emergency_stop"
    VOICE_STATE = "voice.state"
    VOICE_TRANSCRIPT = "voice.transcript"
    VOICE_ERROR = "voice.error"
    COMMAND_RECEIVED = "command.received"
    COMMAND_PROCESSING = "command.processing"
    COMMAND_COMPLETED = "command.completed"
    COMMAND_BLOCKED = "command.blocked"
    COMMAND_FAILED = "command.failed"
    VISION_STATE = "vision.state"
    VISION_FACE_DETECTED = "vision.face_detected"
    VISION_TRACKING_LOST = "vision.tracking_lost"
    VISION_CALIBRATION = "vision.calibration"
    CURSOR_STATE = "cursor.state"
    BROWSER_STATE = "browser.state"
    BROWSER_ACTION = "browser.action"
    EVALUATION_UPDATED = "evaluation.updated"
    SYSTEM_WARNING = "system.warning"
    SYSTEM_ERROR = "system.error"


class EventEnvelope(BaseModel):
    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str | None = None
    sequence: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    subsystem: str
    recoverable: bool
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody

