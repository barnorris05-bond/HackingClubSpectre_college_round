"""Agentic browser control through Playwright.

Verified in this environment: Playwright drives the installed Microsoft Edge
(``channel="msedge"``) with no browser download required. If Playwright or a
browser channel is unavailable the agent reports a structured blocker instead of
raising, so the rest of Handsfree keeps running.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

from app.config import Settings, settings as default_settings
from app.control.state import ControlState
from app.telemetry.log import Telemetry

_ALLOWED_SCHEMES = {"http", "https"}
_HOSTNAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}(?::\d+)?(/.*)?$")
_MAX_PAGE_ERRORS = 20


class BrowserAgent:
    def __init__(
        self,
        state: ControlState,
        telemetry: Telemetry,
        settings: Settings | None = None,
    ) -> None:
        self._state = state
        self._telemetry = telemetry
        self._settings = settings or default_settings
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        # Browser actions are serialized so the safety state is checked when
        # each action actually reaches the executor, not only when it is queued.
        self._action_lock = asyncio.Lock()
        self._errors: list[dict[str, Any]] = []
        self._title: str | None = None
        self._available, self._detail = self._probe()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _probe() -> tuple[bool, str | None]:
        try:
            import playwright.async_api  # noqa: F401
        except Exception as exc:
            return False, f"playwright not importable: {type(exc).__name__}: {exc}"
        return True, "playwright installed"

    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict[str, Any]:
        return {
            "available": self._available,
            "detail": self._detail,
            "started": self._browser is not None,
            "url": self._state.browser_url,
            "title": self._title,
            "channel": self._settings.browser_channel,
            "headless": self._settings.browser_headless,
            "errors": self._errors[-_MAX_PAGE_ERRORS:],
        }

    def check_safety(self) -> dict[str, Any] | None:
        """Centralized safety check enforcing pause and emergency stop."""
        with self._state.lock:
            if self._state.emergency_stop:
                return self._fail(
                    "emergency_stop",
                    "Emergency stop active: browser actions are blocked.",
                )
            if self._state.paused:
                return self._fail(
                    "paused",
                    "System paused: browser actions are blocked. Resume to execute.",
                )
        return None

    async def dispatch_action(self, action: str, **payload: Any) -> dict[str, Any]:
        """Serialize and gate every browser action entry point.

        Pause and emergency-stop setters update state synchronously. Actions
        already inside Playwright cannot always be cancelled safely, but every
        queued action waits here and rechecks state immediately before dispatch.
        Stopping the browser remains available while paused so the user can
        recover. Snapshots pass through the same gate because they may lazily
        start a browser when none is running.
        """
        if action == "stop":
            return await self._dispatch_unlocked(action, **payload)

        async with self._action_lock:
            safety_error = self.check_safety()
            if safety_error is not None:
                return safety_error
            return await self._dispatch_unlocked(action, **payload)

    async def _dispatch_unlocked(self, action: str, **payload: Any) -> dict[str, Any]:
        if action == "start":
            return await self.start()
        if action == "goto":
            return await self.goto(payload.get("url", ""))
        if action == "search":
            return await self.search(payload.get("query", ""))
        if action == "click_target":
            return await self.click_target(payload.get("target", ""), payload.get("role"))
        if action == "type":
            return await self.type_text(payload.get("text", ""), payload.get("selector"))
        if action == "scroll":
            return await self.scroll(payload.get("direction", "down"), payload.get("pixels"))
        if action in ("back", "forward", "reload"):
            return await self.history(action)
        tab_actions = {
            "new_tab": ("new", None),
            "next_tab": ("next", None),
            "previous_tab": ("previous", None),
            "switch_tab": ("switch", payload.get("index")),
            "close_tab": ("close", None),
        }
        if action in tab_actions:
            tab_action, index = tab_actions[action]
            return await self.tab(tab_action, index)
        if action == "snapshot":
            return await self.snapshot()
        if action == "stop":
            return await self.stop()
        return self._fail("unknown_action", action)

    # ------------------------------------------------------------------ #
    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._browser is not None:
                return self.status()
            if not self._available:
                self._state.browser_status = "unavailable"
                self._state.browser_detail = self._detail
                return self.status()

            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            attempts: list[str] = []
            launched = None
            launch_error: str | None = None

            channels = [self._settings.browser_channel]
            for fallback in ("msedge", "chrome", None):
                if fallback not in channels:
                    channels.append(fallback)

            for channel in channels:
                try:
                    kwargs: dict[str, Any] = {
                        "headless": self._settings.browser_headless,
                        "args": ["--disable-blink-features=AutomationControlled"],
                    }
                    if channel:
                        kwargs["channel"] = channel
                    launched = await self._playwright.chromium.launch(**kwargs)
                    attempts.append(f"{channel or 'bundled'}:ok")
                    self._settings.browser_channel = channel or self._settings.browser_channel
                    break
                except Exception as exc:
                    message = f"{type(exc).__name__}: {str(exc)[:180]}"
                    attempts.append(f"{channel or 'bundled'}:FAIL {message}")
                    launch_error = message

            if launched is None:
                self._state.browser_status = "error"
                self._state.browser_detail = (
                    "no usable browser channel. Tried: " + "; ".join(attempts)
                )
                self._telemetry.emit(
                    "browser",
                    "browser_launch_failed",
                    reason="browser_launch_failed",
                    data={"attempts": attempts},
                )
                return self.status()

            self._browser = launched
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800}
            )
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self._settings.browser_timeout_ms)
            self._page.on("pageerror", self._on_page_error)
            self._page.on("console", self._on_console)
            self._state.browser_status = "running"
            self._state.browser_detail = (
                f"launched {self._settings.browser_channel or 'bundled chromium'}"
                f" (headless={self._settings.browser_headless})"
            )
            self._telemetry.emit(
                "browser",
                "browser_started",
                data={
                    "channel": self._settings.browser_channel,
                    "attempts": attempts,
                    "launch_error": launch_error,
                },
            )
            return self.status()

    def _on_page_error(self, error: Any) -> None:
        entry = {"type": "pageerror", "message": str(error)[:300]}
        self._errors.append(entry)
        self._telemetry.emit("browser", "page_error", reason="page_error", data=entry)

    def _on_console(self, message: Any) -> None:
        try:
            if message.type == "error":
                entry = {"type": "console", "message": str(message.text)[:300]}
                self._errors.append(entry)
                self._telemetry.emit(
                    "browser", "console_error", reason="console_error", data=entry
                )
        except Exception:
            pass

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            for closer in (self._context, self._browser):
                try:
                    if closer is not None:
                        await closer.close()
                except Exception:
                    pass
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
            self._playwright = self._browser = self._context = self._page = None
            self._state.browser_status = "stopped"
            self._state.browser_detail = "browser closed"
            self._title = None
        self._telemetry.emit("browser", "browser_stopped")
        return self.status()

    # ------------------------------------------------------------------ #
    async def _ensure_page(self) -> Any:
        if self._page is None:
            await self.start()
        return self._page

    @staticmethod
    def normalize_url(target: str) -> str | None:
        """Accept an http(s) URL or a bare hostname; reject anything else."""
        candidate = (target or "").strip()
        if not candidate:
            return None
        if not candidate.startswith(("http://", "https://")):
            if not _HOSTNAME_RE.match(candidate.lower()):
                return None
            candidate = "https://" + candidate
        parsed = urlparse(candidate)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            return None
        return candidate

    def search_url(self, query: str, engine: str | None = None) -> str:
        engine = (engine or self._settings.browser_search_engine).lower()
        encoded = quote_plus(query)
        if engine == "google":
            return f"https://www.google.com/search?q={encoded}"
        if engine == "bing":
            return f"https://www.bing.com/search?q={encoded}"
        return f"https://duckduckgo.com/?q={encoded}"

    async def _sync_state(self, action: str) -> None:
        page = self._page
        if page is None:
            return
        try:
            self._state.browser_url = page.url
            self._title = await page.title()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    async def goto(self, target: str) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        url = self.normalize_url(target)
        if url is None:
            return self._fail("invalid_url", f"rejected target: {target!r}")
        if self._settings.browser_headless is False and self._page is None:
            await self._ensure_page()
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            return self._fail("navigation_failed", f"{type(exc).__name__}: {exc}")
        await self._sync_state("goto")
        self._telemetry.emit("browser", "navigated", data={"url": page.url, "target": target})
        return {"ok": True, "action": "goto", "url": page.url, "title": self._title}

    async def search(self, query: str, engine: str | None = None) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        result = await self.goto(self.search_url(query, engine))
        if result.get("ok"):
            result["action"] = "search"
            result["query"] = query
        return result

    async def click_target(self, target: str, role: str | None = None) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error

        strategies: list[tuple[str, Any]] = []
        if role in ("button", "link", "tab"):
            strategies.append((f"role={role}", lambda: page.get_by_role(role, name=target, exact=False)))
        strategies.append(("text", lambda: page.get_by_text(target, exact=False)))
        strategies.append(("label", lambda: page.get_by_label(target, exact=False)))
        strategies.append(("placeholder", lambda: page.get_by_placeholder(target, exact=False)))
        strategies.append(("link", lambda: page.get_by_role("link", name=target, exact=False)))

        errors: list[str] = []
        for name, factory in strategies:
            try:
                locator = factory().first
                if await locator.count() == 0:
                    errors.append(f"{name}: not found")
                    continue
                await locator.click(timeout=self._settings.browser_timeout_ms)
                await self._sync_state("click")
                self._telemetry.emit(
                    "browser",
                    "target_clicked",
                    data={"target": target, "strategy": name, "url": page.url},
                )
                return {
                    "ok": True,
                    "action": "click_target",
                    "target": target,
                    "strategy": name,
                    "url": page.url,
                }
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}")
        return self._fail("target_not_found", f"could not click {target!r}", attempts=errors)

    async def type_text(self, text: str, selector: str | None = None) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        try:
            if selector:
                await page.fill(selector, text)
            else:
                await page.keyboard.type(text)
        except Exception as exc:
            return self._fail("type_failed", f"{type(exc).__name__}: {exc}")
        self._telemetry.emit(
            "browser", "typed_text", data={"chars": len(text), "selector": selector}
        )
        return {"ok": True, "action": "type", "chars": len(text)}

    async def scroll(self, direction: str, pixels: int | None = None) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        amount = pixels or 600
        try:
            if direction == "down":
                await page.mouse.wheel(0, amount)
            elif direction == "up":
                await page.mouse.wheel(0, -amount)
            elif direction == "right":
                await page.mouse.wheel(amount, 0)
            elif direction == "left":
                await page.mouse.wheel(-amount, 0)
            elif direction == "to_top":
                await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            elif direction == "to_bottom":
                await page.evaluate(
                    "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"
                )
            else:
                return self._fail("invalid_direction", direction)
        except Exception as exc:
            return self._fail("scroll_failed", f"{type(exc).__name__}: {exc}")
        self._telemetry.emit(
            "browser", "scrolled", data={"direction": direction, "pixels": amount}
        )
        return {"ok": True, "action": "scroll", "direction": direction, "pixels": amount}

    async def history(self, action: str) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        try:
            if action == "back":
                await page.go_back()
            elif action == "forward":
                await page.go_forward()
            elif action == "reload":
                await page.reload(wait_until="domcontentloaded")
            else:
                return self._fail("invalid_action", action)
        except Exception as exc:
            return self._fail("history_failed", f"{type(exc).__name__}: {exc}")
        await self._sync_state(action)
        self._telemetry.emit("browser", f"history_{action}", data={"url": page.url})
        return {"ok": True, "action": action, "url": page.url, "title": self._title}

    async def tab(self, action: str, index: int | None = None) -> dict[str, Any]:
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        page = await self._ensure_page()
        context = self._context
        if page is None or context is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        safety_error = self.check_safety()
        if safety_error is not None:
            return safety_error
        try:
            if action == "new":
                new_page = await context.new_page()
                new_page.set_default_timeout(self._settings.browser_timeout_ms)
                new_page.on("pageerror", self._on_page_error)
                new_page.on("console", self._on_console)
                self._page = new_page
                await new_page.goto("about:blank")
            elif action in ("next", "previous", "switch"):
                pages = context.pages
                if not pages:
                    return self._fail("no_tabs", "")
                current = pages.index(page) if page in pages else 0
                if action == "next":
                    target_index = (current + 1) % len(pages)
                elif action == "previous":
                    target_index = (current - 1) % len(pages)
                else:
                    target_index = (index or 1) - 1
                    if target_index < 0 or target_index >= len(pages):
                        return self._fail(
                            "tab_index_out_of_range",
                            f"only {len(pages)} tab(s) open",
                        )
                self._page = pages[target_index]
                await self._page.bring_to_front()
            elif action == "close":
                pages = context.pages
                await page.close()
                remaining = context.pages
                self._page = remaining[-1] if remaining else await context.new_page()
            else:
                return self._fail("invalid_action", action)
        except Exception as exc:
            return self._fail("tab_failed", f"{type(exc).__name__}: {exc}")
        await self._sync_state(f"tab_{action}")
        self._telemetry.emit(
            "browser", f"tab_{action}", data={"url": self._state.browser_url}
        )
        return {
            "ok": True,
            "action": f"tab_{action}",
            "tabs": len(context.pages),
            "url": self._state.browser_url,
        }

    async def snapshot(self, max_chars: int = 1200) -> dict[str, Any]:
        page = await self._ensure_page()
        if page is None:
            return self._fail("browser_unavailable", self._state.browser_detail or "")
        try:
            title = await page.title()
            text = await page.inner_text("body")
            links = await page.eval_on_selector_all(
                "a[href]",
                "els => els.slice(0, 25).map(e => ({text: (e.innerText||'').trim().slice(0,80), href: e.href}))",
            )
            self._title = title
            self._state.browser_url = page.url
        except Exception as exc:
            return self._fail("snapshot_failed", f"{type(exc).__name__}: {exc}")
        clean = re.sub(r"\n{2,}", "\n", (text or "").strip())
        return {
            "ok": True,
            "action": "snapshot",
            "url": page.url,
            "title": title,
            "text": clean[:max_chars],
            "text_length": len(clean),
            "links": links,
        }

    async def screenshot(self) -> bytes | None:
        # A GET for a screenshot must not lazily start the browser while the
        # system is paused or emergency-stopped.
        page = self._page
        if page is None:
            return None
        try:
            return await page.screenshot(type="png")
        except Exception:
            return None

    def _fail(self, reason: str, detail: str, **extra: Any) -> dict[str, Any]:
        payload = {"ok": False, "reason": reason, "detail": detail, **extra}
        self._telemetry.emit(
            "browser", "browser_action_rejected", reason=reason, data=payload
        )
        return payload
