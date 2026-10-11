"""Vision worker behaviour that does not need a real face.

The available camera returned a uniform frame with no scene in it, so the
worker loop is exercised against the built-in synthetic frame source
(``camera_index = -1``). That still covers thread lifecycle, camera status,
tracking-loss handling, calibration gating and resource release.
"""

from __future__ import annotations

import time

import pytest

from app.control.runtime import Runtime
from app.voice.dictation import DictationSession


def wait_for(predicate, timeout: float = 4.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_vision_dependencies_are_available(runtime: Runtime) -> None:
    assert runtime.tracker.available is True
    assert runtime.tracker.unavailable_reason is None


def test_worker_starts_and_reports_status(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        assert runtime.tracker.running is True
        assert runtime.state.camera_status == "running"
        assert wait_for(lambda: runtime.tracker.status()["fps"] > 0, timeout=5.0), (
            "worker produced no frames"
        )
    finally:
        runtime.tracker.stop()


def test_missing_face_marks_tracking_lost_without_moving_the_cursor(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        start_position = (runtime.state.cursor_nx, runtime.state.cursor_ny)
        assert wait_for(
            lambda: runtime.state.tracking_reason == "tracking_lost", timeout=5.0
        ), "tracking loss was not reported"
        assert runtime.state.tracking is False
        assert runtime.state.faces_detected == 0
        # No landmark means no new position: the pointer must not drift or jump.
        assert (runtime.state.cursor_nx, runtime.state.cursor_ny) == start_position
        assert start_position == (0.5, 0.5)
        assert runtime.state.cursor_valid is False
    finally:
        runtime.tracker.stop()


def test_clicks_are_blocked_while_tracking_is_absent(runtime: Runtime) -> None:
    runtime.state.pointer_injection_enabled = True
    runtime.state.calibrated = True
    runtime.state.set_cursor_control(True, source="test")
    runtime.state.set_paused(False, source="test")
    runtime.tracker.start()
    try:
        assert wait_for(
            lambda: runtime.state.tracking_reason == "tracking_lost", timeout=5.0
        )
        decision = runtime.dispatcher.dispatch(trigger="test")
        assert decision.allowed is False
        assert decision.reason in ("tracking_lost", "cursor_stale")
    finally:
        runtime.tracker.stop()


def test_calibration_waits_for_a_face_instead_of_failing_silently(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        runtime.tracker.request_calibration()
        assert runtime.state.calibration_state == "countdown"
        assert wait_for(
            lambda: "Waiting for a face" in (runtime.state.calibration_detail or ""),
            timeout=5.0,
        ), "calibration did not report that it is waiting for a face"
        assert runtime.state.calibrated is False
        assert runtime.state.calibration_progress == 0.0
    finally:
        runtime.tracker.stop()


def test_calibration_can_be_cancelled(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        runtime.tracker.request_calibration()
        status = runtime.tracker.cancel_calibration()
        assert status["state"] == "idle"
        assert runtime.state.calibrated is False
    finally:
        runtime.tracker.stop()


def test_stopping_releases_the_camera(runtime: Runtime) -> None:
    runtime.tracker.start()
    assert wait_for(lambda: runtime.state.camera_status == "running", timeout=4.0)
    runtime.tracker.stop()
    assert runtime.tracker.running is False
    assert runtime.state.camera_status == "stopped"
    assert runtime.state.tracking is False


def test_unavailable_vision_does_not_break_startup(runtime: Runtime, monkeypatch) -> None:
    """With no vision stack the rest of the system must still function."""
    monkeypatch.setattr(runtime.tracker, "_available", False)
    monkeypatch.setattr(runtime.tracker, "_unavailable_reason", "simulated missing dependency")
    status = runtime.tracker.start()
    assert runtime.tracker.running is False
    assert status["available"] is False
    assert runtime.state.camera_status == "unavailable"
    # Core state still works.
    assert runtime.state.snapshot()["paused"] is True


def test_tracking_quality_is_recorded_as_a_series(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        assert wait_for(
            lambda: len(runtime.telemetry.duration_series("tracking_quality")) > 0,
            timeout=6.0,
        ), "no tracking quality samples were recorded"
        values = runtime.telemetry.duration_series("tracking_quality")
        assert all(0.0 <= value <= 1.0 for value in values)
    finally:
        runtime.tracker.stop()


def test_dictation_session_isolated_from_state(runtime: Runtime) -> None:
    session = DictationSession(runtime.state, runtime.telemetry)
    assert session.active is False
    session.enter(source="test")
    assert session.active is True
    session.append("hello there", source="test")
    assert "hello there" in runtime.state.dictation_text
    session.exit(source="test")
    assert session.active is False
    assert runtime.state.voice_mode == "idle"


def test_tracking_loss_telemetry_is_emitted(runtime: Runtime) -> None:
    runtime.tracker.start()
    try:
        assert wait_for(
            lambda: any(
                event["message"] == "tracking_lost"
                for event in runtime.telemetry.recent(limit=80, kind="tracking")
            ),
            timeout=5.0,
        )
    finally:
        runtime.tracker.stop()


@pytest.mark.parametrize("pause_state", [True, False])
def test_pause_survives_worker_restart(runtime: Runtime, pause_state: bool) -> None:
    runtime.state.set_paused(pause_state, source="test")
    runtime.tracker.start()
    try:
        assert wait_for(lambda: runtime.tracker.status()["fps"] > 0, timeout=5.0)
        assert runtime.state.paused is pause_state
    finally:
        runtime.tracker.stop()
