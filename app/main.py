"""FastAPI application entry point."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import schemas
from app.config import settings
from app.database import engine, init_models
from app.routers import transcriptions, ws

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await init_models()
        logger.info("database ready")
    except Exception as exc:  # noqa: BLE001 - don't crash the app if DB is down
        logger.warning("could not initialise database: %s", exc)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Real-time Hinglish (Hindi + English) speech-to-text with a Gemini Live "
        "backend, PostgreSQL storage and a CRUD dashboard."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transcriptions.router)
app.include_router(ws.router)


@app.get("/health", response_model=schemas.HealthResponse, tags=["health"])
async def health() -> schemas.HealthResponse:
    db_state = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_state = f"error: {exc.__class__.__name__}"

    return schemas.HealthResponse(
        status="ok" if db_state == "ok" else "degraded",
        environment=settings.environment,
        database=db_state,
        active_sessions=ws.active_sessions_count(),
        models=settings.models_in_order,
    )


@app.get("/", include_in_schema=False, response_model=None)
async def index() -> Response:
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return Response("Frontend not built.", media_type="text/plain")


# Static assets (HTML/CSS/JS/AudioWorklet).
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
