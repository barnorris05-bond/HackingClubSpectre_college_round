from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SessionLifecycle(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    STOPPED = "stopped"


class ServiceStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INITIALIZING = "initializing"
    STOPPED = "stopped"
    ERROR = "error"


class TrackingStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    LOST = "lost"
    TRACKING = "tracking"


class SessionState(BaseModel):
    session_id: str
    lifecycle: SessionLifecycle = SessionLifecycle.IDLE
    paused: bool = False
    emergency_stopped: bool = False
    voice_status: ServiceStatus = ServiceStatus.UNAVAILABLE
    vision_status: ServiceStatus = ServiceStatus.UNAVAILABLE
    browser_status: ServiceStatus = ServiceStatus.UNAVAILABLE
    face_detected: bool = False
    tracking_status: TrackingStatus = TrackingStatus.UNAVAILABLE
    tracking_confidence: float | None = Field(default=None, ge=0, le=1)
    target_x: float | None = None
    target_y: float | None = None
    target_stable: bool = False
    calibrated: bool = False
    cursor_control_enabled: bool = False
    browser_permission_enabled: bool = False
    current_command_id: str | None = None
    last_command_result: dict[str, Any] | None = None
    active_warnings: list[str] = Field(default_factory=list)
    started_at: datetime
    updated_at: datetime


class SessionActionResponse(BaseModel):
    state: SessionState

