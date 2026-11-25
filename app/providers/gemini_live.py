"""Gemini Live transcription provider.

Wraps one long-lived Gemini Live session per browser connection. Audio is
forwarded as raw 16 kHz mono 16-bit PCM; text arrives through
``interim_input_transcription`` (cumulative, live) and ``input_transcription``
(finalised) events.

Design notes verified against the live API:
  * The websocket Live endpoint only exists on ``v1beta``/``v1alpha`` while the
    SDK defaults ``api_version`` to ``v1`` -> we must pin the version or the
    handshake fails with HTTP 404.
  * ``interim_input_transcription.text`` is *cumulative*, so the live view is a
    replacement, not an append.
  * After ``send_realtime_input(audio_stream_end=True)`` Gemini emits the final
    ``input_transcription`` and then ``generation_complete``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SEND_MIME = f"audio/pcm;rate={SAMPLE_RATE}"

BASE_INSTRUCTION = (
    "You are a verbatim speech-to-text engine. Transcribe everything you hear "
    "exactly as spoken, including speech that mixes Hindi and English in the same "
    "sentence (Hinglish). Write Hindi words in Devanagari script and English words "
    "in Latin script. Keep the speaker's own words and punctuation. Do not answer, "
    "do not translate, do not summarise, do not comment. Never reply to the "
    "speaker; only produce the transcription."
)

HINT_INSTRUCTIONS: dict[str, str] = {
    "hi-en": "",
    "en": " The speaker will speak English only.",
    "hi": " The speaker will speak Hindi only.",
}

# Optional hard language hints (kept loose for the mixed mode, per the brief).
HINT_LANGUAGE_CODES: dict[str, list[str]] = {
    "en": ["en-IN", "en-US"],
    "hi": ["hi-IN"],
}

StatusCallback = Callable[[str, str], Awaitable[None]]
TextCallback = Callable[[str], Awaitable[None]]


class GeminiLiveTranscriber:
    """Bridges a browser audio stream to a Gemini Live session."""

    def __init__(
        self,
        *,
        api_key: str,
        models: list[str],
        language_hint: str = "hi-en",
        on_partial: TextCallback,
        on_status: StatusCallback,
        on_error: TextCallback,
        api_version: str = "v1beta",
        attempts_per_model: int = 2,
        flush_timeout: float = 4.0,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version=api_version),
        )
        self._plan: list[str] = []
        for model in models:
            self._plan.extend([model] * max(1, attempts_per_model))
        self._language_hint = language_hint if language_hint in HINT_INSTRUCTIONS else "hi-en"
        self._on_partial = on_partial
        self._on_status = on_status
        self._on_error = on_error
        self._flush_timeout = flush_timeout

        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        self._task: asyncio.Task[None] | None = None
        self._finalized = asyncio.Event()
        self._ending = False
        self._aborted = False

        # Cumulative transcript (authoritative, replaced on each event).
        self.text = ""
        self.model_used: str | None = None

    # -- public API ------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="gemini-live")

    def send_audio(self, chunk: bytes) -> None:
        """Queue an audio chunk, dropping the oldest if we fall behind."""
        if self._aborted or self._ending or not chunk:
            return
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(chunk)

    async def finish(self) -> str:
        """Signal end-of-audio, flush the final transcript and return it."""
        self._ending = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=self._flush_timeout + 8)
        return self.text

    async def abort(self) -> None:
        self._aborted = True
        self._ending = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    # -- internals -------------------------------------------------------
    def _build_config(self) -> types.LiveConnectConfig:
        instruction = BASE_INSTRUCTION + HINT_INSTRUCTIONS.get(self._language_hint, "")
        transcription = types.AudioTranscriptionConfig()
        codes = HINT_LANGUAGE_CODES.get(self._language_hint)
        if codes:
            transcription = types.AudioTranscriptionConfig(language_codes=codes)
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.TEXT],
            system_instruction=types.Content(parts=[types.Part(text=instruction)]),
            input_audio_transcription=transcription,
        )

    async def _run(self) -> None:
        last_error: Exception | None = None
        for attempt, model in enumerate(self._plan):
            if self._aborted or self._ending:
                return
            if attempt:
                await self._on_status("reconnecting", "Reconnecting to Gemini…")
                await asyncio.sleep(min(1.0 * attempt, 3.0))
            try:
                await self._on_status("connecting", f"Connecting to {model}…")
                await self._stream_once(model)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surface any connection error
                last_error = exc
                logger.warning("Gemini Live session failed on %s: %s", model, exc)
                if _looks_like_quota_error(exc):
                    await self._on_error(
                        "Gemini quota/rate limit reached. Please wait a moment and try again."
                    )
                    return
                if self._ending or self._aborted:
                    # We were shutting down; a failure here is not worth retrying.
                    return
                continue

        if last_error is not None:
            await self._on_error(f"Could not reach Gemini Live: {last_error}")

    async def _stream_once(self, model: str) -> None:
        config = self._build_config()
        async with self._client.aio.live.connect(model=model, config=config) as session:
            self.model_used = model
            self._finalized.clear()
            await self._on_status("listening", "Listening…")

            receiver = asyncio.create_task(self._receive(session), name="gemini-recv")
            try:
                while True:
                    chunk = await self._queue.get()
                    if chunk is None:
                        break
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type=SEND_MIME)
                    )

                # User stopped: ask Gemini to flush the final transcript.
                with contextlib.suppress(Exception):
                    await session.send_realtime_input(audio_stream_end=True)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._finalized.wait(), timeout=self._flush_timeout)
            except asyncio.CancelledError:
                raise
            finally:
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await receiver

    async def _receive(self, session) -> None:
        async for message in session.receive():
            content = message.server_content
            if content is not None:
                interim = content.interim_input_transcription
                if interim is not None and interim.text:
                    # Interim text is cumulative -> replace.
                    self.text = interim.text
                    await self._on_partial(self.text)

                final = content.input_transcription
                if final is not None:
                    if final.text and len(final.text) >= len(self.text):
                        self.text = final.text
                        await self._on_partial(self.text)
                    if final.finished:
                        self._finalized.set()

                if content.generation_complete:
                    self._finalized.set()

            if message.go_away is not None:
                await self._on_status(
                    "reconnecting",
                    "Gemini is ending this session; reconnecting automatically…",
                )

            if message.usage_metadata is not None:
                usage = message.usage_metadata
                logger.debug(
                    "gemini usage tokens: %s",
                    getattr(usage, "total_token_count", None),
                )


def _looks_like_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("429", "resource_exhausted", "quota", "rate limit", "exceeded")
    )
