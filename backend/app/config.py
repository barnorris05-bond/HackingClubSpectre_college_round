"""Runtime configuration for Handsfree.

All tunables live here so that the vision, voice, safety and browser layers read
one source of truth. Values can be overridden through ``HANDSFREE_``-prefixed
environment variables or a ``.env`` file at the repository root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> <repo root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
LOGS_DIR = PROJECT_ROOT / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANDSFREE_",
        env_file=str(PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    # --- server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000

    # --- camera capture ---------------------------------------------------
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    target_fps: int = 30
    camera_open_timeout_s: float = 8.0
    stream_fps: int = 15
    stream_jpeg_quality: int = 70

    # --- face tracking ----------------------------------------------------
    min_detection_confidence: float = 0.5
    max_faces: int = 1
    # When no landmark is seen for this long, tracking is declared lost and the
    # cursor freezes at its last valid position instead of jumping anywhere.
    tracking_loss_grace_s: float = 0.30

    # --- calibration (requirement: completable within 30 seconds) ---------
    calibration_countdown_s: float = 3.0
    calibration_sample_s: float = 2.0
    calibration_max_duration_s: float = 25.0

    # --- mapping ----------------------------------------------------------
    # Normalised landmark offset (fraction of frame width/height) that maps to a
    # full half-screen excursion.
    head_span_x: float = 0.085
    head_span_y: float = 0.070
    dead_zone: float = 0.015
    cursor_gain: float = 1.0
    invert_x: bool = False
    invert_y: bool = False

    # --- smoothing (One Euro filter) --------------------------------------
    smooth_min_cutoff: float = 1.3
    smooth_beta: float = 0.02
    smooth_d_cutoff: float = 1.0

    # --- click safety -----------------------------------------------------
    stability_window_s: float = 0.35
    stability_max_radius_px: float = 45.0
    click_cooldown_s: float = 1.2
    require_calibration_for_click: bool = True
    min_tracking_quality_for_click: float = 0.35

    # --- OS cursor control ------------------------------------------------
    # Cursor control is opt-in: even when the camera runs, nothing moves the OS
    # pointer until the user explicitly enables it (POST /api/control/cursor).
    enable_os_cursor: bool = False
    dry_run: bool = False
    move_min_interval_s: float = 1.0 / 60.0

    # --- browser agent ----------------------------------------------------
    browser_channel: str = "msedge"
    browser_headless: bool = False
    browser_timeout_ms: int = 15000
    browser_search_engine: str = "duckduckgo"

    # --- telemetry --------------------------------------------------------
    telemetry_file: str = "logs/telemetry.jsonl"
    telemetry_ring_size: int = 800
    telemetry_max_bytes: int = 5_000_000

    @property
    def telemetry_path(self) -> Path:
        candidate = Path(self.telemetry_file)
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


# Fields the Settings screen is allowed to change at runtime, with their types.
MUTABLE_SETTINGS: dict[str, type] = {
    "dead_zone": float,
    "cursor_gain": float,
    "head_span_x": float,
    "head_span_y": float,
    "invert_x": bool,
    "invert_y": bool,
    "smooth_min_cutoff": float,
    "smooth_beta": float,
    "stability_window_s": float,
    "stability_max_radius_px": float,
    "click_cooldown_s": float,
    "min_tracking_quality_for_click": float,
    "require_calibration_for_click": bool,
    "tracking_loss_grace_s": float,
    "dry_run": bool,
    "browser_headless": bool,
    "browser_channel": str,
    "stream_fps": int,
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
