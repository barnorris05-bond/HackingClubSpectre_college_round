"""Handsfree backend entry point.

Run on Windows PowerShell:

    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --app-dir backend
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.control.runtime import Runtime

DESCRIPTION = """
Backend for Handsfree, a voice and webcam browsing system.

Two independent pipelines share one safety layer:

* **Vision** - webcam -> MediaPipe face landmarks -> calibration -> dead zone ->
  One Euro smoothing -> clamped normalised cursor -> optional OS cursor control.
* **Voice** - browser speech recognition -> deterministic classification ->
  safety-gated pointer actuation, structured browser control, or dictation.

Cursor control is opt-in and always cancellable by pause or emergency stop.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = Runtime(settings)
    app.state.runtime = runtime
    await runtime.startup()
    try:
        yield
    finally:
        await runtime.shutdown()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Handsfree API",
        description=DESCRIPTION,
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.include_router(router)
    return application


app = create_app()
