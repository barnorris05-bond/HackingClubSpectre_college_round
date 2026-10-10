from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.models.commands import BrowserRequest, CalibrationRequest, CommandKind, CommandResult, CommandStatus, CursorActionRequest, PermissionRequest, VoiceCommandRequest
from app.models.events import ErrorBody, ErrorResponse, EventType
from app.models.session import ServiceStatus, SessionActionResponse, SessionLifecycle
from app.providers.browser import FakeBrowserAdapter
from app.providers.cursor import FakeCursorAdapter
from app.providers.speech.base import SpeechProvider
from app.providers.vision.base import VisionProvider
from app.services.command_router import CommandRouter
from app.services.evaluation_service import EvaluationService
from app.services.safety_controller import SafetyController
from app.services.session_manager import SessionManager
from app.services.telemetry_service import TelemetryService
from app.services.vision_service import VisionService


class Services:
    def __init__(self, settings: Settings, speech: SpeechProvider | None = None, vision: VisionProvider | None = None):
        self.telemetry = TelemetryService(settings.telemetry_queue_size)
        self.session = SessionManager(self.telemetry)
        self.safety = SafetyController(settings.tracking_min_confidence, settings.stability_radius_px)
        self.evaluation = EvaluationService()
        self.router = CommandRouter()
        self.speech = speech or SpeechProvider()
        if vision is None and settings.camera_enabled:
            try:
                from app.providers.vision.opencv import OpenCVCameraProvider
                vision = OpenCVCameraProvider(settings.camera_index)
            except ImportError:
                vision = VisionProvider()
        self.vision = VisionService(
            vision or VisionProvider(),
            self.session,
            self.telemetry,
            stability_radius_px=settings.stability_radius_px,
            stability_window_ms=settings.stability_window_ms,
        )
        self.cursor = FakeCursorAdapter()
        self.browser = FakeBrowserAdapter() if settings.browser_provider == "dry-run" else None
        self.browser_ready = False

    async def startup(self) -> None:
        await self.session.start()
        available, reason = await self.speech.availability()
        await self.session.set_service_status("voice", ServiceStatus.AVAILABLE if available else ServiceStatus.UNAVAILABLE, None if available else reason)
        # Real camera providers are never opened implicitly at server startup.
        # The injected fake is initialized for deterministic API tests only.
        if self.vision.provider.name == "fake":
            await self.vision.initialize()
        else:
            await self.session.set_service_status("vision", ServiceStatus.UNAVAILABLE, "Camera activation is explicit; call /api/v1/vision/start")
        if self.browser:
            try:
                await self.browser.initialize()
                self.browser_ready = True
                await self.session.set_service_status("browser", ServiceStatus.AVAILABLE)
            except Exception as exc:
                await self.session.set_service_status("browser", ServiceStatus.ERROR, str(exc))
        else:
            await self.session.set_service_status("browser", ServiceStatus.UNAVAILABLE, "No browser provider configured")

    async def shutdown(self) -> None:
        await self.speech.shutdown()
        await self.vision.shutdown()
        if self.browser:
            await self.browser.shutdown()
        self.session.state.lifecycle = SessionLifecycle.STOPPED


def error_response(code: str, message: str, subsystem: str, recoverable: bool = True, details: dict[str, Any] | None = None, request_id: str | None = None, response_status: int | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, subsystem=subsystem, recoverable=recoverable, details=details, request_id=request_id))
    code_status = status.HTTP_409_CONFLICT if code.startswith("SAFETY_") else status.HTTP_503_SERVICE_UNAVAILABLE if code.endswith("_UNAVAILABLE") else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=response_status or code_status, content=body.model_dump(mode="json"))


def create_app(settings: Settings | None = None, *, speech: SpeechProvider | None = None, vision: VisionProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    services = Services(settings, speech=speech, vision=vision)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await services.startup()
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(title="Handsfree Backend Lab", version="1.0.0", lifespan=lifespan)
    app.state.services = services
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return error_response("INVALID_REQUEST", str(exc.detail), "api")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return error_response("INVALID_REQUEST", "Request validation failed", "api", details={"validation": exc.errors()}, response_status=status.HTTP_422_UNPROCESSABLE_CONTENT)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": "handsfree-backend", "version": app.version}

    @app.get("/api/v1/ready")
    async def readiness():
        state = services.session.snapshot()
        return {"ready": state.lifecycle == SessionLifecycle.ACTIVE and not state.emergency_stopped, "safe": not state.emergency_stopped, "partial": True, "services": {"voice": state.voice_status, "vision": state.vision_status, "browser": state.browser_status}}

    @app.get("/api/v1/session")
    async def get_session():
        return services.session.snapshot()

    @app.post("/api/v1/session/pause", response_model=SessionActionResponse)
    async def pause():
        return {"state": await services.session.pause()}

    @app.post("/api/v1/session/resume", response_model=SessionActionResponse)
    async def resume():
        return {"state": await services.session.resume()}

    @app.post("/api/v1/session/emergency-stop", response_model=SessionActionResponse)
    async def emergency_stop():
        return {"state": await services.session.emergency_stop()}

    @app.post("/api/v1/session/recover", response_model=SessionActionResponse)
    async def recover():
        return {"state": await services.session.recover()}

    @app.get("/api/v1/voice/status")
    async def voice_status():
        available, reason = await services.speech.availability()
        return {"provider": services.speech.name, "status": services.session.state.voice_status, "available": available, "message": reason, "active": bool(getattr(services.speech, "running", False))}

    @app.post("/api/v1/voice/commands", response_model=CommandResult)
    async def voice_command(payload: VoiceCommandRequest):
        normalized = services.router.classify(payload.transcript)
        command_id = normalized.command_id
        services.session.state.current_command_id = command_id
        await services.telemetry.publish(EventType.COMMAND_RECEIVED, services.session.state.session_id, {"command_id": command_id, "kind": normalized.kind, "transcript": normalized.normalized})
        await services.telemetry.publish(EventType.COMMAND_PROCESSING, services.session.state.session_id, {"command_id": command_id, "intent": normalized.intent})
        if normalized.kind.value == "unknown":
            result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.FAILED, message="No supported intent matched", reason_code="UNKNOWN_INTENT")
        elif normalized.kind.value == "dictation":
            result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.COMPLETED, message="Dictation transcript accepted", details={"text": normalized.normalized.removeprefix("dictate ").removeprefix("type ")})
        elif normalized.kind == CommandKind.BROWSER and not payload.execute:
            if not services.browser_ready or services.browser is None:
                result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.FAILED, message="Browser provider is unavailable", reason_code="BROWSER_UNAVAILABLE")
            else:
                plan = await services.browser.plan(normalized.normalized)
                result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.PROCESSING, message="Browser plan created; awaiting explicit confirmation", details={"phase": "plan_created", "plan": plan})
                await services.telemetry.publish(EventType.BROWSER_ACTION, services.session.state.session_id, {"command_id": command_id, "phase": "plan_created", "plan": plan})
        else:
            snapshot = services.session.snapshot()
            decision = services.safety.authorize(snapshot, normalized.kind, target_stable=snapshot.target_stable if normalized.kind.value == "direct_click" else True)
            if not payload.execute:
                decision = type(decision)(False, "EXECUTION_NOT_REQUESTED", "Execution was not requested")
            if not decision.allowed:
                services.evaluation.increment("blocked_action_count")
                result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.BLOCKED, message=decision.message, reason_code=decision.reason_code)
                await services.telemetry.publish(EventType.COMMAND_BLOCKED, services.session.state.session_id, result.model_dump(mode="json"))
                services.session.state.last_command_result = result.model_dump(mode="json")
                services.session.state.current_command_id = None
                return result
            if normalized.kind.value == "browser":
                if not services.browser_ready or services.browser is None:
                    result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.FAILED, message="Browser provider is unavailable", reason_code="BROWSER_UNAVAILABLE")
                else:
                    plan = await services.browser.plan(normalized.normalized)
                    output = await services.browser.execute(plan)
                    services.evaluation.increment("successful_action_count")
                    result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.COMPLETED, message="Browser action completed in dry-run adapter", details=output)
            else:
                await services.cursor.click()
                services.evaluation.increment("successful_action_count")
                result = CommandResult(command_id=command_id, kind=normalized.kind, status=CommandStatus.COMPLETED, message="Direct click completed", details={"dry_run": True})
        final_event = EventType.COMMAND_COMPLETED if result.status == CommandStatus.COMPLETED else EventType.COMMAND_FAILED if result.status == CommandStatus.FAILED else EventType.COMMAND_PROCESSING
        await services.telemetry.publish(final_event, services.session.state.session_id, result.model_dump(mode="json"))
        services.session.state.last_command_result = result.model_dump(mode="json")
        services.session.state.current_command_id = None
        return result

    @app.get("/api/v1/vision/status")
    async def vision_status():
        return services.vision.status()

    @app.post("/api/v1/vision/start")
    async def vision_start():
        ok, message = await services.vision.start()
        return {"started": ok, "message": message, "status": services.vision.status()}

    @app.post("/api/v1/vision/stop")
    async def vision_stop():
        await services.vision.stop()
        return services.vision.status()

    @app.post("/api/v1/vision/calibration")
    async def calibrate(payload: CalibrationRequest):
        ok, message = await services.vision.calibrate(payload.points)
        return {"status": services.vision.calibration_status, "calibrated": ok, "message": message}

    @app.post("/api/v1/vision/calibration/reset")
    async def reset_calibration():
        await services.vision.reset_calibration()
        return services.vision.status()

    @app.post("/api/v1/cursor/permission", response_model=SessionActionResponse)
    async def cursor_permission(payload: PermissionRequest):
        return {"state": await services.session.set_cursor_permission(payload.enabled)}

    @app.post("/api/v1/cursor/move")
    async def cursor_move(payload: CursorActionRequest):
        snapshot = services.session.snapshot()
        decision = services.safety.authorize(snapshot, CommandKind.DIRECT_CLICK, target_stable=snapshot.target_stable)
        if not decision.allowed:
            services.evaluation.increment("blocked_action_count")
            return error_response("SAFETY_BLOCKED", decision.message, "safety", details={"reason_code": decision.reason_code})
        await services.cursor.move(payload.x, payload.y)
        services.evaluation.increment("successful_action_count")
        return {"status": "completed", "dry_run": True, "x": payload.x, "y": payload.y}

    @app.get("/api/v1/browser/status")
    async def browser_status():
        return {"provider": services.browser.name if services.browser else "none", "status": services.session.state.browser_status, "available": services.browser_ready, "permission_enabled": services.session.state.browser_permission_enabled}

    @app.post("/api/v1/browser/permission", response_model=SessionActionResponse)
    async def browser_permission(payload: PermissionRequest):
        return {"state": await services.session.set_browser_permission(payload.enabled)}

    @app.post("/api/v1/browser/requests", response_model=CommandResult)
    async def browser_request(payload: BrowserRequest):
        return await voice_command(VoiceCommandRequest(transcript=payload.request, execute=payload.execute))

    @app.get("/api/v1/evaluation/summary")
    async def evaluation_summary():
        return {"metrics": services.evaluation.summary()}

    @app.websocket("/api/v1/telemetry")
    async def telemetry(websocket: WebSocket):
        await websocket.accept()
        queue, events = await services.telemetry.subscribe()
        try:
            snapshot = services.session.snapshot()
            await websocket.send_json({"type": EventType.SESSION_STATE, "timestamp": snapshot.updated_at.isoformat(), "session_id": snapshot.session_id, "sequence": 0, "data": {"state": snapshot.model_dump(mode="json")}})
            async for event in events:
                await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            await services.telemetry.unsubscribe(queue)

    return app


app = create_app()

