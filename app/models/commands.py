from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CommandKind(StrEnum):
    DIRECT_CLICK = "direct_click"
    BROWSER = "browser"
    DICTATION = "dictation"
    UNKNOWN = "unknown"


class CommandStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class VoiceCommandRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)
    execute: bool = True


class BrowserRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)
    execute: bool = False


class CommandResult(BaseModel):
    command_id: str
    kind: CommandKind
    status: CommandStatus
    message: str
    reason_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PermissionRequest(BaseModel):
    enabled: bool


class CursorActionRequest(BaseModel):
    x: float = Field(ge=0, le=100000)
    y: float = Field(ge=0, le=100000)


class CalibrationRequest(BaseModel):
    points: list[dict[str, float]] = Field(default_factory=list, max_length=4)

