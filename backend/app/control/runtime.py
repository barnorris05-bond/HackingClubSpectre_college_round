"""Composition root.

Builds the single set of long-lived services the API talks to and owns the
policy decisions that span pipelines: what a transcript means, which action it
maps to, and whether safety allows it to run.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.browser.agent import BrowserAgent
from app.config import MUTABLE_SETTINGS, Settings, settings as default_settings
from app.control.safety import ClickDispatcher
from app.control.state import ControlState
from app.telemetry.log import Telemetry, describe_series
from app.vision.cursor import CursorController
from app.vision.tracker import FaceTracker
from app.voice.commands import Command, CommandKind, classify
from app.voice.dictation import DictationSession


class Runtime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings
        self.telemetry = Telemetry(
            self.settings.telemetry_path,
            ring_size=self.settings.telemetry_ring_size,
            max_bytes=self.settings.telemetry_max_bytes,
        )
        self.state = ControlState(self.telemetry, self.settings)
        self.cursor = CursorController(self.telemetry, self.settings)
        self.tracker = FaceTracker(self.state, self.telemetry, self.cursor, self.settings)
        self.dispatcher = ClickDispatcher(
            self.state, self.telemetry, self.cursor, self.settings
        )
        self.dictation = DictationSession(self.state, self.telemetry)
        self.browser = BrowserAgent(self.state, self.telemetry, self.settings)
        self._tasks: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    async def startup(self) -> None:
        import asyncio

        self.telemetry.attach_loop(asyncio.get_running_loop())
        self.cursor.refresh_screen_size()
        self.state.set_screen_size(*self.cursor.screen_size)
        self.state.set_cursor_backend(self.cursor.available, self.cursor.detail)
        self.state.set_vision_availability(
            self.tracker.available, self.tracker.unavailable_reason
        )
        self.state.browser_status = "not_started"
        self.state.browser_detail = (
            self.browser.status()["detail"]
            if self.browser.available
            else self.browser.status()["detail"]
        )
        self.telemetry.emit(
            "system",
            "runtime_started",
            data={
                "cursor_backend": self.cursor.detail,
                "vision_available": self.tracker.available,
                "vision_unavailable_reason": self.tracker.unavailable_reason,
                "browser_available": self.browser.available,
                "dry_run": self.settings.dry_run,
            },
        )

    async def shutdown(self) -> None:
        self.tracker.stop()
        await self.browser.stop()
        self.telemetry.emit("system", "runtime_stopped")

    # ------------------------------------------------------------------ #
    def settings_view(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name in MUTABLE_SETTINGS:
            payload[name] = getattr(self.settings, name)
        return {
            "mutable": sorted(MUTABLE_SETTINGS.keys()),
            "values": payload,
            "read_only": {
                "camera_index": self.settings.camera_index,
                "frame_width": self.settings.frame_width,
                "frame_height": self.settings.frame_height,
                "target_fps": self.settings.target_fps,
                "calibration_countdown_s": self.settings.calibration_countdown_s,
                "calibration_sample_s": self.settings.calibration_sample_s,
                "calibration_max_duration_s": self.settings.calibration_max_duration_s,
                "stability_window_s": self.settings.stability_window_s,
                "telemetry_file": str(self.settings.telemetry_path),
            },
        }

    def apply_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        applied: dict[str, Any] = {}
        rejected: dict[str, str] = {}
        for key, value in patch.items():
            expected = MUTABLE_SETTINGS.get(key)
            if expected is None:
                rejected[key] = "not a runtime-mutable setting"
                continue
            try:
                if expected is bool:
                    if isinstance(value, str):
                        coerced = value.strip().lower() in ("1", "true", "yes", "on")
                    else:
                        coerced = bool(value)
                elif expected is float:
                    coerced = float(value)
                    if coerced != coerced or coerced in (float("inf"), float("-inf")):
                        raise ValueError("not a finite number")
                elif expected is int:
                    coerced = int(value)
                else:
                    coerced = str(value)
            except (TypeError, ValueError) as exc:
                rejected[key] = f"invalid value: {exc}"
                continue
            setattr(self.settings, key, coerced)
            applied[key] = coerced
        if applied:
            self._clamp_critical_settings()
            self.telemetry.emit(
                "settings",
                "settings_updated",
                data={"applied": applied, "rejected": rejected},
            )
        return {"applied": applied, "rejected": rejected, **self.settings_view()}

    def _clamp_critical_settings(self) -> None:
        """Keep safety-critical knobs inside sane bounds."""
        self.settings.dead_zone = min(max(self.settings.dead_zone, 0.0), 0.15)
        self.settings.cursor_gain = min(max(self.settings.cursor_gain, 0.1), 4.0)
        self.settings.head_span_x = min(max(self.settings.head_span_x, 0.01), 0.5)
        self.settings.head_span_y = min(max(self.settings.head_span_y, 0.01), 0.5)
        self.settings.click_cooldown_s = min(max(self.settings.click_cooldown_s, 0.2), 10.0)
        self.settings.stability_window_s = min(
            max(self.settings.stability_window_s, 0.05), 2.0
        )
        self.settings.stability_max_radius_px = min(
            max(self.settings.stability_max_radius_px, 5.0), 500.0
        )

    # ------------------------------------------------------------------ #
    # pointer injection (hardware-free testing path)
    # ------------------------------------------------------------------ #
    def inject_pointer(
        self,
        nx: float,
        ny: float,
        quality: float | None = None,
        tracking: bool = True,
    ) -> dict[str, Any]:
        """Feed a synthetic pointer position.

        Used by the API's test-support endpoint so safety gating can be
        exercised without a camera. It is disabled by default.
        """
        if not self.state.pointer_injection_enabled:
            return {
                "ok": False,
                "reason": "injection_disabled",
                "detail": "enable POST /api/control/pointer-injection first",
            }
        nx = min(max(float(nx), 0.0), 1.0)
        ny = min(max(float(ny), 0.0), 1.0)
        if tracking:
            self.state.record_pointer(nx, ny, *self.cursor.to_screen(nx, ny))
            self.state.set_tracking(
                True, "ok", quality=quality if quality is not None else 0.9, faces=1
            )
        else:
            self.state.freeze_pointer("tracking_lost")
            self.state.set_tracking(False, "tracking_lost", quality=0.0, faces=0)
        return {"ok": True, **self.state.snapshot()["cursor"]}

    # ------------------------------------------------------------------ #
    # voice pipeline
    # ------------------------------------------------------------------ #
    async def handle_voice(
        self,
        text: str,
        received_at: float | None = None,
        client_ts_ms: float | None = None,
        source: str = "browser-speech",
    ) -> dict[str, Any]:
        received_at = received_at if received_at is not None else time.monotonic()
        self.state.last_transcript = text

        # Dictation first: while active, speech is text and nothing else.
        dictation_result = self.dictation.route(text)
        if dictation_result is not None:
            return {"transcript": text, "route": "dictation", **dictation_result}

        command = classify(text, search_engine=self.settings.browser_search_engine)
        self.telemetry.emit(
            "voice",
            "transcript_classified",
            data={
                **command.to_dict(),
                "source": source,
                "client_ts_ms": client_ts_ms,
            },
        )

        result: dict[str, Any] = {"transcript": text, "route": "command", **command.to_dict()}

        if command.kind is CommandKind.DIRECT_CLICK:
            decision = self.dispatcher.dispatch(
                trigger="voice:direct_click",
                button=command.params.get("button", "left"),
                received_at=received_at,
            )
            result["decision"] = decision.to_dict()
            result["executed"] = decision.allowed
        elif command.kind is CommandKind.STOP:
            self.state.set_emergency_stop(True, source="voice")
            result["executed"] = True
            result["action_result"] = "emergency_stop engaged"
        elif command.kind is CommandKind.PAUSE:
            self.state.set_paused(True, source="voice")
            result["executed"] = True
        elif command.kind is CommandKind.RESUME:
            self.state.set_paused(False, source="voice")
            result["executed"] = True
        else:
            action_result = await self.execute_browser_command(command)
            result["action_result"] = action_result
            result["executed"] = bool(action_result.get("ok"))

        self.state.record_command(text, str(command.kind), result, executed=result.get("executed"))
        if client_ts_ms:
            latency = (time.time() * 1000.0) - float(client_ts_ms)
            if 0 <= latency < 60_000:
                self.telemetry.record_duration("voice_end_to_end_ms", latency)
                result["client_latency_ms"] = round(latency, 2)
        return result

    async def execute_browser_command(self, command: Command) -> dict[str, Any]:
        """Map a structured command onto the browser agent or the OS."""
        kind = command.kind
        if kind is CommandKind.SCROLL:
            direction = command.action or "down"
            if direction.startswith("to_"):
                direction = direction
            pixels = command.params.get("pixels")
            if self.browser.status().get("started"):
                return await self.browser.dispatch_action(
                    "scroll", direction=direction, pixels=pixels
                )
            clicks = -(pixels or 600) // 40 if direction == "up" else (pixels or 600) // 40
            if direction.startswith("to_"):
                clicks = -50 if direction == "to_top" else 50
            decision = self.dispatcher.dispatch(
                trigger=f"voice:scroll_os:{direction}",
                received_at=None,
                ignore_cooldown=True,
                require_stability=False,
                require_quality=False,
                scroll=int(clicks),
            )
            return {
                "ok": decision.allowed,
                "reason": decision.reason,
                "target": "os",
                "clicks": int(clicks),
                "detail": "browser agent not running; scrolled the OS window under the pointer",
                **decision.detail,
            }
        if kind is CommandKind.SEARCH:
            return await self.browser.dispatch_action(
                "search", query=command.target or ""
            )
        if kind is CommandKind.NAVIGATE:
            if command.action == "goto":
                url = command.params.get("url")
                if not url:
                    return {
                        "ok": False,
                        "reason": "unresolved_target",
                        "detail": (
                            f"'{command.target}' is not a URL. Say 'search for "
                            f"{command.target}' or give a full site name."
                        ),
                    }
                return await self.browser.dispatch_action("goto", url=url)
            return await self.browser.dispatch_action(command.action or "")
        if kind is CommandKind.TAB:
            tab_actions = {
                "new": "new_tab",
                "next": "next_tab",
                "previous": "previous_tab",
                "switch": "switch_tab",
                "close": "close_tab",
            }
            return await self.browser.dispatch_action(
                tab_actions.get(command.action or "", command.action or ""),
                index=command.params.get("index"),
            )
        if kind is CommandKind.BROWSER_CLICK:
            return await self.browser.dispatch_action(
                "click_target", target=command.target or "", role=command.params.get("role")
            )
        if kind is CommandKind.TYPE_TEXT:
            return await self.browser.dispatch_action(
                "type", text=command.target or ""
            )
        return {
            "ok": False,
            "reason": "unhandled_kind",
            "detail": f"no executor for {kind}",
        }

    # ------------------------------------------------------------------ #
    # evaluation
    # ------------------------------------------------------------------ #
    def start_task(self, name: str, input_mode: str, notes: str | None = None) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        record = {
            "id": task_id,
            "name": name,
            "input_mode": input_mode,
            "notes": notes,
            "started_at": time.time(),
            "started_monotonic": time.monotonic(),
        }
        self._tasks[task_id] = record
        self.telemetry.emit(
            "evaluation",
            "task_started",
            data={"id": task_id, "name": name, "input_mode": input_mode},
        )
        return {k: v for k, v in record.items() if k != "started_monotonic"}

    def finish_task(self, task_id: str, completed: bool = True) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            return {"ok": False, "reason": "unknown_task", "detail": task_id}
        duration_s = time.monotonic() - record["started_monotonic"]
        record.update(
            {
                "finished_at": time.time(),
                "duration_s": round(duration_s, 3),
                "completed": completed,
            }
        )
        self.telemetry.record_duration(
            f"task_duration_s:{record['input_mode']}", duration_s
        )
        self.telemetry.emit(
            "evaluation",
            "task_finished",
            data={
                "id": task_id,
                "name": record["name"],
                "input_mode": record["input_mode"],
                "duration_s": record["duration_s"],
                "completed": completed,
            },
        )
        return {k: v for k, v in record.items() if k != "started_monotonic"}

    def evaluation_summary(self) -> dict[str, Any]:
        messages = self.telemetry.messages()
        snapshot = self.state.snapshot()
        tasks = [
            {k: v for k, v in t.items() if k != "started_monotonic"}
            for t in self._tasks.values()
            if "duration_s" in t
        ]
        voice_tasks = [t for t in tasks if t["input_mode"] == "voice"]
        manual_tasks = [t for t in tasks if t["input_mode"] == "manual"]

        executed = messages.get("click_executed", 0)
        rejected = messages.get("click_rejected", 0)
        attempted = executed + rejected

        total_events = sum(messages.values())
        has_durations = any(len(v) > 0 for v in self.telemetry._durations.values())
        is_measured = (total_events > 0 or len(tasks) > 0 or has_durations)

        return {
            "measured": is_measured,
            "note": (
                "Every number below is aggregated from recorded events and "
                "measured timings. Nothing is estimated or simulated."
                if is_measured
                else "No measurements recorded yet. Start tasks or perform actions to record data."
            ),
            "clicks": {
                "attempts": attempted,
                "executed": executed,
                "rejected": rejected,
                "selection_error_rate": (
                    None if attempted == 0 else round(rejected / attempted, 4)
                ),
                "reject_reasons": self.telemetry.reject_reasons(),
            },
            "voice": {
                "transcripts": messages.get("transcript_classified", 0),
                "dictation_entries": messages.get("dictation_text", 0),
                "direct_click_intents": messages.get("click_executed", 0)
                + messages.get("click_rejected", 0),
                "browser_actions": sum(
                    v
                    for k, v in messages.items()
                    if k
                    in (
                        "navigated",
                        "target_clicked",
                        "scrolled",
                        "tab_new",
                        "tab_next",
                        "tab_previous",
                        "tab_close",
                        "tab_switch",
                        "typed_text",
                        "history_back",
                        "history_forward",
                        "history_reload",
                    )
                ),
                "browser_action_rejections": messages.get(
                    "browser_action_rejected", 0
                ),
            },
            "latency": {
                "click_dispatch_ms": describe_series(
                    self.telemetry.duration_series("click_dispatch_ms"), "ms"
                ),
                "voice_end_to_end_ms": describe_series(
                    self.telemetry.duration_series("voice_end_to_end_ms"), "ms"
                ),
                "note": (
                    "voice_end_to_end_ms spans browser speech-final timestamp to "
                    "dispatch completion and is only present when the client "
                    "sends a timestamp. Server-side dispatch latency is reported "
                    "separately."
                ),
            },
            "calibration": describe_series(
                self.telemetry.duration_series("calibration_s"), "s"
            ),
            "tracking_quality": describe_series(
                self.telemetry.duration_series("tracking_quality"), "ratio"
            ),
            "task_timing": {
                "voice": describe_series(
                    self.telemetry.duration_series("task_duration_s:voice"), "s"
                ),
                "manual": describe_series(
                    self.telemetry.duration_series("task_duration_s:manual"), "s"
                ),
                "voice_is_faster": (
                    None
                    if not voice_tasks or not manual_tasks
                    else round(
                        sum(t["duration_s"] for t in manual_tasks) / len(manual_tasks)
                        - sum(t["duration_s"] for t in voice_tasks) / len(voice_tasks),
                        3,
                    )
                ),
                "sessions": tasks,
            },
            "current": {
                "paused": snapshot["paused"],
                "emergency_stop": snapshot["emergency_stop"],
                "cursor_control_enabled": snapshot["cursor_control_enabled"],
                "calibrated": snapshot["calibration"]["calibrated"],
                "tracking": snapshot["tracking"],
            },
        }

    def reset_evaluation(self) -> dict[str, Any]:
        self.telemetry.reset()
        self.state.reset_evaluation()
        self._tasks.clear()
        self.telemetry.emit("evaluation", "evaluation_reset")
        return self.evaluation_summary()
