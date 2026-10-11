"""Head tracking worker: MediaPipe face landmarks -> calibrated cursor position.

Pipeline A of the architecture:

    webcam -> OpenCV capture -> MediaPipe FaceLandmarker -> neutral calibration
    -> tilt/scale normalisation -> dead zone -> One Euro smoothing
    -> clamped normalised cursor -> (optional) OS cursor movement

Design notes that matter for correctness:

* The capture/inference loop runs in its own thread. Nothing here blocks the
  asyncio event loop, and the API only ever reads published state.
* A missing face never moves the cursor. Tracking loss freezes the last valid
  position instead of falling back to a default (which would be a jump to the
  screen origin or centre).
* ``tracking_quality`` is an explicitly documented *heuristic* built from
  detection ratio, presence and pointer steadiness. MediaPipe's FaceLandmarker
  does not expose a calibrated probability of correctness, so we do not present
  one.
"""

from __future__ import annotations

import math
import logging
import threading
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any

from app.config import Settings, settings as default_settings
from app.control.state import ControlState
from app.telemetry.log import Telemetry
from app.vision.cursor import CursorController
from app.vision.filters import OneEuroFilter2D, radial_dead_zone

MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
logger = logging.getLogger(__name__)

# FaceLandmarker landmark indices (468-point canonical face mesh).
NOSE_TIP = 1
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263
EYE_INNER_L = 133
EYE_INNER_R = 362
# Subset drawn on the preview stream.
PREVIEW_POINTS = [1, 33, 263, 133, 362, 61, 291, 199, 152, 10]

SYNTHETIC_CAMERA_INDEX = -1  # tests can run the whole loop with no hardware


def map_head_to_cursor(
    anchor: tuple[float, float],
    iod: float,
    neutral: tuple[float, float] | None,
    neutral_iod: float | None,
    settings: Settings,
) -> tuple[float, float]:
    """Pure mapping from a face anchor to a clamped normalised cursor position.

    Kept free of state so the geometry is directly unit-testable:

    1. subtract the calibrated neutral pose,
    2. rescale by the inter-ocular distance so leaning in/out does not change
       the gain (posture robustness),
    3. remove a radial dead zone,
    4. map through the configured head span onto [0, 1] and clamp.
    """
    if neutral is None:
        neutral = anchor
    if neutral_iod and neutral_iod > 1e-6:
        scale = min(max(neutral_iod / iod, 0.6), 1.6) if iod > 1e-6 else 1.0
    else:
        scale = 1.0

    dx = (anchor[0] - neutral[0]) * scale
    dy = (anchor[1] - neutral[1]) * scale
    dz_x, dz_y, _ = radial_dead_zone(dx, dy, settings.dead_zone)

    # Compensate the dead zone so the configured head span still reaches the
    # screen edge. Without this the dead zone would eat the outer range and
    # leave an unreachable margin at the edges.
    span_x = max(settings.head_span_x - settings.dead_zone, 1e-6)
    span_y = max(settings.head_span_y - settings.dead_zone, 1e-6)
    nx = 0.5 + (dz_x / span_x) * 0.5 * settings.cursor_gain
    ny = 0.5 + (dz_y / span_y) * 0.5 * settings.cursor_gain
    if settings.invert_x:
        nx = 1.0 - nx
    if settings.invert_y:
        ny = 1.0 - ny
    return min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)


class CalibrationSession:
    def __init__(self, countdown_s: float, sample_s: float, max_s: float) -> None:
        self.started = time.monotonic()
        self.countdown_s = countdown_s
        self.sample_s = sample_s
        self.max_s = max_s
        self.phase = "countdown"
        self.countdown_anchor: float | None = None
        self.samples: list[tuple[float, float]] = []
        self.iods: list[float] = []

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def timed_out(self) -> bool:
        return self.elapsed() > self.max_s


class FaceTracker:
    def __init__(
        self,
        state: ControlState,
        telemetry: Telemetry,
        cursor: CursorController,
        settings: Settings | None = None,
    ) -> None:
        self._state = state
        self._telemetry = telemetry
        self._cursor = cursor
        self._settings = settings or default_settings

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._landmarker: Any = None
        self._startup_error: str | None = None
        self._startup_error_code: str | None = None
        self._cv2: Any = None
        self._mp: Any = None
        self._capture: Any = None
        self._frame_index = 0
        self._last_ts_ms = 0

        # availability
        self._available = False
        self._unavailable_reason: str | None = None
        self._probe_dependencies()

        # tracking internals
        self._camera_error: str | None = None
        self._smoother = OneEuroFilter2D(
            self._settings.smooth_min_cutoff,
            self._settings.smooth_beta,
            self._settings.smooth_d_cutoff,
        )
        self._neutral: tuple[float, float] | None = None
        self._neutral_iod: float | None = None
        self._detection_window: deque[tuple[float, bool]] = deque(maxlen=120)
        self._last_face_at: float | None = None
        self._calibration: CalibrationSession | None = None
        self._cursor_enabled_announced = False

        # frame publication
        self._latest_jpeg: bytes | None = None
        self._stream_clients = 0
        self._fps_window: deque[float] = deque(maxlen=30)
        self._inference_ms: float = 0.0

    # ------------------------------------------------------------------ #
    # dependency probing
    # ------------------------------------------------------------------ #
    def _probe_dependencies(self) -> None:
        if not MODEL_PATH.exists():
            self._available = False
            self._unavailable_reason = (
                f"face landmark model missing at {MODEL_PATH}"
            )
            return
        try:
            import cv2  # noqa: F401
            import mediapipe as mp  # noqa: F401
            from mediapipe.tasks.python.vision import FaceLandmarker  # noqa: F401
        except Exception as exc:
            self._available = False
            self._unavailable_reason = (
                f"vision dependency import failed: {type(exc).__name__}: {exc}"
            )
            return
        self._available = True
        self._unavailable_reason = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if not self._available:
                self._state.set_vision_availability(False, self._unavailable_reason)
                self._telemetry.emit(
                    "vision",
                    "vision_unavailable",
                    reason="dependency_missing",
                    data={"detail": self._unavailable_reason},
                )
                return self.status()
            self._startup_error = None
            self._startup_error_code = None
            try:
                # Validate the actual configured model before reporting a
                # running worker. File presence and importability are not
                # enough to prove MediaPipe can construct this task.
                self._landmarker_instance()
            except Exception as exc:
                self._startup_error_code = "model_load_failed"
                self._startup_error = (
                    f"Could not load MediaPipe model at {MODEL_PATH}: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.exception("Failed to initialize MediaPipe FaceLandmarker from %s", MODEL_PATH)
                self._state.set_camera_status("error", self._startup_error)
                self._state.freeze_pointer("vision_error")
                self._state.set_tracking(False, "vision_error", quality=0.0, faces=0)
                self._telemetry.emit(
                    "vision",
                    "vision_model_load_failed",
                    reason="model_load_failed",
                    data={"model": str(MODEL_PATH), "error": self._startup_error},
                )
                return self.status()
            self._stop_event.clear()
            self._state.set_camera_status("starting")
            self._state.set_vision_availability(True, None)
            self._thread = threading.Thread(
                target=self._run, name="handsfree-vision", daemon=True
            )
            self._thread.start()
        self._telemetry.emit("vision", "vision_started")
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        with self._lock:
            self._thread = None
            self._release_capture()
            self._latest_jpeg = None
            # Do not tear down a MediaPipe task while its worker is still
            # executing inference. If it stopped cleanly, release the task so
            # repeated start/stop cycles do not leak native worker resources.
            if not (thread and thread.is_alive()) and self._landmarker is not None:
                landmarker, self._landmarker = self._landmarker, None
                try:
                    landmarker.close()
                except Exception:
                    logger.exception("Failed to close MediaPipe FaceLandmarker")
        self._state.set_camera_status("stopped", "camera released by user request")
        self._state.set_tracking(False, "stopped", quality=0.0, faces=0)
        self._telemetry.emit("vision", "vision_stopped")
        return self.status()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self._available,
                "unavailable_reason": self._unavailable_reason,
                "running": self.running,
                "fps": round(self._fps(), 1),
                "inference_ms": round(self._inference_ms, 2),
                "stream_clients": self._stream_clients,
                "model": str(MODEL_PATH.name),
                "synthetic_source": self._settings.camera_index == SYNTHETIC_CAMERA_INDEX,
                "error": self._startup_error or self._camera_error,
                "error_code": self._startup_error_code,
                "calibration": {
                    "state": self._state.calibration_state,
                    "progress": round(self._state.calibration_progress, 3),
                },
            }

    def _fps(self) -> float:
        if len(self._fps_window) < 2:
            return 0.0
        span = self._fps_window[-1] - self._fps_window[0]
        if span <= 0:
            return 0.0
        return (len(self._fps_window) - 1) / span

    # ------------------------------------------------------------------ #
    # calibration
    # ------------------------------------------------------------------ #
    def request_calibration(self) -> dict[str, Any]:
        with self._lock:
            self._calibration = CalibrationSession(
                self._settings.calibration_countdown_s,
                self._settings.calibration_sample_s,
                self._settings.calibration_max_duration_s,
            )
            self._state.calibration_state = "countdown"
            self._state.calibration_progress = 0.0
            self._state.calibration_detail = (
                "Look straight ahead at the camera and hold still."
            )
            self._state.calibrated = False
            self._state.set_camera_status(self._state.camera_status)
        self._telemetry.emit(
            "calibration",
            "calibration_started",
            data={
                "countdown_s": self._settings.calibration_countdown_s,
                "sample_s": self._settings.calibration_sample_s,
                "budget_s": self._settings.calibration_max_duration_s,
            },
        )
        return self.calibration_status()

    def cancel_calibration(self) -> dict[str, Any]:
        with self._lock:
            if self._calibration is not None:
                self._calibration = None
                self._state.calibration_state = "idle"
                self._state.calibration_detail = "cancelled"
                self._state.calibration_progress = 0.0
        self._telemetry.emit("calibration", "calibration_cancelled")
        return self.calibration_status()

    def calibration_status(self) -> dict[str, Any]:
        with self._lock:
            session = self._calibration
        return {
            "state": self._state.calibration_state,
            "progress": round(self._state.calibration_progress, 3),
            "detail": self._state.calibration_detail,
            "calibrated": self._state.calibrated,
            "calibrated_at": self._state.calibrated_at,
            "neutral": self._state.neutral,
            "elapsed_s": (
                None if session is None else round(session.elapsed(), 2)
            ),
            "budget_s": self._settings.calibration_max_duration_s,
        }

    def _finish_calibration(self, ok: bool, detail: str) -> None:
        with self._lock:
            session = self._calibration
            self._calibration = None
            if session is None:
                return
            elapsed = session.elapsed()
            if not ok:
                self._state.calibration_state = "failed"
                self._state.calibration_detail = detail
                self._state.calibrated = False
                self._state.calibration_progress = 0.0
                self._telemetry.emit(
                    "calibration",
                    "calibration_failed",
                    reason="calibration_failed",
                    data={"detail": detail, "elapsed_s": round(elapsed, 2)},
                )
                return

            xs = [s[0] for s in session.samples]
            ys = [s[1] for s in session.samples]
            neutral = (median(xs), median(ys))
            iod = median(session.iods) if session.iods else None
            # Real measured jitter -> adaptive dead zone (requirement 7: adapt
            # to changing lighting/posture rather than a fixed magic number).
            jitter = median(
                [math.hypot(x - neutral[0], y - neutral[1]) for x, y in session.samples]
            )
            dead_zone = min(max(jitter * 3.0, 0.006), 0.05)
            self._settings.dead_zone = round(dead_zone, 4)

            self._neutral = neutral
            self._neutral_iod = iod
            self._smoother.reset()
            self._state.neutral = (round(neutral[0], 5), round(neutral[1], 5))
            self._state.calibrated = True
            self._state.calibrated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._state.calibration_state = "ready"
            self._state.calibration_progress = 1.0
            self._state.calibration_detail = (
                f"Neutral captured from {len(session.samples)} samples in "
                f"{elapsed:.1f}s; dead zone auto-set to {dead_zone:.4f}"
            )

        self._telemetry.emit(
            "calibration",
            "calibration_ready",
            data={
                "samples": len(session.samples),
                "elapsed_s": round(elapsed, 2),
                "dead_zone": round(dead_zone, 4),
                "jitter": round(jitter, 5),
                "neutral": [round(neutral[0], 5), round(neutral[1], 5)],
                "iod": None if iod is None else round(iod, 5),
            },
        )
        self._telemetry.record_duration("calibration_s", elapsed)

    # ------------------------------------------------------------------ #
    # worker loop
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        settings = self._settings
        frame_interval = 1.0 / max(settings.target_fps, 1)
        open_attempts = 0
        quality_samples = 0
        while not self._stop_event.is_set():
            if not self._ensure_capture():
                open_attempts += 1
                # Back off but keep trying: the user may plug a camera in.
                delay = min(1.0 + open_attempts * 0.5, 5.0)
                self._state.set_camera_status(
                    "unavailable",
                    self._camera_error or "camera not available",
                )
                self._state.freeze_pointer("camera_unavailable")
                self._state.set_tracking(False, "camera_unavailable", quality=0.0, faces=0)
                if open_attempts == 1:
                    self._telemetry.emit(
                        "vision",
                        "camera_unavailable",
                        reason="camera_open_failed",
                        data={"detail": self._camera_error},
                    )
                self._stop_event.wait(delay)
                continue

            started = time.monotonic()
            frame = self._read_frame()
            if frame is None:
                continue
            try:
                self._process_frame(frame)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                logger.exception("Vision frame processing failed")
                self._startup_error_code = "inference_failed"
                self._startup_error = detail
                self._state.set_camera_status("error", detail)
                self._state.freeze_pointer("vision_error")
                self._state.set_tracking(False, "vision_error", quality=0.0, faces=0)
                self._telemetry.emit(
                    "vision",
                    "frame_processing_error",
                    reason="frame_error",
                    data={"error": detail, "model": str(MODEL_PATH)},
                )
                with self._lock:
                    failed_landmarker = self._landmarker
                    self._landmarker = None
                if failed_landmarker is not None:
                    try:
                        failed_landmarker.close()
                    except Exception:
                        logger.exception("Failed to release the failed MediaPipe task")
                self._stop_event.set()
                break
            with self._lock:
                self._fps_window.append(time.monotonic())
            quality_samples += 1
            if quality_samples >= 15:
                quality_samples = 0
                self._telemetry.record_series(
                    "tracking_quality", self._state.tracking_quality
                )
            sleep_for = frame_interval - (time.monotonic() - started)
            if sleep_for > 0:
                self._stop_event.wait(sleep_for)

        self._release_capture()

    def _ensure_capture(self) -> bool:
        with self._lock:
            if self._capture is not None:
                return True
            self._camera_error: str | None = None
            if self._settings.camera_index == SYNTHETIC_CAMERA_INDEX:
                self._capture = "synthetic"
                self._state.set_camera_status(
                    "running", "synthetic frame source (hardware-free test mode)"
                )
                return True
            try:
                import cv2

                self._cv2 = cv2
                backend = getattr(cv2, "CAP_DSHOW", 0)
                capture = cv2.VideoCapture(self._settings.camera_index, backend)
                if not capture.isOpened():
                    capture.release()
                    capture = cv2.VideoCapture(self._settings.camera_index)
                if not capture.isOpened():
                    capture.release()
                    self._camera_error = (
                        f"could not open camera index {self._settings.camera_index}"
                    )
                    return False
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._settings.frame_width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._settings.frame_height)
                capture.set(cv2.CAP_PROP_FPS, self._settings.target_fps)
                self._capture = capture
                self._state.set_camera_status(
                    "running",
                    f"camera {self._settings.camera_index} at "
                    f"{self._settings.frame_width}x{self._settings.frame_height}",
                )
                self._telemetry.emit(
                    "vision",
                    "camera_opened",
                    data={
                        "index": self._settings.camera_index,
                        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    },
                )
                return True
            except Exception as exc:
                self._camera_error = f"{type(exc).__name__}: {exc}"
                return False

    def _release_capture(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None and capture != "synthetic":
            try:
                capture.release()
            except Exception:
                pass
        self._camera_error = None

    def _read_frame(self) -> Any:
        capture = self._capture
        if capture == "synthetic":
            import numpy as np

            return np.random.randint(
                0, 60, (self._settings.frame_height, self._settings.frame_width, 3), dtype="uint8"
            )
        if capture is None:
            return None
        ok, frame = capture.read()
        if not ok or frame is None:
            self._release_capture()
            self._telemetry.emit(
                "vision",
                "camera_read_failed",
                reason="camera_read_failed",
                data={"detail": "frame read failed, reopening capture"},
            )
            self._state.freeze_pointer("camera_lost")
            self._state.set_tracking(False, "camera_lost", quality=0.0, faces=0)
            return None
        return frame

    # ------------------------------------------------------------------ #
    # per-frame processing
    # ------------------------------------------------------------------ #
    def _landmarker_instance(self) -> Any:
        if self._landmarker is not None:
            return self._landmarker
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            FaceLandmarker,
            FaceLandmarkerOptions,
            RunningMode,
        )

        self._mp = mp
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=RunningMode.VIDEO,
            num_faces=self._settings.max_faces,
            min_face_detection_confidence=self._settings.min_detection_confidence,
            min_tracking_confidence=self._settings.min_detection_confidence,
        )
        self._landmarker = FaceLandmarker.create_from_options(options)
        return self._landmarker

    def _process_frame(self, frame: Any) -> None:
        import cv2
        import mediapipe as mp
        import numpy as np

        # Mirror the frame so moving your head right moves the cursor right.
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)

        now = time.monotonic()
        ts_ms = int(now * 1000)
        if ts_ms <= self._last_ts_ms:
            ts_ms = self._last_ts_ms + 1
        self._last_ts_ms = ts_ms

        started = time.perf_counter()
        landmarker = self._landmarker_instance()
        result = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms
        )
        self._inference_ms = (time.perf_counter() - started) * 1000.0

        faces = result.face_landmarks or []
        self._detection_window.append((now, bool(faces)))

        anchor: tuple[float, float] | None = None
        iod: float | None = None
        presence: float | None = None
        if faces:
            landmarks = faces[0]
            nose = landmarks[NOSE_TIP]
            left = landmarks[LEFT_EYE_OUTER]
            right = landmarks[RIGHT_EYE_OUTER]
            inner_l = landmarks[EYE_INNER_L]
            inner_r = landmarks[EYE_INNER_R]

            eye_mid_x = (left.x + right.x) / 2.0
            eye_mid_y = (left.y + right.y) / 2.0
            ax = 0.55 * nose.x + 0.45 * eye_mid_x
            ay = 0.55 * nose.y + 0.45 * eye_mid_y

            # Remove head roll so a tilted head does not read as a diagonal
            # cursor push (posture robustness).
            angle = math.atan2(right.y - left.y, right.x - left.x)
            dx, dy = ax - eye_mid_x, ay - eye_mid_y
            cos_a, sin_a = math.cos(-angle), math.sin(-angle)
            anchor = (
                eye_mid_x + dx * cos_a - dy * sin_a,
                eye_mid_y + dx * sin_a + dy * cos_a,
            )
            iod = math.hypot(right.x - left.x, right.y - left.y)
            presences = [
                getattr(lm, "presence", 1.0)
                for lm in (nose, left, right, inner_l, inner_r)
                if getattr(lm, "presence", None) is not None
            ]
            if presences:
                presence = sum(presences) / len(presences)

        if anchor is not None and iod and iod > 1e-6:
            self._last_face_at = now
            self._on_face(anchor, iod, presence, now)
        else:
            self._on_no_face(now, presence)

        if self._stream_clients > 0:
            self._publish_jpeg(frame, result, anchor)

    # -- face present ---------------------------------------------------- #
    def _on_face(
        self,
        anchor: tuple[float, float],
        iod: float,
        presence: float | None,
        now: float,
    ) -> None:
        session = self._calibration
        if session is not None:
            self._advance_calibration(session, anchor, iod, now)

        nx, ny = map_head_to_cursor(
            anchor, iod, self._neutral, self._neutral_iod, self._settings
        )

        self._smoother.fx.min_cutoff = self._settings.smooth_min_cutoff
        self._smoother.fx.beta = self._settings.smooth_beta
        self._smoother.fy.min_cutoff = self._settings.smooth_min_cutoff
        self._smoother.fy.beta = self._settings.smooth_beta
        fx, fy = self._smoother(nx, ny, now)
        fx = min(max(fx, 0.0), 1.0)
        fy = min(max(fy, 0.0), 1.0)

        self._state.record_pointer(fx, fy, *self._cursor.to_screen(fx, fy))
        self._maybe_move_os_cursor(fx, fy)
        self._state.set_tracking(
            True,
            "ok",
            quality=self._quality(now, presence),
            faces=1,
        )

    # -- face absent ----------------------------------------------------- #
    def _on_no_face(self, now: float, presence: float | None) -> None:
        session = self._calibration
        if session is not None:
            # Hold the countdown while the face is missing rather than
            # calibrating against nothing.
            session.countdown_anchor = None
            self._state.calibration_detail = "Waiting for a face in view..."
            if session.timed_out():
                self._finish_calibration(
                    False, "no face detected during calibration window"
                )
            self._state.set_tracking(
                False,
                "no_face_during_calibration",
                quality=self._quality(now, presence),
                faces=0,
            )
            return

        last = self._last_face_at
        lost_for = math.inf if last is None else now - last
        if lost_for > self._settings.tracking_loss_grace_s:
            # Freeze: keep the last valid position, do not invent a new one.
            self._state.freeze_pointer("tracking_lost")
            self._state.set_tracking(
                False,
                "tracking_lost",
                quality=self._quality(now, presence),
                faces=0,
            )
        else:
            self._state.set_tracking(
                False, "grace_period", quality=self._quality(now, presence), faces=0
            )

    # -- calibration advance -------------------------------------------- #
    def _advance_calibration(
        self,
        session: CalibrationSession,
        anchor: tuple[float, float],
        iod: float,
        now: float,
    ) -> None:
        if session.timed_out():
            self._finish_calibration(False, "calibration exceeded its time budget")
            return

        if session.phase == "countdown":
            if session.countdown_anchor is None:
                session.countdown_anchor = now
                self._state.calibration_detail = "Hold still, look straight ahead."
            elapsed = now - session.countdown_anchor
            progress = min(elapsed / max(session.countdown_s, 0.1), 1.0)
            self._state.calibration_state = "countdown"
            self._state.calibration_progress = progress * 0.4
            if elapsed >= session.countdown_s:
                session.phase = "sampling"
                session.countdown_anchor = now
                self._state.calibration_state = "sampling"
                self._state.calibration_detail = "Capturing your neutral position..."
            return

        session.samples.append(anchor)
        session.iods.append(iod)
        elapsed = now - (session.countdown_anchor or now)
        progress = min(elapsed / max(session.sample_s, 0.1), 1.0)
        self._state.calibration_progress = 0.4 + progress * 0.6
        if elapsed >= session.sample_s and len(session.samples) >= 5:
            self._finish_calibration(True, "ok")

    # -- quality heuristic ---------------------------------------------- #
    def _quality(self, now: float, presence: float | None) -> float:
        window = 1.0
        recent = [seen for t, seen in self._detection_window if now - t <= window]
        detection_ratio = (sum(1 for s in recent if s) / len(recent)) if recent else 0.0
        stability = self._state.pointer_stability()
        radius = stability.get("radius_px")
        threshold = self._settings.stability_max_radius_px
        steadiness = (
            0.0
            if radius is None
            else max(0.0, min(1.0, 1.0 - (radius / max(threshold, 1e-6))))
        )
        presence_term = 1.0 if presence is None else max(0.0, min(1.0, presence))
        # Documented heuristic, deliberately not called a probability.
        return round(
            0.5 * detection_ratio + 0.3 * steadiness + 0.2 * presence_term, 4
        )

    # -- OS cursor actuation -------------------------------------------- #
    def _maybe_move_os_cursor(self, nx: float, ny: float) -> None:
        state = self._state
        # Keep the safety check and the actual move atomic with pause/e-stop
        # setters. The lock order matches ClickDispatcher: state, then cursor.
        with state.lock:
            if state.emergency_stop:
                return
            if not state.cursor_control_enabled:
                if self._cursor_enabled_announced:
                    self._cursor_enabled_announced = False
                return
            if state.paused:
                return
            if not self._cursor.available:
                return
            if not self._cursor_enabled_announced:
                self._cursor_enabled_announced = True
                self._telemetry.emit(
                    "cursor",
                    "os_cursor_active",
                    data={"dry_run": self._settings.dry_run},
                )
            self._cursor.move_to(nx, ny)

    # ------------------------------------------------------------------ #
    # preview stream
    # ------------------------------------------------------------------ #
    def subscribe_stream(self) -> None:
        with self._lock:
            self._stream_clients += 1

    def unsubscribe_stream(self) -> None:
        with self._lock:
            self._stream_clients = max(0, self._stream_clients - 1)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def _publish_jpeg(self, frame: Any, result: Any, anchor: tuple[float, float] | None) -> None:
        import cv2

        height, width = frame.shape[:2]
        overlay = frame.copy()

        # Dead-zone box around the neutral point.
        if self._neutral:
            nx0 = int((self._neutral[0] - self._settings.dead_zone) * width)
            nx1 = int((self._neutral[0] + self._settings.dead_zone) * width)
            ny0 = int((self._neutral[1] - self._settings.dead_zone) * height)
            ny1 = int((self._neutral[1] + self._settings.dead_zone) * height)
            cv2.rectangle(overlay, (nx0, ny0), (nx1, ny1), (0, 200, 255), 1)
            cx, cy = int(self._neutral[0] * width), int(self._neutral[1] * height)
            cv2.drawMarker(
                overlay, (cx, cy), (0, 200, 255), cv2.MARKER_CROSS, 12, 1
            )

        if anchor:
            ax, ay = int(anchor[0] * width), int(anchor[1] * height)
            cv2.circle(overlay, (ax, ay), 5, (0, 255, 0), -1)

        faces = result.face_landmarks or []
        if faces:
            landmarks = faces[0]
            for index in PREVIEW_POINTS:
                if index < len(landmarks):
                    point = landmarks[index]
                    cv2.circle(
                        overlay,
                        (int(point.x * width), int(point.y * height)),
                        2,
                        (255, 120, 0),
                        -1,
                    )

        # Cursor reticle mirrors the normalised cursor position.
        rx = int(self._state.cursor_nx * width)
        ry = int(self._state.cursor_ny * height)
        cv2.circle(overlay, (rx, ry), 8, (0, 255, 255), 1)

        status = "TRACKING" if faces else "NO FACE"
        if self._state.paused:
            status += " | PAUSED"
        if self._state.emergency_stop:
            status += " | E-STOP"
        if self._state.cursor_control_enabled:
            status += " | CURSOR ON"
        cv2.putText(
            overlay, status, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
        )
        cv2.putText(
            overlay,
            f"{self._fps():.0f} fps | {self._inference_ms:.0f} ms",
            (8, height - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
        )

        ok, buffer = cv2.imencode(
            ".jpg", overlay, [int(cv2.IMWRITE_JPEG_QUALITY), self._settings.stream_jpeg_quality]
        )
        if ok:
            with self._lock:
                self._latest_jpeg = buffer.tobytes()
