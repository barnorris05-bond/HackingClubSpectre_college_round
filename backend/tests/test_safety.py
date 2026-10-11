"""Every click must pass every gate, and every refusal must carry a real reason.

These tests drive the dispatcher directly with dry_run enabled, so a bug that
wrongly allows a click cannot touch the host mouse.
"""

from __future__ import annotations

import time
import threading

import pytest

from app.control.runtime import Runtime
from app.control.safety import (
    REASON_COOLDOWN,
    REASON_CURSOR_DISABLED,
    REASON_EMERGENCY_STOP,
    REASON_NOT_CALIBRATED,
    REASON_OK,
    REASON_PAUSED,
    REASON_POINTER_UNSTABLE,
    REASON_TRACKING_LOST,
)
from app.config import Settings


def arm_ready_pointer(runtime: Runtime, nx: float = 0.5, ny: float = 0.5) -> None:
    """Bring the system to the state where a click *should* be allowed."""
    runtime.state.pointer_injection_enabled = True
    runtime.state.calibrated = True
    runtime.state.calibration_state = "ready"
    runtime.state.set_cursor_control(True, source="test")
    runtime.state.set_paused(False, source="test")
    # Two samples inside the stability window: one is not enough to judge
    # steadiness, and the gate correctly refuses with too little history.
    runtime.inject_pointer(nx, ny, quality=0.9, tracking=True)
    runtime.inject_pointer(nx, ny, quality=0.9, tracking=True)


def test_click_allowed_when_every_precondition_holds(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is True
    assert decision.reason == REASON_OK
    assert runtime.state.click_count == 1
    assert runtime.state.rejected_click_count == 0


def test_emergency_stop_blocks_and_disarms(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.set_emergency_stop(True, source="test")
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_EMERGENCY_STOP
    assert runtime.state.cursor_control_enabled is False


def test_cursor_control_is_off_by_default(runtime: Runtime) -> None:
    assert runtime.state.cursor_control_enabled is False
    decision = runtime.dispatcher.evaluate()
    assert decision.reason == REASON_CURSOR_DISABLED


def test_pause_blocks_clicks(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.set_paused(True, source="test")
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_PAUSED


def test_uncalibrated_blocks_clicks(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.calibrated = False
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_NOT_CALIBRATED


def test_tracking_loss_blocks_clicks(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.inject_pointer(0.5, 0.5, tracking=False)
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_TRACKING_LOST


def test_lost_tracking_freezes_the_pointer_instead_of_jumping(runtime: Runtime) -> None:
    arm_ready_pointer(runtime, nx=0.72, ny=0.28)
    before = (runtime.state.cursor_nx, runtime.state.cursor_ny)
    runtime.inject_pointer(0.5, 0.5, tracking=False)
    after = (runtime.state.cursor_nx, runtime.state.cursor_ny)
    # The last valid position is retained; the pointer is not reset anywhere.
    assert after == before
    assert after != (0.5, 0.5) or before == (0.5, 0.5)


def test_single_sample_is_not_enough_to_judge_stability(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.trail.clear()
    runtime.inject_pointer(0.5, 0.5, quality=0.9, tracking=True)
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_POINTER_UNSTABLE


def test_unstable_pointer_blocks_clicks(runtime: Runtime) -> None:
    arm_ready_pointer(runtime, nx=0.5, ny=0.5)
    # Two very different samples inside the stability window => unstable.
    runtime.inject_pointer(0.95, 0.5, quality=0.9, tracking=True)
    stability = runtime.state.pointer_stability()
    assert stability["stable"] is False
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == REASON_POINTER_UNSTABLE


def test_low_tracking_quality_blocks_clicks(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.tracking_quality = 0.05
    runtime.state.trail.clear()
    runtime.inject_pointer(0.5, 0.5, quality=0.05, tracking=True)
    runtime.inject_pointer(0.5, 0.5, quality=0.05, tracking=True)
    runtime.state.tracking_quality = 0.05
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == "low_tracking_quality"


def test_cooldown_blocks_a_second_click(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    first = runtime.dispatcher.dispatch(trigger="test")
    assert first.allowed is True, first.reason
    second = runtime.dispatcher.dispatch(trigger="test")
    assert second.allowed is False
    assert second.reason == REASON_COOLDOWN
    assert second.detail["remaining_s"] > 0


def test_cooldown_can_be_bypassed_for_os_scrolling(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.dispatcher.dispatch(trigger="test")
    decision = runtime.dispatcher.dispatch(
        trigger="test:scroll", ignore_cooldown=True, require_stability=False, scroll=3
    )
    assert decision.allowed is True


def test_rejections_are_counted_with_reasons(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.set_paused(True, source="test")
    runtime.dispatcher.dispatch(trigger="test")
    runtime.state.set_paused(False, source="test")
    runtime.inject_pointer(0.5, 0.5, tracking=False)
    runtime.dispatcher.dispatch(trigger="test")

    # Only refused actuations are counted here, so unrelated events that share
    # a reason string (tracking loss) do not inflate the number.
    reasons = runtime.telemetry.reject_reasons()
    assert reasons == {REASON_PAUSED: 1, REASON_TRACKING_LOST: 1}
    assert runtime.state.rejected_click_count == 2
    assert runtime.state.click_count == 0


def test_stale_pointer_is_rejected(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.cursor_updated_at = time.monotonic() - 5.0
    decision = runtime.dispatcher.dispatch(trigger="test")
    assert decision.allowed is False
    assert decision.reason == "cursor_stale"


def test_pointer_injection_refused_until_enabled(settings: Settings) -> None:
    from app.control.runtime import Runtime as RuntimeClass

    fresh = RuntimeClass(settings)
    assert fresh.state.pointer_injection_enabled is False
    result = fresh.inject_pointer(0.5, 0.5)
    assert result["ok"] is False
    assert result["reason"] == "injection_disabled"


@pytest.mark.parametrize("blocked_by", ["pause", "emergency_stop"])
def test_tracker_os_pointer_move_is_blocked_by_safety_switch(
    runtime: Runtime, monkeypatch, blocked_by: str
) -> None:
    moves: list[tuple[float, float]] = []
    runtime.cursor._available = True
    runtime.state.cursor_control_enabled = True
    runtime.state.paused = False
    runtime.state.emergency_stop = False
    monkeypatch.setattr(runtime.cursor, "move_to", lambda nx, ny: moves.append((nx, ny)))

    if blocked_by == "pause":
        runtime.state.set_paused(True, source="test")
    else:
        runtime.state.set_emergency_stop(True, source="test")

    runtime.tracker._maybe_move_os_cursor(0.7, 0.3)
    assert moves == []


def test_pause_waits_for_in_progress_mock_pointer_move_then_blocks_next(
    runtime: Runtime, monkeypatch
) -> None:
    entered_move = threading.Event()
    release_move = threading.Event()
    pause_started = threading.Event()
    moves: list[tuple[float, float]] = []
    runtime.cursor._available = True
    runtime.state.cursor_control_enabled = True
    runtime.state.paused = False
    runtime.state.emergency_stop = False

    def mock_move(nx: float, ny: float) -> None:
        moves.append((nx, ny))
        entered_move.set()
        assert release_move.wait(timeout=2)

    monkeypatch.setattr(runtime.cursor, "move_to", mock_move)
    mover = threading.Thread(
        target=runtime.tracker._maybe_move_os_cursor, args=(0.6, 0.4)
    )
    mover.start()
    assert entered_move.wait(timeout=2)

    def pause() -> None:
        pause_started.set()
        runtime.state.set_paused(True, source="test")

    pauser = threading.Thread(target=pause)
    pauser.start()
    assert pause_started.wait(timeout=2)
    release_move.set()
    mover.join(timeout=2)
    pauser.join(timeout=2)
    assert not mover.is_alive()
    assert not pauser.is_alive()

    runtime.tracker._maybe_move_os_cursor(0.8, 0.2)
    assert moves == [(0.6, 0.4)]


def test_rejection_telemetry_records_the_decision(runtime: Runtime) -> None:
    arm_ready_pointer(runtime)
    runtime.state.set_paused(True, source="test")
    runtime.dispatcher.dispatch(trigger="voice:direct_click")
    events = runtime.telemetry.recent(limit=50, kind="click")
    assert events, "a rejected click must still be recorded"
    last = events[-1]
    assert last["message"] == "click_rejected"
    assert last["reason"] == REASON_PAUSED
    assert last["data"]["trigger"] == "voice:direct_click"


@pytest.mark.parametrize("button", ["left", "right"])
def test_button_is_propagated_to_the_record(runtime: Runtime, button: str) -> None:
    arm_ready_pointer(runtime)
    runtime.dispatcher.dispatch(trigger="test", button=button)
    assert runtime.state.last_click is not None
    assert runtime.state.last_click.button == button
