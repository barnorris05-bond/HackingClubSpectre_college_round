from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None = None


class SpeechProvider:
    name = "none"

    async def availability(self) -> tuple[bool, str]:
        return False, "No speech provider configured"

    async def initialize(self) -> None:
        return None

    async def start(self) -> None:
        raise RuntimeError("Speech provider is unavailable")

    async def stop(self) -> None:
        return None

    async def transcripts(self) -> AsyncIterator[Transcript]:
        if False:
            yield Transcript("")

    async def shutdown(self) -> None:
        return None


class FakeSpeechProvider(SpeechProvider):
    name = "fake"

    def __init__(self, available: bool = True):
        self.available = available
        self.initialized = False
        self.running = False

    async def availability(self) -> tuple[bool, str]:
        return self.available, "ready" if self.available else "fake provider disabled"

    async def initialize(self) -> None:
        if not self.available:
            raise RuntimeError("fake speech provider unavailable")
        self.initialized = True

    async def start(self) -> None:
        if not self.initialized:
            raise RuntimeError("speech provider not initialized")
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def shutdown(self) -> None:
        self.running = False
        self.initialized = False

