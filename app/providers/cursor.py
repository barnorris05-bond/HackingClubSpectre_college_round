from dataclasses import dataclass, field


class CursorAdapter:
    name = "none"

    async def move(self, x: float, y: float) -> None:
        raise RuntimeError("cursor adapter is unavailable")

    async def click(self) -> None:
        raise RuntimeError("cursor adapter is unavailable")


@dataclass
class FakeCursorAdapter(CursorAdapter):
    name: str = "fake"
    actions: list[dict] = field(default_factory=list)

    async def move(self, x: float, y: float) -> None:
        self.actions.append({"action": "move", "x": x, "y": y})

    async def click(self) -> None:
        self.actions.append({"action": "click"})

