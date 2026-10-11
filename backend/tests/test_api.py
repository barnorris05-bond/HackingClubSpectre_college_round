"""API-level behaviour, including the combined head-point + voice-activate flow.

The app runs with dry_run enabled globally, so these requests exercise every
line of the click path without moving the host pointer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        runtime = app.state.runtime
        # Start every test from a known, disarmed state.
        runtime.state.reset_evaluation()
        runtime.state.set_emergency_stop(False, source="test")
        runtime.state.set_cursor_control(False, source="test")
        runtime.state.set_paused(True, source="test")
        runtime.state.calibrated = False
        runtime.state.pointer_injection_enabled = False
        # Voice mode and settings are global; reset them so tests stay isolated.
        runtime.dictation.exit(source="test")
        runtime.dictation.clear(source="test")
        runtime.state.set_dictation("")
        runtime.apply_settings({"require_calibration_for_click": True, "dry_run": True})
        yield test_client
        # Leave nothing armed for the next test.
        runtime.state.set_cursor_control(False, source="test")
        runtime.state.set_paused(True, source="test")
        runtime.dictation.exit(source="test")


def arm_via_api(client: TestClient) -> None:
    """Arm the system using only public endpoints."""
    assert client.patch("/api/settings", json={"require_calibration_for_click": False}).status_code == 200
    assert client.post("/api/control/pointer-injection", json={"enabled": True}).status_code == 200
    assert client.post("/api/control/cursor", json={"enabled": True}).json()["cursor_control_enabled"] is True
    assert client.post("/api/control/pause", json={"paused": False}).json()["paused"] is False
    # Two samples inside the stability window make the pointer steady.
    client.post("/api/vision/pointer", json={"nx": 0.5, "ny": 0.5, "quality": 0.9})
    client.post("/api/vision/pointer", json={"nx": 0.5, "ny": 0.5, "quality": 0.9})


# --- basics ------------------------------------------------------------ #
def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_endpoint(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["project"] == "Handsfree"


def test_status_reports_central_state(client: TestClient) -> None:
    body = client.get("/api/status").json()
    for key in ("paused", "emergency_stop", "cursor_control_enabled", "camera", "tracking", "calibration", "cursor", "clicks", "voice", "vision"):
        assert key in body


def test_pause_round_trip(client: TestClient) -> None:
    assert client.post("/api/control/pause", json={"paused": False}).json()["paused"] is False
    assert client.post("/api/control/pause", json={"paused": True}).json()["paused"] is True


def test_emergency_stop_disarms_cursor(client: TestClient) -> None:
    client.post("/api/control/cursor", json={"enabled": True})
    body = client.post("/api/control/emergency-stop", json={"active": True}).json()
    assert body["emergency_stop"] is True
    assert body["cursor_control_enabled"] is False
    assert body["paused"] is True


def test_clearing_emergency_stop_does_not_resume_browser_actions(client: TestClient) -> None:
    client.post("/api/control/pause", json={"paused": False})
    client.post("/api/control/emergency-stop", json={"active": True})
    cleared = client.post("/api/control/emergency-stop", json={"active": False}).json()
    assert cleared["paused"] is True
    assert cleared["cursor_control_enabled"] is False
    assert cleared["tracking"]["reason"] == "paused"

    result = client.post(
        "/api/browser/action", json={"action": "goto", "url": "https://example.com"}
    ).json()
    assert result["ok"] is False
    assert result["reason"] == "paused"


def test_vision_start_reports_model_initialization_failure(client: TestClient, monkeypatch) -> None:
    runtime = app.state.runtime

    def fail_model_load():
        raise RuntimeError("model graph rejected asset")

    monkeypatch.setattr(runtime.tracker, "_landmarker_instance", fail_model_load)
    response = client.post("/api/vision/start")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason"] == "model_load_failed"
    assert "model graph rejected asset" in detail["detail"]
    assert runtime.tracker.running is False


def test_vision_start_api_starts_backend_worker_with_safety_disarmed(client: TestClient, monkeypatch) -> None:
    runtime = app.state.runtime
    # Keep the endpoint lifecycle test independent of physical camera access.
    monkeypatch.setattr(runtime.tracker, "_run", lambda: runtime.tracker._stop_event.wait(1))
    response = client.post("/api/vision/start")
    try:
        assert response.status_code == 200
        assert response.json()["running"] is True
        assert response.json()["camera"]["status"] in {"starting", "running"}
        assert "tracking" in response.json()
        assert runtime.tracker.running is True
        assert runtime.state.cursor_control_enabled is False
    finally:
        stopped = client.post("/api/vision/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert runtime.state.cursor_control_enabled is False


def test_cursor_cannot_be_armed_during_emergency_stop(client: TestClient) -> None:
    client.post("/api/control/emergency-stop", json={"active": True})
    body = client.post("/api/control/cursor", json={"enabled": True}).json()
    assert body["cursor_control_enabled"] is False
    client.post("/api/control/emergency-stop", json={"active": False})


# --- classification ---------------------------------------------------- #
def test_classify_does_not_actuate(client: TestClient) -> None:
    body = client.post("/api/voice/classify", json={"text": "click"}).json()
    assert body["kind"] == "direct_click"
    assert client.get("/api/status").json()["clicks"]["executed"] == 0


def test_sentence_with_click_is_not_a_direct_click(client: TestClient) -> None:
    body = client.post("/api/voice/classify", json={"text": "click on the sign in button"}).json()
    assert body["kind"] == "browser_click"
    assert body["is_direct_click_phrase"] is False


# --- safety-gated click ------------------------------------------------ #
def test_voice_click_blocked_while_disarmed(client: TestClient) -> None:
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["kind"] == "direct_click"
    assert result["executed"] is False
    assert result["decision"]["reason"] == "cursor_control_disabled"


def test_voice_click_executes_when_armed(client: TestClient) -> None:
    arm_via_api(client)
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["executed"] is True
    assert result["decision"]["allowed"] is True
    assert client.get("/api/status").json()["clicks"]["executed"] == 1


def test_second_click_is_rate_limited(client: TestClient) -> None:
    arm_via_api(client)
    client.post("/api/voice/command", json={"text": "click"})
    second = client.post("/api/voice/command", json={"text": "click"}).json()
    assert second["executed"] is False
    assert second["decision"]["reason"] == "cooldown"


def test_click_blocked_after_tracking_loss(client: TestClient) -> None:
    arm_via_api(client)
    client.post("/api/vision/pointer", json={"nx": 0.5, "ny": 0.5, "tracking": False})
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["executed"] is False
    assert result["decision"]["reason"] == "tracking_lost"


def test_paused_blocks_voice_click(client: TestClient) -> None:
    arm_via_api(client)
    client.post("/api/control/pause", json={"paused": True})
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["executed"] is False
    assert result["decision"]["reason"] == "paused"


def test_emergency_stop_blocks_voice_click(client: TestClient) -> None:
    arm_via_api(client)
    client.post("/api/control/emergency-stop", json={"active": True})
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["executed"] is False
    assert result["decision"]["reason"] == "emergency_stop"


def test_voice_stop_phrase_engages_emergency_stop(client: TestClient) -> None:
    arm_via_api(client)
    result = client.post("/api/voice/command", json={"text": "stop"}).json()
    assert result["executed"] is True
    assert client.get("/api/status").json()["emergency_stop"] is True


# --- dictation separation --------------------------------------------- #
def test_dictation_captures_speech_instead_of_commands(client: TestClient) -> None:
    client.post("/api/voice/mode", json={"mode": "dictation"})
    result = client.post("/api/voice/command", json={"text": "click"}).json()
    assert result["route"] == "dictation"
    assert result["handled"] is True
    status = client.get("/api/status").json()
    assert status["voice"]["mode"] == "dictation"
    assert status["clicks"]["executed"] == 0
    assert "click" in status["voice"]["dictation_text"]


def test_dictation_exit_phrase_by_voice(client: TestClient) -> None:
    client.post("/api/voice/mode", json={"mode": "dictation"})
    result = client.post("/api/voice/command", json={"text": "stop dictation"}).json()
    assert result["action"] == "exit"
    assert client.get("/api/voice/mode").json()["mode"] == "idle"


def test_dictation_text_accumulates_and_clears(client: TestClient) -> None:
    client.post("/api/voice/mode", json={"mode": "dictation"})
    client.post("/api/voice/dictation", json={"text": "hello world"})
    body = client.post("/api/voice/dictation", json={"text": "second line"}).json()
    assert body["text"].startswith("hello world")
    assert body["length"] > len("hello world")
    client.delete("/api/voice/dictation")
    assert client.get("/api/voice/dictation").json()["text"] == ""


# --- browser intent routing -------------------------------------------- #
def test_browser_intent_does_not_touch_the_os_pointer(client: TestClient, monkeypatch) -> None:
    runtime = app.state.runtime
    calls: list[str] = []

    async def fake_click_target(target: str, role: str | None = None):
        calls.append(target)
        return {"ok": True, "action": "click_target", "target": target}

    monkeypatch.setattr(runtime.browser, "click_target", fake_click_target)
    arm_via_api(client)
    result = client.post("/api/voice/command", json={"text": "click on the sign in button"}).json()
    assert result["kind"] == "browser_click"
    assert calls == ["sign in"]
    assert client.get("/api/status").json()["clicks"]["executed"] == 0


def test_direct_browser_action_works_when_unpaused(client: TestClient, monkeypatch) -> None:
    runtime = app.state.runtime
    client.post("/api/control/pause", json={"paused": False})
    calls: list[str] = []

    async def fake_goto(url: str):
        calls.append(url)
        return {"ok": True, "action": "goto", "url": url}

    monkeypatch.setattr(runtime.browser, "goto", fake_goto)
    response = client.post(
        "/api/browser/action", json={"action": "goto", "url": "https://example.com"}
    )
    assert response.json()["ok"] is True
    assert calls == ["https://example.com"]


def test_browser_actions_blocked_when_paused(client: TestClient) -> None:
    client.post("/api/control/pause", json={"paused": True})

    # Direct browser API
    for action, payload in [
        ("goto", {"url": "https://example.com"}),
        ("click_target", {"target": "Sign in"}),
        ("type", {"text": "hello"}),
        ("scroll", {"direction": "down"}),
        ("new_tab", {}),
    ]:
        res = client.post("/api/browser/action", json={"action": action, **payload}).json()
        assert res["ok"] is False
        assert res["reason"] == "paused"

    # Voice browser command while paused
    voice_res = client.post("/api/voice/command", json={"text": "open example.com"}).json()
    assert voice_res["executed"] is False
    assert voice_res["action_result"]["reason"] == "paused"
    assert any(
        event["message"] == "browser_action_rejected" and event.get("reason") == "paused"
        for event in client.get("/api/telemetry/events?kind=browser").json()["events"]
    )


def test_browser_actions_blocked_during_emergency_stop(client: TestClient) -> None:
    client.post("/api/control/pause", json={"paused": False})
    client.post("/api/control/emergency-stop", json={"active": True})

    for action, payload in [
        ("goto", {"url": "https://example.com"}),
        ("click_target", {"target": "Sign in"}),
        ("type", {"text": "hello"}),
        ("scroll", {"direction": "down"}),
        ("new_tab", {}),
    ]:
        res = client.post("/api/browser/action", json={"action": action, **payload}).json()
        assert res["ok"] is False
        assert res["reason"] == "emergency_stop"

    voice_res = client.post("/api/voice/command", json={"text": "open example.com"}).json()
    assert voice_res["executed"] is False
    assert voice_res["action_result"]["reason"] == "emergency_stop"


def test_unresolved_navigation_target_is_reported(client: TestClient) -> None:
    client.post("/api/control/pause", json={"paused": False})
    client.post("/api/control/emergency-stop", json={"active": False})
    result = client.post("/api/voice/command", json={"text": "open the settings menu"}).json()
    assert result["executed"] is False
    assert result["action_result"]["reason"] == "unresolved_target"


# --- settings ---------------------------------------------------------- #
def test_settings_lists_mutable_values(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert "dead_zone" in body["mutable"]
    assert "dead_zone" in body["values"]


def test_settings_patch_applies_and_clamps(client: TestClient) -> None:
    body = client.patch("/api/settings", json={"dead_zone": 0.5}).json()
    assert body["applied"]["dead_zone"] == 0.5
    # Clamped to the safe ceiling.
    assert body["values"]["dead_zone"] <= 0.15
    client.patch("/api/settings", json={"dead_zone": 0.015})


def test_settings_rejects_unknown_keys(client: TestClient) -> None:
    body = client.patch("/api/settings", json={"not_a_setting": 1}).json()
    assert "not_a_setting" in body["rejected"]
    assert body["applied"] == {}


def test_settings_persists_across_reads(client: TestClient) -> None:
    client.patch("/api/settings", json={"cursor_gain": 1.75})
    assert client.get("/api/settings").json()["values"]["cursor_gain"] == 1.75
    client.patch("/api/settings", json={"cursor_gain": 1.0})


# --- evaluation -------------------------------------------------------- #
def test_evaluation_reports_no_fabricated_numbers(client: TestClient) -> None:
    client.post("/api/evaluation/reset")
    summary = client.get("/api/evaluation/summary").json()
    assert summary["clicks"]["attempts"] == 0
    assert summary["clicks"]["selection_error_rate"] is None
    assert summary["latency"]["click_dispatch_ms"]["measured"] is False
    assert summary["task_timing"]["voice"]["measured"] is False
    assert summary["task_timing"]["voice_is_faster"] is None


def test_task_timing_measures_real_duration(client: TestClient) -> None:
    client.post("/api/evaluation/reset")
    task = client.post(
        "/api/evaluation/task/start", json={"name": "demo", "input_mode": "voice"}
    ).json()
    finished = client.post(
        "/api/evaluation/task/finish", json={"id": task["id"], "completed": True}
    ).json()
    assert finished["duration_s"] >= 0
    summary = client.get("/api/evaluation/summary").json()
    assert summary["task_timing"]["voice"]["count"] == 1


def test_finishing_unknown_task_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/evaluation/task/finish", json={"id": "does-not-exist", "completed": True}
    )
    assert response.status_code == 404


def test_rejected_clicks_appear_in_evaluation(client: TestClient) -> None:
    client.post("/api/evaluation/reset")
    client.post("/api/voice/command", json={"text": "click"})
    summary = client.get("/api/evaluation/summary").json()
    assert summary["clicks"]["attempts"] == 1
    assert summary["clicks"]["rejected"] == 1
    assert summary["clicks"]["selection_error_rate"] == 1.0
    assert "cursor_control_disabled" in summary["clicks"]["reject_reasons"]


# --- telemetry --------------------------------------------------------- #
def test_telemetry_events_and_clear(client: TestClient) -> None:
    client.post("/api/voice/command", json={"text": "scroll down"})
    events = client.get("/api/telemetry/events?limit=20").json()["events"]
    assert any(event["kind"] == "voice" for event in events)
    client.delete("/api/telemetry/events")
    assert client.get("/api/telemetry/events").json()["count"] <= 1


def test_vision_status_endpoint(client: TestClient) -> None:
    body = client.get("/api/vision/status").json()
    assert "available" in body
    assert "calibration" in body


def test_vision_stream_reports_no_frame_when_stopped(client: TestClient) -> None:
    response = client.get("/api/vision/frame")
    assert response.status_code == 503
