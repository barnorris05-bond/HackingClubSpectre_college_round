"""Voice classification must be deterministic and strict.

The central safety property: an OS click is only ever a *whole-utterance* exact
match. A sentence containing the word "click" must not become an OS click.
"""

from __future__ import annotations

import pytest

from app.voice.commands import (
    DIRECT_CLICK_PHRASES,
    CommandKind,
    classify,
    is_direct_click_phrase,
    normalize,
)


@pytest.mark.parametrize(
    "phrase",
    ["click", "Click", "  CLICK.  ", "left click", "left-click", "click here", "right click"],
)
def test_direct_click_exact_matches(phrase: str) -> None:
    command = classify(phrase)
    assert command.kind is CommandKind.DIRECT_CLICK
    assert command.match == "exact"
    assert command.requires_pointer_safety is True


@pytest.mark.parametrize(
    "phrase",
    [
        "click on the sign in button",
        "click the login link",
        "please click",
        "can you click here",
        "click and scroll down",
        "i said click",
        "double click",
        "clickety clack",
    ],
)
def test_click_lookalikes_are_not_os_clicks(phrase: str) -> None:
    command = classify(phrase)
    assert command.kind is not CommandKind.DIRECT_CLICK
    assert command.requires_pointer_safety is False
    assert is_direct_click_phrase(phrase) is False


def test_click_like_sentence_becomes_browser_intent() -> None:
    command = classify("click on the sign in button")
    assert command.kind is CommandKind.BROWSER_CLICK
    assert command.target == "sign in"
    assert command.match == "pattern"


def test_search_is_browser_intent() -> None:
    command = classify("search for mechanical keyboards")
    assert command.kind is CommandKind.SEARCH
    assert command.target == "mechanical keyboards"
    assert "duckduckgo.com" in str(command.params["url"])


@pytest.mark.parametrize("phrase", ["scroll down", "scroll up", "scroll down 400"])
def test_scroll_directions(phrase: str) -> None:
    command = classify(phrase)
    assert command.kind is CommandKind.SCROLL
    assert command.action in ("up", "down")


def test_scroll_pixel_amount_parsed() -> None:
    command = classify("scroll down 400")
    assert command.params["pixels"] == 400


def test_scroll_to_bottom() -> None:
    command = classify("scroll to bottom")
    assert command.kind is CommandKind.SCROLL
    assert command.action == "to_bottom"


@pytest.mark.parametrize(
    "phrase,action",
    [("back", "back"), ("go back", "back"), ("reload", "reload"), ("forward", "forward")],
)
def test_history_intents(phrase: str, action: str) -> None:
    command = classify(phrase)
    assert command.kind is CommandKind.NAVIGATE
    assert command.action == action


def test_navigation_with_bare_hostname() -> None:
    command = classify("open wikipedia.org")
    assert command.kind is CommandKind.NAVIGATE
    assert command.params["url"] == "https://wikipedia.org"


def test_navigation_without_url_has_no_url() -> None:
    command = classify("open the settings menu")
    assert command.kind is CommandKind.NAVIGATE
    assert "url" not in command.params


def test_tab_switch_index() -> None:
    command = classify("switch to tab 3")
    assert command.kind is CommandKind.TAB
    assert command.params["index"] == 3


@pytest.mark.parametrize(
    "phrase,kind",
    [("stop", CommandKind.STOP), ("pause", CommandKind.PAUSE), ("resume", CommandKind.RESUME)],
)
def test_safety_phrases(phrase: str, kind: CommandKind) -> None:
    assert classify(phrase).kind is kind


def test_type_text() -> None:
    command = classify("type hello world")
    assert command.kind is CommandKind.TYPE_TEXT
    assert command.target == "hello world"


def test_unknown_is_not_guessed() -> None:
    command = classify("what is the weather like today")
    assert command.kind is CommandKind.UNKNOWN
    assert command.match == "none"


def test_empty_transcript() -> None:
    assert classify("").kind is CommandKind.UNKNOWN
    assert classify("   ").kind is CommandKind.UNKNOWN


def test_normalize_collapses_punctuation_and_case() -> None:
    assert normalize("  Click,   NOW!! ") == "click now"
    assert normalize("\u201cclick\u201d") == "click"


def test_direct_phrase_set_is_small_and_closed() -> None:
    # Guard against accidental growth of the set that can actuate the OS.
    assert len(DIRECT_CLICK_PHRASES) <= 16
    assert all(p == normalize(p) for p in DIRECT_CLICK_PHRASES)


def test_classification_is_deterministic() -> None:
    first = classify("scroll down").to_dict()
    for _ in range(20):
        assert classify("scroll down").to_dict() == first
