import asyncio

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # The managed test host cannot create the Proactor self-pipe; production
    # Uvicorn remains free to use its normal Windows policy.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.commands import CommandKind
from app.models.session import TrackingStatus
from app.providers.vision.base import FakeVisionProvider
from app.providers.vision.base import VisionSample
from app.services.safety_controller import SafetyController
from app.services.telemetry_service import TelemetryService
from app.services.vision_service import VisionService


def make_app():
    return create_app(Settings(), vision=FakeVisionProvider())


def test_health_and_partial_readiness():
    with TestClient(make_app()) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        ready = client.get("/api/v1/ready").json()
        assert ready["ready"] is True
        assert ready["services"]["voice"] == "unavailable"


def test_pause_resume_emergency_recovery_never_enables_cursor():
    with TestClient(make_app()) as client:
        assert client.post("/api/v1/cursor/permission", json={"enabled": True}).json()["state"]["cursor_control_enabled"] is True
        assert client.post("/api/v1/session/pause").json()["state"]["paused"] is True
        assert client.post("/api/v1/session/resume").json()["state"]["paused"] is False
        stopped = client.post("/api/v1/session/emergency-stop").json()["state"]
        assert stopped["emergency_stopped"] is True and stopped["cursor_control_enabled"] is False
        resumed = client.post("/api/v1/session/resume").json()["state"]
        assert resumed["emergency_stopped"] is True
        recovered = client.post("/api/v1/session/recover").json()["state"]
        assert recovered["emergency_stopped"] is False and recovered["cursor_control_enabled"] is False


def test_exact_command_fast_path_does_not_match_natural_language():
    with TestClient(make_app()) as client:
        result = client.post("/api/v1/voice/commands", json={"transcript": "click the sign in button"}).json()
        assert result["kind"] == "browser"
        assert result["status"] == "blocked"
        assert result["reason_code"] == "BROWSER_PERMISSION_DISABLED"
        result = client.post("/api/v1/voice/commands", json={"transcript": "click"}).json()
        assert result["kind"] == "direct_click"
        assert result["status"] == "blocked"
        assert result["reason_code"] == "CURSOR_CONTROL_DISABLED"


def test_structured_invalid_request():
    with TestClient(make_app()) as client:
        response = client.post("/api/v1/voice/commands", json={"transcript": ""})
        assert response.status_code == 422


def test_safety_invariants():
    safety = SafetyController()
    from app.models.session import SessionState
    from datetime import datetime, timezone
    base = SessionState(session_id="test", started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    assert safety.authorize(base, CommandKind.DIRECT_CLICK).reason_code == "CURSOR_CONTROL_DISABLED"
    base.cursor_control_enabled = True
    assert safety.authorize(base, CommandKind.DIRECT_CLICK).reason_code == "TRACKING_UNAVAILABLE"
    base.tracking_status = TrackingStatus.TRACKING
    base.tracking_confidence = 0.9
    base.calibrated = True
    assert safety.authorize(base, CommandKind.DIRECT_CLICK, target_stable=False).reason_code == "TARGET_UNSTABLE"
    base.paused = True
    assert safety.authorize(base, CommandKind.DIRECT_CLICK).reason_code == "SESSION_PAUSED"
    base.paused = False
    base.emergency_stopped = True
    assert safety.authorize(base, CommandKind.BROWSER).reason_code == "EMERGENCY_STOP"


def test_bounded_telemetry_queue_drops_oldest_and_cleans_up():
    async def run():
        telemetry = TelemetryService(queue_size=2)
        queue, events = await telemetry.subscribe()
        for index in range(4):
            await telemetry.publish("system.warning", "s", {"index": index})
        assert queue.qsize() == 2
        first = await anext(events)
        assert first.data["index"] == 2
        await telemetry.unsubscribe(queue)
        assert telemetry.client_count == 0
    asyncio.run(run())


def test_telemetry_initial_snapshot_and_disconnect():
    with TestClient(make_app()) as client:
        with client.websocket_connect("/api/v1/telemetry") as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "session.state"
            assert snapshot["sequence"] == 0


def test_websocket_receives_ordered_state_updates_and_disconnect_cleans_client():
    with TestClient(make_app()) as client:
        services = client.app.state.services
        with client.websocket_connect("/api/v1/telemetry") as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["sequence"] == 0
            client.post("/api/v1/session/pause")
            paused = websocket.receive_json()
            client.post("/api/v1/session/resume")
            resumed = websocket.receive_json()
            assert paused["type"] == "session.paused"
            assert resumed["type"] == "session.resumed"
            assert paused["sequence"] < resumed["sequence"]
        assert services.telemetry.client_count == 0


def test_websocket_broadcasts_to_multiple_clients_and_reconnect_gets_current_state():
    with TestClient(make_app()) as client:
        services = client.app.state.services
        with client.websocket_connect("/api/v1/telemetry") as first:
            first.receive_json()
            with client.websocket_connect("/api/v1/telemetry") as second:
                second.receive_json()
                assert services.telemetry.client_count == 2
                client.post("/api/v1/session/emergency-stop")
                first_event = first.receive_json()
                second_event = second.receive_json()
                assert first_event["type"] == second_event["type"] == "session.emergency_stop"
                assert first_event["sequence"] == second_event["sequence"]
            assert services.telemetry.client_count == 1
        assert services.telemetry.client_count == 0
        with client.websocket_connect("/api/v1/telemetry") as reconnect:
            snapshot = reconnect.receive_json()
            assert snapshot["type"] == "session.state"
            assert snapshot["data"]["state"]["emergency_stopped"] is True


def test_vision_stability_requires_bounded_consecutive_samples_and_resets_on_loss():
    async def run():
        telemetry = TelemetryService()
        from app.services.session_manager import SessionManager

        session = SessionManager(telemetry)
        provider = FakeVisionProvider()
        vision = VisionService(provider, session, telemetry, stability_radius_px=20, stability_window_ms=20)
        await session.start()
        await vision.initialize()
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.0, frame_width=1000, frame_height=1000)
        await vision.sample()
        assert session.state.target_stable is False
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.01, frame_width=1000, frame_height=1000)
        await vision.sample()
        assert session.state.target_stable is False
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.029, frame_width=1000, frame_height=1000)
        await vision.sample()
        assert session.state.target_stable is True
        provider.next_sample = VisionSample(False, None)
        await vision.sample()
        assert session.state.target_stable is False

    asyncio.run(run())


def test_vision_stop_and_safety_state_changes_invalidate_stability():
    async def run():
        telemetry = TelemetryService()
        from app.services.session_manager import SessionManager

        session = SessionManager(telemetry)
        provider = FakeVisionProvider()
        vision = VisionService(provider, session, telemetry, stability_radius_px=20, stability_window_ms=20)
        await session.start()
        await vision.initialize()
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.0, frame_width=1000, frame_height=1000)
        await vision.sample()
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.01, frame_width=1000, frame_height=1000)
        await vision.sample()
        provider.next_sample = VisionSample(True, 0.9, 0.5, 0.5, timestamp=1.029, frame_width=1000, frame_height=1000)
        await vision.sample()
        assert session.state.target_stable is True
        await session.pause()
        assert session.state.target_stable is False
        await session.resume()
        await vision.sample()
        assert session.state.target_stable is False
        await vision.stop()
        assert session.state.tracking_status == TrackingStatus.LOST
        assert session.state.target_stable is False

    asyncio.run(run())

