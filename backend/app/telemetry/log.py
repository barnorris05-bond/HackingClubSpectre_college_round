"""Real event telemetry.

Records only events that actually happened. Nothing here synthesises numbers;
the Evaluation screen reads its measurements from this store.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RingBuffer = deque


class Telemetry:
    """Append-only JSONL event log plus an in-memory ring for the UI.

    Thread safe: the vision worker thread and the asyncio event loop both emit.
    """

    def __init__(
        self,
        path: Path,
        ring_size: int = 800,
        max_bytes: int = 5_000_000,
    ) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._ring: RingBuffer[dict[str, Any]] = deque(maxlen=ring_size)
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._seq = 0
        self._counters: Counter[str] = Counter()
        # Counted by message as well as by kind: the Evaluation screen needs to
        # distinguish click_executed from click_rejected inside kind "click".
        self._messages: Counter[str] = Counter()
        self._reasons: Counter[str] = Counter()
        # Only refused actuations, so the Evaluation screen does not mix in
        # rejection reasons from unrelated events (e.g. tracking loss).
        self._rejection_reasons: Counter[str] = Counter()
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- wiring ------------------------------------------------------------
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from the FastAPI lifespan so threads can publish."""
        self._loop = loop

    # -- recording ---------------------------------------------------------
    def emit(
        self,
        kind: str,
        message: str,
        *,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event: dict[str, Any] = {
                "id": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "epoch_ms": int(time.time() * 1000),
                "kind": kind,
                "message": message,
                "data": data or {},
            }
            if reason:
                event["reason"] = reason
                self._reasons[reason] += 1
                if kind in ("click", "scroll") and "rejected" in message:
                    self._rejection_reasons[reason] += 1
            self._counters[kind] += 1
            self._messages[message] += 1
            self._ring.append(event)
            self._append_file(event)
            self._publish(event)
        return event

    def record_duration(self, name: str, seconds: float) -> None:
        """Store a genuinely measured duration (seconds)."""
        with self._lock:
            self._durations[name].append(float(seconds))

    def record_series(self, name: str, value: float) -> None:
        """Store a sampled measurement that is not a duration (e.g. a ratio)."""
        with self._lock:
            self._durations[name].append(float(value))

    def _append_file(self, event: dict[str, Any]) -> None:
        try:
            if self._path.exists() and self._path.stat().st_size > self._max_bytes:
                rotated = self._path.with_suffix(self._path.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                self._path.rename(rotated)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            # Telemetry must never take the app down.
            pass

    def _publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or not self._queues:
            return
        for queue in list(self._queues):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

    # -- read side ---------------------------------------------------------
    def recent(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._ring)
        if kind:
            events = [e for e in events if e["kind"] == kind]
        return events[-limit:]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    def counters(self) -> dict[str, int]:
        """Counts by event kind."""
        with self._lock:
            return dict(self._counters)

    def messages(self) -> dict[str, int]:
        """Counts by event message, e.g. ``click_executed``, ``navigated``."""
        with self._lock:
            return dict(self._messages)

    def reject_reasons(self) -> dict[str, int]:
        """Counts of reasons why an actuation was refused."""
        with self._lock:
            return dict(self._rejection_reasons)

    def all_reasons(self) -> dict[str, int]:
        """Counts of every telemetry reason, across all event kinds."""
        with self._lock:
            return dict(self._reasons)

    def duration_series(self, name: str) -> list[float]:
        with self._lock:
            return list(self._durations.get(name, []))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._messages.clear()
            self._reasons.clear()
            self._rejection_reasons.clear()
            self._durations.clear()
            self._ring.clear()
            self._seq = 0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = fraction * (len(ordered) - 1)
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def describe_series(values: list[float], unit: str = "ms") -> dict[str, Any]:
    """Summary of a measured series. Returns ``count: 0`` when nothing was measured."""
    if not values:
        return {"count": 0, "unit": unit, "measured": False}
    return {
        "count": len(values),
        "unit": unit,
        "measured": True,
        "mean": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "p50": round(_percentile(values, 0.5) or 0.0, 2),
        "p95": round(_percentile(values, 0.95) or 0.0, 2),
    }
