"""End-to-end smoke test for the WebSocket transcription bridge.

Generates speech on macOS (``say``), streams it as 16 kHz mono PCM to the
running server and prints every status/partial/final/saved message. Useful for
verifying the full browser -> server -> Gemini -> database path without a mic.

Usage:
    python scripts/ws_smoke.py "नमस्ते, कल meeting में deadline है"
    python scripts/ws_smoke.py --voice Rishi "Please send the report by tomorrow"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import wave

import websockets

DEFAULT_URL = "ws://127.0.0.1:8000/ws/transcribe"
CHUNK_BYTES = 3200  # 100 ms of 16 kHz mono 16-bit PCM


def say_to_pcm(text: str, voice: str) -> bytes:
    aiff = tempfile.mktemp(suffix=".aiff")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
        check=True,
    )
    with wave.open(wav, "rb") as wf:
        return wf.readframes(wf.getnframes())


async def run(url: str, pcm: bytes, language_hint: str) -> int:
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "language_hint": language_hint}))

        saved: dict | None = None

        async def receiver() -> None:
            nonlocal saved
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "partial":
                    print(f"\r[partial] {msg['text']}", end="", flush=True)
                elif kind == "status":
                    print(f"\n[status] {msg.get('state')}: {msg.get('message')}")
                elif kind == "final":
                    print(f"\n[final] {msg['text']}")
                elif kind == "saved":
                    saved = msg["transcription"]
                    print(f"[saved] id={saved['id']} title={saved['title']!r}")
                    return
                elif kind == "error":
                    print(f"\n[error] {msg['message']}")

        recv_task = asyncio.create_task(receiver())

        for i in range(0, len(pcm), CHUNK_BYTES):
            await ws.send(pcm[i : i + CHUNK_BYTES])
            await asyncio.sleep(0.1)

        await ws.send(json.dumps({"type": "stop"}))

        try:
            await asyncio.wait_for(recv_task, timeout=30)
        except asyncio.TimeoutError:
            print("\n[timeout waiting for save]")
            recv_task.cancel()

    return 0 if saved else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="WebSocket transcription smoke test")
    parser.add_argument("text", nargs="?", default="नमस्ते, कल मीटिंग में deadline है।")
    parser.add_argument("--voice", default="Lekha")
    parser.add_argument("--lang", default="hi-en", choices=["hi-en", "en", "hi"])
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    pcm = say_to_pcm(args.text, args.voice)
    print(f"streaming {len(pcm) / 32000:.1f}s to {args.url}")
    return asyncio.run(run(args.url, pcm, args.lang))


if __name__ == "__main__":
    sys.exit(main())
