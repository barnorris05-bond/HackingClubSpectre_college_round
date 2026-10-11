"""Dictation mode.

While dictation is active, recognised speech is appended to a text buffer and is
never classified as a command. Only a small explicit set of escape phrases is
honoured, so the user can always leave the mode by voice.
"""

from __future__ import annotations

import re
from typing import Any

from app.control.state import ControlState
from app.telemetry.log import Telemetry
from app.voice.commands import normalize


class DictationSession:
    ENTER_PHRASES = frozenset(
        {"start dictation", "dictation mode", "begin dictation", "start typing mode"}
    )
    EXIT_PHRASES = frozenset(
        {"stop dictation", "exit dictation", "end dictation", "stop typing"}
    )
    CLEAR_PHRASES = frozenset({"clear dictation", "clear text", "delete all text"})

    def __init__(self, state: ControlState, telemetry: Telemetry) -> None:
        self._state = state
        self._telemetry = telemetry

    # ------------------------------------------------------------------ #
    @property
    def active(self) -> bool:
        return self._state.voice_mode == "dictation"

    def enter(self, source: str = "api") -> dict[str, Any]:
        if not self.active:
            self._state.set_voice_mode("dictation", source=source)
        return self.status()

    def exit(self, source: str = "api") -> dict[str, Any]:
        if self.active:
            self._state.set_voice_mode("idle", source=source)
        return self.status()

    def append(self, text: str, source: str = "voice") -> dict[str, Any]:
        """Append dictated text. Returns the updated status."""
        appended = self._state.append_dictation(text)
        if text.strip():
            self._telemetry.emit(
                "voice",
                "dictation_appended",
                data={"chars": len(text.strip()), "source": source},
            )
        return {**self.status(), "appended": appended}

    def clear(self, source: str = "api") -> dict[str, Any]:
        self._state.set_dictation("")
        self._telemetry.emit("voice", "dictation_cleared", data={"source": source})
        return self.status()

    def set_text(self, text: str, source: str = "api") -> dict[str, Any]:
        self._state.set_dictation(text)
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "mode": self._state.voice_mode,
            "active": self.active,
            "text": self._state.dictation_text,
            "length": len(self._state.dictation_text),
        }

    # ------------------------------------------------------------------ #
    def route(self, transcript: str) -> dict[str, Any] | None:
        """Handle an utterance received while dictation is active.

        Returns a dict when the utterance was consumed by dictation (or an
        escape phrase), or ``None`` when the caller should fall back to normal
        command classification.
        """
        normalized = normalize(transcript)
        if normalized in self.EXIT_PHRASES:
            self.exit(source="voice")
            self._telemetry.emit(
                "voice", "dictation_exit_by_voice", data={"transcript": transcript}
            )
            return {"handled": True, "action": "exit", **self.status()}
        if normalized in self.CLEAR_PHRASES:
            self.clear(source="voice")
            return {"handled": True, "action": "clear", **self.status()}
        if normalized in self.ENTER_PHRASES:
            self.enter(source="voice")
            return {"handled": True, "action": "enter", **self.status()}

        if not self.active:
            return None

        text = self._clean(transcript)
        if not text:
            return {"handled": True, "action": "ignored", **self.status()}

        result = self.append(text, source="voice")
        self._telemetry.emit(
            "voice",
            "dictation_text",
            data={"text": text, "length": result["length"]},
        )
        return {"handled": True, "action": "append", **result}

    @staticmethod
    def _clean(transcript: str) -> str:
        """Strip dictation punctuation commands without altering the words."""
        text = (transcript or "").strip()
        text = re.sub(r"\s+", " ", text)
        replacements = {
            " full stop": ".",
            " comma": ",",
            " question mark": "?",
            " exclamation mark": "!",
            " new line": "\n",
            " newline": "\n",
        }
        lowered = text.lower()
        for spoken, symbol in replacements.items():
            lowered = lowered.replace(spoken, symbol)
        return lowered
