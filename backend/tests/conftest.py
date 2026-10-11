"""Shared test fixtures.

Safety first: ``dry_run`` is turned on at import time so that no test can click
or move the real operating-system pointer, even if a safety gate wrongly lets a
click through. Any test that needs to observe actuation asserts on the decision
object and on recorded state instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, settings as global_settings  # noqa: E402

# Hard safety interlock for the whole test session.
global_settings.dry_run = True


@pytest.fixture()
def settings() -> Settings:
    """Isolated settings with the synthetic camera and no real actuation."""
    return Settings(
        dry_run=True,
        camera_index=-1,  # synthetic frame source
        telemetry_file="logs/test-telemetry.jsonl",
        browser_headless=True,
        click_cooldown_s=1.0,
        stability_max_radius_px=45.0,
        stability_window_s=0.35,
        require_calibration_for_click=True,
        min_tracking_quality_for_click=0.35,
    )


@pytest.fixture()
def runtime(settings: Settings):
    from app.control.runtime import Runtime

    return Runtime(settings)
