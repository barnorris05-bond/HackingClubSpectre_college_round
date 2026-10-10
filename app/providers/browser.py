from dataclasses import dataclass, field


class BrowserAdapter:
    name = "none"

    async def availability(self) -> tuple[bool, str]:
        return False, "No browser provider configured"

    async def initialize(self) -> None:
        raise RuntimeError("browser provider is unavailable")

    async def plan(self, request: str) -> dict:
        return {"request": request, "steps": [], "provider": self.name}

    async def execute(self, plan: dict) -> dict:
        raise RuntimeError("browser provider is unavailable")

    async def shutdown(self) -> None:
        return None


@dataclass
class FakeBrowserAdapter(BrowserAdapter):
    name: str = "dry-run"
    initialized: bool = False
    executions: list[dict] = field(default_factory=list)

    async def availability(self) -> tuple[bool, str]:
        return True, "dry-run adapter; no external browser actions"

    async def initialize(self) -> None:
        self.initialized = True

    async def plan(self, request: str) -> dict:
        return {"request": request, "steps": ["dry-run: no browser side effect"], "provider": self.name}

    async def execute(self, plan: dict) -> dict:
        result = {"executed": True, "dry_run": True, "plan": plan}
        self.executions.append(result)
        return result

    async def shutdown(self) -> None:
        self.initialized = False

