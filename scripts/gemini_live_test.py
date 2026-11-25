"""Standalone Gemini Live smoke test (build-plan step 3).

Streams a 16 kHz mono 16-bit PCM audio file to the Gemini Live API and prints
the incremental input transcription. This proves the API key, model name and
session configuration work *before* wiring up the web app.

Usage:
    python scripts/gemini_live_test.py path/to/audio.wav
    python scripts/gemini_live_test.py path/to/audio.pcm          # raw PCM
    python scripts/gemini_live_test.py --say "नमस्ते, meeting कल है"  # macOS only

Environment:
    GEMINI_API_KEY    required
    GEMINI_LIVE_MODEL default: gemini-3.5-transcribe-live
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import wave

from google import genai
from google.genai import types

# Gemini Live expects raw little-endian 16-bit mono PCM at 16 kHz.
SAMPLE_RATE = 16_000
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 3200 bytes = 100 ms

SYSTEM_INSTRUCTION = (
    "You are a verbatim speech-to-text engine. Transcribe everything you hear "
    "exactly as spoken, including mixed Hindi and English (Hinglish). Write Hindi "
    "words in Devanagari script and English words in Latin script. Do not answer, "
    "do not translate, do not summarize, do not add punctuation you did not hear. "
    "Never reply to the speaker; only transcribe."
)


def read_pcm(path: str) -> bytes:
    """Return raw 16-bit mono PCM bytes, converting WAV/AIFF if needed."""
    if path.lower().endswith(".pcm") or path.lower().endswith(".raw"):
        with open(path, "rb") as fh:
            return fh.read()

    # Everything else: let afconvert normalise it (macOS) or use the stdlib for WAV.
    try:
        with wave.open(path, "rb") as wf:
            if wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1:
                raise ValueError("needs resample")
            return wf.readframes(wf.getnframes())
    except Exception:
        pass

    out = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", path, out],
        check=True,
    )
    with wave.open(out, "rb") as wf:
        return wf.readframes(wf.getnframes())


def say_to_pcm(text: str, voice: str) -> bytes:
    """Synthesise speech on macOS and return 16 kHz mono PCM (test only)."""
    aiff = tempfile.mktemp(suffix=".aiff")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
    return read_pcm(aiff)


async def run(pcm: bytes, model: str, api_key: str) -> None:
    # The Live websocket endpoint only exists on v1beta / v1alpha, but this SDK
    # defaults api_version to "v1" -> the handshake would fail with HTTP 404.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=os.environ.get("GEMINI_API_VERSION", "v1beta")),
    )

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.TEXT],
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM_INSTRUCTION)]),
        input_audio_transcription=types.AudioTranscriptionConfig(),
    )

    print(f"→ model: {model}")
    print(f"→ audio: {len(pcm)} bytes, {len(pcm) / (SAMPLE_RATE * 2):.1f}s")
    print("-" * 60)

    transcript: list[str] = []

    async with client.aio.live.connect(model=model, config=config) as session:
        print("connected; setup complete")

        async def sender() -> None:
            for i in range(0, len(pcm), CHUNK_BYTES):
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=pcm[i : i + CHUNK_BYTES],
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )
                await asyncio.sleep(CHUNK_MS / 1000)
            # Tell Gemini the stream has ended so it flushes the final transcript.
            await session.send_realtime_input(audio_stream_end=True)

        async def receiver() -> None:
            async for message in session.receive():
                content = message.server_content
                if content and content.input_transcription and content.input_transcription.text:
                    piece = content.input_transcription.text
                    transcript.append(piece)
                    print(piece, end="", flush=True)
                if message.go_away:
                    print(f"\n[go away: {message.go_away.time_left}]")

        send_task = asyncio.create_task(sender())
        try:
            await asyncio.wait_for(receiver(), timeout=30)
        except asyncio.TimeoutError:
            print("\n[receiver timed out]")
        finally:
            await send_task

    print("\n" + "-" * 60)
    print("FULL TRANSCRIPT:")
    print("".join(transcript).strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini Live transcription smoke test")
    parser.add_argument("audio", nargs="?", help="WAV/AIFF/PCM file to transcribe")
    parser.add_argument("--say", help="macOS: synthesise this text instead of a file")
    parser.add_argument(
        "--voice",
        default="Lekha",
        help="macOS voice for --say (default Lekha, Hindi)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.5-transcribe-live"),
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: set GEMINI_API_KEY", file=sys.stderr)
        return 2

    if args.say:
        pcm = say_to_pcm(args.say, args.voice)
    elif args.audio:
        pcm = read_pcm(args.audio)
    else:
        parser.error("provide an audio file or --say TEXT")
        return 2

    asyncio.run(run(pcm, args.model, api_key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
