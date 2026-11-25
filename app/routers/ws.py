"""WebSocket bridge: browser audio <-> Gemini Live <-> database.

Protocol
--------
Client -> server
    {"type": "start", "language_hint": "hi-en" | "en" | "hi"}
    <binary>   raw 16 kHz mono 16-bit PCM chunks
    {"type": "stop"}
    {"type": "ping"}

Server -> client
    {"type": "status", "state": "...", "message": "..."}
    {"type": "partial", "text": "..."}          live, cumulative transcript
    {"type": "final", "text": "..."}            text at stop
    {"type": "saved", "transcription": {...}}   row persisted to the DB
    {"type": "error", "message": "..."}
    {"type": "pong"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import crud, schemas
from app.config import settings
from app.database import SessionLocal
from app.providers import GeminiLiveTranscriber

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_SESSIONS = settings.max_concurrent_sessions
_active_sessions = 0
_session_lock = asyncio.Lock()


def _origin_allowed(origin: str | None) -> bool:
    allowed = settings.origins_list
    if not allowed or allowed == ["*"]:
        return True
    if origin is None:
        return True  # same-origin/non-browser clients
    return origin in allowed


@router.websocket("/ws/transcribe")
async def transcribe_socket(websocket: WebSocket) -> None:
    global _active_sessions

    origin = websocket.headers.get("origin")
    if not _origin_allowed(origin):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()

    out: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    send_lock = asyncio.Lock()

    async def emit(payload: dict[str, Any]) -> None:
        await out.put(payload)

    async def writer() -> None:
        while True:
            payload = await out.get()
            if payload is None:
                return
            try:
                async with send_lock:
                    await websocket.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                return

    async def status(state: str, message: str = "") -> None:
        await emit({"type": "status", "state": state, "message": message})

    async def partial(text: str) -> None:
        await emit({"type": "partial", "text": text})

    async def error(message: str) -> None:
        await emit({"type": "error", "message": message})

    writer_task = asyncio.create_task(writer(), name="ws-writer")

    language_hint = "hi-en"
    transcriber: GeminiLiveTranscriber | None = None
    started_at: float | None = None
    stop_lock = asyncio.Lock()
    watchdog: asyncio.Task[None] | None = None
    session_counted = False

    async def finalize() -> None:
        """Stop recording, persist the transcript and notify the client."""
        global _active_sessions
        nonlocal transcriber, started_at, session_counted
        async with stop_lock:
            if transcriber is None:
                return
            local = transcriber
            transcriber = None
            if watchdog and not watchdog.done():
                watchdog.cancel()

            duration = int(time.time() - started_at) if started_at else None
            started_at = None

            await status("saving", "Finishing transcript…")
            try:
                text = (await local.finish()).strip()
            except Exception:  # noqa: BLE001
                logger.exception("error while flushing transcript")
                text = local.text.strip()
            finally:
                with contextlib.suppress(Exception):
                    await local.abort()

            limit = settings.max_transcript_chars
            if len(text) > limit:
                text = text[:limit].rstrip()

            if session_counted:
                async with _session_lock:
                    _active_sessions = max(0, _active_sessions - 1)
                session_counted = False

            if not text:
                await emit({"type": "final", "text": ""})
                await status("idle", "No speech detected — nothing saved.")
                return

            await emit({"type": "final", "text": text})
            try:
                async with SessionLocal() as db:
                    row = await crud.create_transcription(
                        db,
                        schemas.TranscriptionCreate(
                            text=text,
                            language_hint=language_hint,
                            duration_seconds=duration,
                            source="gemini-live",
                        ),
                    )
                    payload = schemas.TranscriptionRead.model_validate(row).model_dump(
                        mode="json"
                    )
                await emit({"type": "saved", "transcription": payload})
                await status("idle", "Saved.")
            except Exception as exc:  # noqa: BLE001
                logger.exception("failed to save transcription")
                await error(f"Could not save transcription: {exc}")

    async def start_session() -> None:
        global _active_sessions
        nonlocal transcriber, started_at, session_counted, watchdog
        async with stop_lock:
            if transcriber is not None:
                return
            if not settings.gemini_api_key:
                await error("Server is missing GEMINI_API_KEY.")
                return
            async with _session_lock:
                if _active_sessions >= _MAX_SESSIONS:
                    await error(
                        "Server is at its concurrent recording limit. Please try again shortly."
                    )
                    return
                _active_sessions += 1
                session_counted = True

            started_at = time.time()
            transcriber = GeminiLiveTranscriber(
                api_key=settings.gemini_api_key,
                models=settings.models_in_order,
                language_hint=language_hint,
                on_partial=partial,
                on_status=status,
                on_error=error,
            )
            transcriber.start()
            watchdog = asyncio.create_task(_watchdog(settings.max_session_seconds))

    async def _watchdog(limit: int) -> None:
        await asyncio.sleep(limit)
        await status("stopping", f"Maximum recording length ({limit}s) reached.")
        await finalize()

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            text_payload = message.get("text")
            audio_payload = message.get("bytes")

            if audio_payload is not None:
                if transcriber is not None:
                    transcriber.send_audio(audio_payload)
                continue

            if text_payload is None:
                continue

            try:
                control = json.loads(text_payload)
            except json.JSONDecodeError:
                await error("Malformed control message.")
                continue

            command = control.get("type")
            if command == "start":
                hint = str(control.get("language_hint", "hi-en"))
                language_hint = hint if hint in {"hi-en", "en", "hi"} else "hi-en"
                await start_session()
            elif command == "stop":
                await finalize()
            elif command == "ping":
                await emit({"type": "pong"})
            else:
                await error(f"Unknown command: {command}")

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("websocket handler crashed")
    finally:
        if watchdog and not watchdog.done():
            watchdog.cancel()
        if transcriber is not None:
            with contextlib.suppress(Exception):
                await transcriber.abort()
            if session_counted:
                async with _session_lock:
                    _active_sessions = max(0, _active_sessions - 1)
        await out.put(None)
        with contextlib.suppress(Exception):
            await writer_task


def active_sessions_count() -> int:
    """Current number of in-flight recording sessions (used by /health)."""
    return _active_sessions
