import re
from dataclasses import dataclass
from uuid import uuid4

from app.models.commands import CommandKind


def normalize_transcript(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


@dataclass(frozen=True)
class ClassifiedCommand:
    command_id: str
    kind: CommandKind
    normalized: str
    intent: str


class CommandRouter:
    def classify(self, transcript: str) -> ClassifiedCommand:
        normalized = normalize_transcript(transcript)
        command_id = str(uuid4())
        if normalized in {"click", "click click"}:
            return ClassifiedCommand(command_id, CommandKind.DIRECT_CLICK, normalized, "direct_click")
        if normalized.startswith(("click ", "scroll ", "search ", "open ", "go to ", "navigate ")):
            return ClassifiedCommand(command_id, CommandKind.BROWSER, normalized, "browser_request")
        if normalized.startswith(("dictate ", "type ")):
            return ClassifiedCommand(command_id, CommandKind.DICTATION, normalized, "dictation")
        return ClassifiedCommand(command_id, CommandKind.UNKNOWN, normalized, "unknown")

