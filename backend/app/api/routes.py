"""HTTP and WebSocket surface for Handsfree.

The two things that matter most here: cursor control is opt-in, and the camera
stream is served from the same capture that drives tracking (so the preview
proves what the tracker actually sees).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.control.runtime import Runtime
from app.voice.commands import DIRECT_CLICK_PHRASES, classify

router = APIRouter()


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


# --------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------- #
class PauseRequest(BaseModel):
    paused: bool


class CursorRequest(BaseModel):
    enabled: bool


class EmergencyStopRequest(BaseModel):
    active: bool = True


class PointerInjectionRequest(BaseModel):
    enabled: bool


class PointerRequest(BaseModel):
    nx: float = Field(ge=0.0, le=1.0)
    ny: float = Field(ge=0.0, le=1.0)
    quality: float | None = Field(default=None, ge=0.0, le=1.0)
    tracking: bool = True


class VoiceCommandRequest(BaseModel):
    text: str
    client_ts_ms: float | None = None


class VoiceModeRequest(BaseModel):
    mode: Literal["idle", "dictation"]


class DictationRequest(BaseModel):
    text: str


class ClassifyRequest(BaseModel):
    text: str


class BrowserActionRequest(BaseModel):
    action: Literal[
        "goto",
        "search",
        "click_target",
        "type",
        "scroll",
        "back",
        "forward",
        "reload",
        "new_tab",
        "next_tab",
        "previous_tab",
        "switch_tab",
        "close_tab",
        "start",
        "stop",
        "snapshot",
    ]
    url: str | None = None
    query: str | None = None
    target: str | None = None
    role: str | None = None
    text: str | None = None
    selector: str | None = None
    direction: str | None = None
    pixels: int | None = None
    index: int | None = None


class TaskStartRequest(BaseModel):
    name: str = "untitled task"
    input_mode: Literal["voice", "manual"] = "voice"
    notes: str | None = None


class TaskFinishRequest(BaseModel):
    id: str
    completed: bool = True


# --------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------- #
@router.get("/", tags=["system"])
def root() -> dict[str, Any]:
    return {
        "project": "Handsfree",
        "message": "Voice and webcam browsing API",
        "docs": "/docs",
    }


@router.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    from datetime import datetime, timezone

    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/api/status", tags=["system"])
def get_status(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    snapshot = runtime.state.snapshot()
    snapshot["vision"] = runtime.tracker.status()
    snapshot["browser_status"] = runtime.browser.status()
    snapshot["dictation"] = runtime.dictation.status()
    return snapshot


# --------------------------------------------------------------------- #
# control
# --------------------------------------------------------------------- #
@router.post("/api/control/pause", tags=["control"])
def set_pause(
    payload: PauseRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.state.set_paused(payload.paused, source="api")


@router.post("/api/control/cursor", tags=["control"])
def set_cursor(
    payload: CursorRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.state.set_cursor_control(payload.enabled, source="api")


@router.post("/api/control/emergency-stop", tags=["control"])
def set_emergency_stop(
    payload: EmergencyStopRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.state.set_emergency_stop(payload.active, source="api")


@router.post("/api/control/pointer-injection", tags=["control", "test-support"])
def set_pointer_injection(
    payload: PointerInjectionRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    """Enable the synthetic pointer path so safety can be tested without hardware."""
    runtime.state.pointer_injection_enabled = payload.enabled
    runtime.telemetry.emit(
        "control",
        "pointer_injection_" + ("enabled" if payload.enabled else "disabled"),
        data={"source": "api"},
    )
    return runtime.state.snapshot()


@router.post("/api/vision/pointer", tags=["control", "test-support"])
def inject_pointer(
    payload: PointerRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    result = runtime.inject_pointer(
        payload.nx, payload.ny, quality=payload.quality, tracking=payload.tracking
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


# --------------------------------------------------------------------- #
# vision
# --------------------------------------------------------------------- #
@router.get("/api/vision/status", tags=["vision"])
def vision_status(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    state = runtime.state.snapshot()
    return {
        **runtime.tracker.status(),
        "camera": state["camera"],
        "tracking": state["tracking"],
        "calibration": runtime.tracker.calibration_status(),
    }


@router.post("/api/vision/start", tags=["vision"])
def vision_start(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    if not runtime.tracker.available:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "vision_unavailable",
                "detail": runtime.tracker.unavailable_reason,
                "hint": (
                    "Install OpenCV and MediaPipe and download the face landmark "
                    "model. The rest of the app runs without them."
                ),
            },
        )
    status = runtime.tracker.start()
    if status.get("error_code") == "model_load_failed":
        raise HTTPException(
            status_code=503,
            detail={"reason": status["error_code"], "detail": status.get("error")},
        )
    return vision_status(runtime)


@router.post("/api/vision/stop", tags=["vision"])
def vision_stop(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    runtime.tracker.stop()
    return vision_status(runtime)


@router.post("/api/vision/calibrate", tags=["vision"])
def vision_calibrate(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    if not runtime.tracker.available:
        raise HTTPException(
            status_code=503,
            detail={"reason": "vision_unavailable", "detail": runtime.tracker.unavailable_reason},
        )
    if not runtime.tracker.running:
        runtime.tracker.start()
    return runtime.tracker.request_calibration()


@router.delete("/api/vision/calibrate", tags=["vision"])
def vision_cancel_calibration(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.tracker.cancel_calibration()


@router.get("/api/vision/calibration", tags=["vision"])
def vision_calibration(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.tracker.calibration_status()


@router.get("/api/vision/frame", tags=["vision"])
def vision_frame(runtime: Runtime = Depends(get_runtime)) -> Response:
    frame = runtime.tracker.latest_jpeg()
    if frame is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "no_frame",
                "detail": "camera is not running or has not produced a frame yet",
            },
        )
    return Response(content=frame, media_type="image/jpeg")


@router.get("/api/vision/stream", tags=["vision"])
def vision_stream(runtime: Runtime = Depends(get_runtime)) -> StreamingResponse:
    if not runtime.tracker.available:
        raise HTTPException(
            status_code=503,
            detail={"reason": "vision_unavailable", "detail": runtime.tracker.unavailable_reason},
        )
    if not runtime.tracker.running:
        runtime.tracker.start()

    async def generate():
        # The subscriber count keeps the tracker from JPEG-encoding frames
        # nobody is watching. On client disconnect the generator is cancelled
        # and the finally block releases the subscription.
        runtime.tracker.subscribe_stream()
        try:
            while True:
                frame = runtime.tracker.latest_jpeg()
                if frame:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                await asyncio.sleep(1.0 / max(runtime.settings.stream_fps, 1))
        finally:
            runtime.tracker.unsubscribe_stream()

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------- #
# voice
# --------------------------------------------------------------------- #
@router.post("/api/voice/command", tags=["voice"])
async def voice_command(
    payload: VoiceCommandRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return await runtime.handle_voice(
        payload.text,
        received_at=time.monotonic(),
        client_ts_ms=payload.client_ts_ms,
    )


@router.post("/api/voice/classify", tags=["voice"])
def voice_classify(
    payload: ClassifyRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    """Classification only - never actuates anything. Used by the UI and tests."""
    command = classify(payload.text, runtime.settings.browser_search_engine)
    return {
        **command.to_dict(),
        "is_direct_click_phrase": command.normalized in DIRECT_CLICK_PHRASES,
        "decision_preview": (
            runtime.dispatcher.evaluate().to_dict()
            if command.requires_pointer_safety
            else None
        ),
    }


@router.get("/api/voice/mode", tags=["voice"])
def voice_mode(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.dictation.status()


@router.post("/api/voice/mode", tags=["voice"])
def set_voice_mode(
    payload: VoiceModeRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    if payload.mode == "dictation":
        return runtime.dictation.enter(source="api")
    return runtime.dictation.exit(source="api")


@router.get("/api/voice/dictation", tags=["voice"])
def get_dictation(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.dictation.status()


@router.post("/api/voice/dictation", tags=["voice"])
def post_dictation(
    payload: DictationRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.dictation.append(payload.text, source="api")


@router.delete("/api/voice/dictation", tags=["voice"])
def clear_dictation(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.dictation.clear(source="api")


# --------------------------------------------------------------------- #
# browser agent
# --------------------------------------------------------------------- #
@router.get("/api/browser/status", tags=["browser"])
def browser_status(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.browser.status()


@router.post("/api/browser/action", tags=["browser"])
async def browser_action(
    payload: BrowserActionRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return await runtime.browser.dispatch_action(
        payload.action,
        url=payload.url or "",
        query=payload.query or "",
        target=payload.target or "",
        role=payload.role,
        text=payload.text or "",
        selector=payload.selector,
        direction=payload.direction or "down",
        pixels=payload.pixels,
        index=payload.index,
    )


@router.get("/api/browser/screenshot", tags=["browser"])
async def browser_screenshot(runtime: Runtime = Depends(get_runtime)) -> Response:
    data = await runtime.browser.screenshot()
    if data is None:
        raise HTTPException(
            status_code=503,
            detail={"reason": "no_browser", "detail": "browser agent is not running"},
        )
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------- #
# telemetry and evaluation
# --------------------------------------------------------------------- #
@router.get("/api/telemetry/events", tags=["telemetry"])
def telemetry_events(
    limit: int = 100, kind: str | None = None, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    events = runtime.telemetry.recent(limit=min(max(limit, 1), 800), kind=kind)
    return {"count": len(events), "events": events}


@router.delete("/api/telemetry/events", tags=["telemetry"])
def telemetry_clear(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    runtime.telemetry.reset()
    runtime.telemetry.emit("telemetry", "telemetry_cleared")
    return {"ok": True}


@router.get("/api/evaluation/summary", tags=["evaluation"])
def evaluation_summary(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.evaluation_summary()


@router.post("/api/evaluation/task/start", tags=["evaluation"])
def task_start(
    payload: TaskStartRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.start_task(payload.name, payload.input_mode, payload.notes)


@router.post("/api/evaluation/task/finish", tags=["evaluation"])
def task_finish(
    payload: TaskFinishRequest, runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    result = runtime.finish_task(payload.id, payload.completed)
    if not result.get("ok", True):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/api/evaluation/reset", tags=["evaluation"])
def evaluation_reset(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.reset_evaluation()


# --------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------- #
@router.get("/api/settings", tags=["settings"])
def get_settings_view(runtime: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    return runtime.settings_view()


@router.patch("/api/settings", tags=["settings"])
def patch_settings(
    patch: dict[str, Any] = Body(...), runtime: Runtime = Depends(get_runtime)
) -> dict[str, Any]:
    return runtime.apply_settings(patch)


# --------------------------------------------------------------------- #
# websocket telemetry
# --------------------------------------------------------------------- #
@router.websocket("/api/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    runtime: Runtime = websocket.app.state.runtime
    await websocket.accept()
    queue = runtime.telemetry.subscribe()
    try:
        recent = runtime.telemetry.recent(limit=25)
        await websocket.send_json({"type": "history", "events": recent})
        while True:
            event = await queue.get()
            await websocket.send_json({"type": "event", "event": event})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        runtime.telemetry.unsubscribe(queue)
