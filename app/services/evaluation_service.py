from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass
class Metric:
    values: deque[float]
    status: str = "not_measured"


class EvaluationService:
    def __init__(self, max_samples: int = 200):
        self._metrics = {name: Metric(deque(maxlen=max_samples)) for name in (
            "recognition_latency_ms", "vision_inference_latency_ms", "tracking_update_rate_hz",
            "command_completion_ms", "blocked_action_count", "successful_action_count",
        )}
        self._starts: dict[str, float] = {}

    def start(self, operation_id: str) -> None:
        self._starts[operation_id] = monotonic()

    def finish(self, operation_id: str, metric: str) -> float | None:
        started = self._starts.pop(operation_id, None)
        if started is None or metric not in self._metrics:
            return None
        value = (monotonic() - started) * 1000
        self.add(metric, value)
        return value

    def add(self, metric: str, value: float) -> None:
        if metric not in self._metrics:
            return
        self._metrics[metric].values.append(value)
        self._metrics[metric].status = "measured"

    def increment(self, metric: str) -> None:
        if metric not in self._metrics:
            return
        current = self._metrics[metric].values[-1] if self._metrics[metric].values else 0
        self.add(metric, current + 1)

    def summary(self) -> dict[str, Any]:
        result = {}
        for name, metric in self._metrics.items():
            values = list(metric.values)
            result[name] = {
                "status": metric.status,
                "sample_count": len(values),
                "unit": "hz" if "rate" in name else ("count" if "count" in name else "ms"),
                "latest": values[-1] if values else None,
                "average": sum(values) / len(values) if values else None,
            }
        return result

