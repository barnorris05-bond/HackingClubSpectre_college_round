"""Geometry of head-to-cursor mapping.

MediaPipe's FaceLandmarker could not be exercised against a live face in this
environment (the available camera returned a uniform frame), so the mapping is
verified as a pure function over synthetic landmarks. That covers calibration
subtraction, scale compensation, dead zone, span mapping, inversion and
clamping without needing hardware.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.vision.filters import OneEuroFilter2D, radial_dead_zone
from app.vision.tracker import map_head_to_cursor


@pytest.fixture()
def cfg() -> Settings:
    return Settings(
        dry_run=True,
        dead_zone=0.02,
        head_span_x=0.085,
        head_span_y=0.070,
        cursor_gain=1.0,
        invert_x=False,
        invert_y=False,
    )


def test_neutral_pose_maps_to_screen_centre(cfg: Settings) -> None:
    nx, ny = map_head_to_cursor((0.5, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    assert nx == pytest.approx(0.5)
    assert ny == pytest.approx(0.5)


def test_offset_inside_dead_zone_is_ignored(cfg: Settings) -> None:
    nx, ny = map_head_to_cursor((0.5, 0.51), 0.08, (0.5, 0.5), 0.08, cfg)
    assert (nx, ny) == (0.5, 0.5)


def test_full_span_reaches_right_edge(cfg: Settings) -> None:
    # Offset equal to head_span_x maps to the full right excursion.
    nx, _ = map_head_to_cursor((0.5 + 0.085, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    assert nx == pytest.approx(1.0, abs=0.02)


def test_beyond_full_span_clamps(cfg: Settings) -> None:
    nx, ny = map_head_to_cursor((0.9, 0.05), 0.08, (0.5, 0.5), 0.08, cfg)
    assert nx == 1.0
    assert ny == 0.0


def test_vertical_span(cfg: Settings) -> None:
    _, ny = map_head_to_cursor((0.5, 0.5 - 0.070), 0.08, (0.5, 0.5), 0.08, cfg)
    assert ny == pytest.approx(0.0, abs=0.02)


def test_inversion(cfg: Settings) -> None:
    cfg.invert_x = True
    nx, _ = map_head_to_cursor((0.5 + 0.06, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    assert nx < 0.5


def test_scale_compensation_keeps_gain_when_leaning_in(cfg: Settings) -> None:
    """Leaning closer (larger inter-ocular distance) must not amplify the cursor."""
    far_x, _ = map_head_to_cursor((0.56, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    near_x, _ = map_head_to_cursor((0.56, 0.5), 0.10, (0.5, 0.5), 0.08, cfg)
    assert near_x < far_x, "closer face must not push the cursor further"
    # Scale is bounded so a bad reading cannot wildly amplify movement.
    extreme_x, _ = map_head_to_cursor((0.56, 0.5), 0.20, (0.5, 0.5), 0.08, cfg)
    assert extreme_x >= 0.5


def test_calibrated_neutral_is_subtracted(cfg: Settings) -> None:
    """A non-centre neutral pose is still treated as the cursor's zero."""
    nx, ny = map_head_to_cursor((0.62, 0.38), 0.08, (0.62, 0.38), 0.08, cfg)
    assert (nx, ny) == (0.5, 0.5)


def test_gain_scales_response(cfg: Settings) -> None:
    base, _ = map_head_to_cursor((0.56, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    cfg.cursor_gain = 2.0
    boosted, _ = map_head_to_cursor((0.56, 0.5), 0.08, (0.5, 0.5), 0.08, cfg)
    assert boosted > base


def test_uncalibrated_defaults_to_identity(cfg: Settings) -> None:
    nx, ny = map_head_to_cursor((0.44, 0.56), 0.08, None, None, cfg)
    assert 0.0 <= nx <= 1.0
    assert 0.0 <= ny <= 1.0


def test_radial_dead_zone_is_continuous() -> None:
    # At the boundary the output magnitude reaches zero, so there is no jump.
    x, y, magnitude = radial_dead_zone(0.02, 0.0, 0.02)
    assert (x, y) == (0.0, 0.0)
    assert magnitude == pytest.approx(0.02)
    x, y, magnitude = radial_dead_zone(0.03, 0.0, 0.02)
    assert x == pytest.approx(0.01)
    assert y == 0.0
    assert magnitude == pytest.approx(0.03)


def test_dead_zone_preserves_direction() -> None:
    x, y, _ = radial_dead_zone(0.03, 0.03, 0.01)
    assert x == pytest.approx(y)


def test_one_euro_filter_smooths_a_step() -> None:
    smoother = OneEuroFilter2D(min_cutoff=1.0, beta=0.0, d_cutoff=1.0)
    smoother(0.0, 0.0, 0.0)
    out, _ = smoother(1.0, 0.0, 1 / 30)
    # Output must lag a sudden step rather than jumping to it in one frame.
    assert 0.0 < out < 1.0


def test_one_euro_filter_converges() -> None:
    smoother = OneEuroFilter2D(min_cutoff=1.0, beta=0.0, d_cutoff=1.0)
    smoother(0.0, 0.0, 0.0)
    t = 0.0
    for _ in range(120):
        t += 1 / 30
        out, _ = smoother(1.0, 0.0, t)
    assert out == pytest.approx(1.0, abs=0.02)


def test_one_euro_filter_tracks_fast_motion_responsively() -> None:
    """A large beta must let genuine fast movement through (no sluggish cursor)."""
    smoother = OneEuroFilter2D(min_cutoff=1.0, beta=0.05, d_cutoff=1.0)
    smoother(0.0, 0.0, 0.0)
    t = 0.0
    out = 0.0
    for _ in range(6):
        t += 1 / 30
        out, _ = smoother(1.0, 0.0, t)
    assert out > 0.3
