from dataclasses import dataclass


@dataclass(frozen=True)
class VisionSample:
    face_detected: bool
    confidence: float | None
    x: float | None = None
    y: float | None = None
    timestamp: float | None = None
    frame_width: int | None = None
    frame_height: int | None = None


class VisionProvider:
    name = "none"

    async def availability(self) -> tuple[bool, str]:
        return False, "No vision provider configured"

    async def initialize(self) -> None:
        raise RuntimeError("vision provider is unavailable")

    async def sample(self) -> VisionSample:
        raise RuntimeError("vision provider is unavailable")

    async def shutdown(self) -> None:
        return None


class FakeVisionProvider(VisionProvider):
    name = "fake"

    def __init__(self, available: bool = True):
        self.available = available
        self.initialized = False
        self.next_sample = VisionSample(False, None)

    async def availability(self) -> tuple[bool, str]:
        return self.available, "ready" if self.available else "fake provider disabled"

    async def initialize(self) -> None:
        if not self.available:
            raise RuntimeError("fake vision provider unavailable")
        self.initialized = True

    async def sample(self) -> VisionSample:
        if not self.initialized:
            raise RuntimeError("vision provider not initialized")
        return self.next_sample

    async def shutdown(self) -> None:
        self.initialized = False

