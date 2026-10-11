"""Signal smoothing for head tracking.

The One Euro filter is a low-latency adaptive low-pass filter: it smooths hard
when the signal is slow (killing hand/head tremor) and backs off when the signal
moves quickly (so the cursor does not feel laggy). Reference: Casiez, Roussel &
Vogel (CHI 2012).
"""

from __future__ import annotations

import math


class _LowPass:
    def __init__(self) -> None:
        self._y: float | None = None

    def reset(self) -> None:
        self._y = None

    def __call__(self, x: float, alpha: float) -> float:
        if self._y is None:
            self._y = x
        else:
            self._y = alpha * x + (1.0 - alpha) * self._y
        return self._y


class OneEuroFilter:
    """Filter a scalar stream sampled at irregular intervals."""

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x = _LowPass()
        self._dx = _LowPass()
        self._last_raw: float | None = None
        self._last_t: float | None = None

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._last_raw = None
        self._last_t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def __call__(self, x: float, t: float) -> float:
        if self._last_t is None:
            dt = 1.0 / 30.0
        else:
            dt = max(t - self._last_t, 1e-4)
        self._last_t = t

        if self._last_raw is None:
            dx = 0.0
        else:
            dx = (x - self._last_raw) / dt
        self._last_raw = x

        dx_hat = self._dx(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        return self._x(x, self._alpha(cutoff, dt))


class OneEuroFilter2D:
    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0) -> None:
        self.fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self.fx.reset()
        self.fy.reset()

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        return self.fx(x, t), self.fy(y, t)


def radial_dead_zone(dx: float, dy: float, radius: float) -> tuple[float, float, float]:
    """Subtract a dead-zone radius from the magnitude of a 2D offset.

    Continuous at the boundary (output magnitude reaches zero smoothly), which
    avoids the cursor sticking-then-jumping that per-axis dead zones cause.
    """
    magnitude = math.hypot(dx, dy)
    if radius <= 0 or magnitude <= radius:
        return 0.0, 0.0, magnitude
    scale = (magnitude - radius) / magnitude
    return dx * scale, dy * scale, magnitude
