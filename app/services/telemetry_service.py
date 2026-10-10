import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.models.events import EventEnvelope, EventType


class TelemetryService:
    def __init__(self, queue_size: int = 100):
        self.queue_size = queue_size
        self._clients: set[asyncio.Queue[EventEnvelope]] = set()
        self._sequence = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def publish(self, event_type: EventType, session_id: str | None, data: dict[str, Any]) -> EventEnvelope:
        # There is no await in this critical section. The asyncio event loop
        # cannot interleave another publisher until after the snapshot is made.
        self._sequence += 1
        event = EventEnvelope(type=event_type, session_id=session_id, sequence=self._sequence, data=data)
        clients = tuple(self._clients)
        for queue in clients:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    async def subscribe(self) -> tuple[asyncio.Queue[EventEnvelope], AsyncIterator[EventEnvelope]]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self.queue_size)
        self._clients.add(queue)

        async def iterator() -> AsyncIterator[EventEnvelope]:
            try:
                while True:
                    yield await queue.get()
            finally:
                self._clients.discard(queue)

        return queue, iterator()

    async def unsubscribe(self, queue: asyncio.Queue[EventEnvelope]) -> None:
        self._clients.discard(queue)

