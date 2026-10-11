"""Deterministic voice command classification.

Three deliberately separate paths (requirement: direct commands, dictation, and
natural-language browser requests must not be conflated):

1. **Direct commands** - a small closed set recognised by *exact* match of the
   whole normalized utterance. ``click`` is a direct click; the sentence
   "click on the sign in button" is not, because it is not in the set.
2. **Structured browser intents** - anchored regular expressions (scroll, goto,
   search, tab control, click-on-target). These never actuate the OS pointer.
3. **Dictation** - handled outside this module entirely (see ``dictation.py``);
   text spoken in dictation mode is never classified here.

The classifier is pure and synchronous so it is cheap to unit test and cannot
be influenced by timing, randomness or a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CommandKind(StrEnum):
    DIRECT_CLICK = "direct_click"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    SEARCH = "search"
    BROWSER_CLICK = "browser_click"
    TYPE_TEXT = "type_text"
    TAB = "tab"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    DICTATION = "dictation"
    UNKNOWN = "unknown"


# Whole-utterance exact matches that may actuate the OS pointer.
DIRECT_CLICK_PHRASES: frozenset[str] = frozenset(
    {
        "click",
        "click now",
        "click here",
        "single click",
        "left click",
        "leftclick",
        "left-click",
        "do a click",
        "do click",
        "tap here",
        "right click",
        "rightclick",
        "right-click",
    }
)

STOP_PHRASES = frozenset({"stop", "emergency stop", "abort", "halt", "freeze"})
PAUSE_PHRASES = frozenset({"pause", "pause tracking", "hold"})
RESUME_PHRASES = frozenset({"resume", "resume tracking", "continue", "unpause"})

_NAV_PREFIXES = (
    "open",
    "go to",
    "goto",
    "navigate to",
    "visit",
    "load",
)

_SCROLL_RE = re.compile(
    r"^(?:scroll|page|move) (?P<dir>up|down|left|right)"
    r"(?: (?:by )?(?P<amount>\d+)(?: (?:percent|%)| pixels)?)?$"
)
_SCROLL_EDGE_RE = re.compile(
    r"^scroll to (?P<edge>top|bottom)(?: of (?:the )?(?:page|document))?$"
)
_NAV_RE = re.compile(r"^(?:" + "|".join(_NAV_PREFIXES) + r") (?P<target>.+)$")
_SEARCH_RE = re.compile(r"^(?:search|google|look up|find)(?: for)? (?P<query>.+)$")
_BROWSER_CLICK_RE = re.compile(
    r"^(?:click|press|select|tap)(?: on)?(?: the)? (?P<target>.+?)"
    r"(?: (?P<role>button|link|tab|box|field|icon))?$"
)
_TYPE_RE = re.compile(r"^(?:type|enter|write|dictate)(?: out)? (?P<text>.+)$")
_TAB_RE = re.compile(r"^switch to tab (?P<index>\d+)$")
# Multi-action utterances such as "click and scroll" are refused outright.
_COMPOUND_RE = re.compile(r"\b(and then|then|and)\b")

_SIMPLE_INTENTS: dict[str, tuple[CommandKind, str]] = {
    "back": (CommandKind.NAVIGATE, "back"),
    "go back": (CommandKind.NAVIGATE, "back"),
    "forward": (CommandKind.NAVIGATE, "forward"),
    "go forward": (CommandKind.NAVIGATE, "forward"),
    "reload": (CommandKind.NAVIGATE, "reload"),
    "reload page": (CommandKind.NAVIGATE, "reload"),
    "refresh": (CommandKind.NAVIGATE, "reload"),
    "new tab": (CommandKind.TAB, "new"),
    "open new tab": (CommandKind.TAB, "new"),
    "close tab": (CommandKind.TAB, "close"),
    "close this tab": (CommandKind.TAB, "close"),
    "next tab": (CommandKind.TAB, "next"),
    "previous tab": (CommandKind.TAB, "previous"),
    "prev tab": (CommandKind.TAB, "previous"),
}


@dataclass(frozen=True)
class Command:
    raw: str
    normalized: str
    kind: CommandKind
    action: str | None = None
    target: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    match: str = "none"  # "exact" | "pattern" | "none"
    explanation: str = ""
    requires_pointer_safety: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "kind": str(self.kind),
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "match": self.match,
            "explanation": self.explanation,
            "requires_pointer_safety": self.requires_pointer_safety,
        }


def normalize(text: str) -> str:
    """Lowercase, strip punctuation noise, collapse whitespace."""
    lowered = (text or "").strip().lower()
    lowered = lowered.replace("\u2019", "'")
    # Keep characters meaningful for URLs (: / . -) and drop other punctuation.
    cleaned = re.sub(r"[^\w\s\-/:@.]", " ", lowered)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(".")


def is_direct_click_phrase(text: str) -> bool:
    """True only for a *whole-utterance* exact match."""
    return normalize(text) in DIRECT_CLICK_PHRASES


def _normalize_target_url(target: str) -> str | None:
    """Return a usable URL for a spoken target, or None if it is prose."""
    candidate = target.strip()
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://")):
        return candidate
    # A bare hostname such as "wikipedia.org" is a URL; a phrase is not.
    if "." in candidate and " " not in candidate:
        return "https://" + candidate
    return None


def _search_url(engine: str, query: str) -> str:
    from urllib.parse import quote_plus

    if engine == "google":
        return f"https://www.google.com/search?q={quote_plus(query)}"
    if engine == "bing":
        return f"https://www.bing.com/search?q={quote_plus(query)}"
    return f"https://duckduckgo.com/?q={quote_plus(query)}"


def classify(text: str, search_engine: str = "duckduckgo") -> Command:
    """Classify a single utterance. Never raises on unknown input."""
    raw = (text or "").strip()
    normalized = normalize(raw)

    if not normalized:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.UNKNOWN,
            match="none",
            explanation="empty transcript",
        )

    # 1. Whole-utterance exact matches (the only path to an OS click).
    if normalized in DIRECT_CLICK_PHRASES:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.DIRECT_CLICK,
            action="click",
            params={"button": "right" if "right" in normalized else "left"},
            match="exact",
            explanation="exact direct-click phrase",
            requires_pointer_safety=True,
        )

    if normalized in STOP_PHRASES:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.STOP,
            action="emergency_stop",
            match="exact",
            explanation="emergency stop phrase",
        )
    if normalized in PAUSE_PHRASES:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.PAUSE,
            action="pause",
            match="exact",
            explanation="pause phrase",
        )
    if normalized in RESUME_PHRASES:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.RESUME,
            action="resume",
            match="exact",
            explanation="resume phrase",
        )

    # 2. Structured patterns.
    match = _SCROLL_RE.match(normalized)
    if match:
        size = int(match.group("amount") or 0)
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.SCROLL,
            action=match.group("dir"),
            params={"pixels": size if size else None},
            match="pattern",
            explanation="scroll direction pattern",
        )

    match = _SCROLL_EDGE_RE.match(normalized)
    if match:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.SCROLL,
            action=f"to_{match.group('edge')}",
            match="pattern",
            explanation="scroll-to-edge pattern",
        )

    if normalized in _SIMPLE_INTENTS:
        kind, action = _SIMPLE_INTENTS[normalized]
        return Command(
            raw=raw,
            normalized=normalized,
            kind=kind,
            action=action,
            match="exact",
            explanation="simple navigation intent",
        )

    match = _TAB_RE.match(normalized)
    if match:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.TAB,
            action="switch",
            params={"index": int(match.group("index"))},
            match="pattern",
            explanation="tab switch pattern",
        )

    match = _SEARCH_RE.match(normalized)
    if match:
        query = match.group("query").strip()
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.SEARCH,
            action="search",
            target=query,
            params={"url": _search_url(search_engine, query)},
            match="pattern",
            explanation="search pattern",
        )

    match = _NAV_RE.match(normalized)
    if match:
        target = match.group("target").strip()
        url = _normalize_target_url(target)
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.NAVIGATE,
            action="goto",
            target=target,
            params={"url": url} if url else {},
            match="pattern",
            explanation="navigation prefix pattern",
        )

    # A compound utterance is never a single safe action, so it is not allowed to
    # become an OS click and is refused as a browser click too.
    if _COMPOUND_RE.search(normalized):
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.UNKNOWN,
            match="none",
            explanation="compound utterance: say one action at a time",
        )

    match = _BROWSER_CLICK_RE.match(normalized)
    if match:
        target = match.group("target").strip()
        if target:
            return Command(
                raw=raw,
                normalized=normalized,
                kind=CommandKind.BROWSER_CLICK,
                action="click_target",
                target=target,
                params={"role": match.group("role")},
                match="pattern",
                explanation=(
                    "click-on-target pattern (browser intent, not an OS click)"
                ),
            )

    match = _TYPE_RE.match(normalized)
    if match:
        return Command(
            raw=raw,
            normalized=normalized,
            kind=CommandKind.TYPE_TEXT,
            action="type",
            target=match.group("text").strip(),
            match="pattern",
            explanation="type pattern",
        )

    return Command(
        raw=raw,
        normalized=normalized,
        kind=CommandKind.UNKNOWN,
        match="none",
        explanation="no deterministic rule matched; treated as natural language",
    )
