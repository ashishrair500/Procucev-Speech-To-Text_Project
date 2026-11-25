"""Language-matrix accuracy test.

Speaks a set of Hinglish sentences (alternating a Hindi and an English macOS
voice to imitate code-switching), streams them through the running server and
prints a Markdown table of what was said vs what was transcribed. Used to
produce the results table in the README.

Usage:
    python scripts/language_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ws_smoke import DEFAULT_URL, say_to_pcm  # noqa: E402

HINDI_VOICE = "Lekha"
ENGLISH_VOICE = "Rishi"
GAP_SECONDS = 0.5

# Each case is a list of (voice, text) segments spoken back-to-back.
CASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Intro", [(HINDI_VOICE, "नमस्ते, मेरा नाम आशीष है।"),
               (ENGLISH_VOICE, "I work as a software engineer.")]),
    ("Meeting", [(HINDI_VOICE, "कल client meeting में"),
                 (ENGLISH_VOICE, "we need to discuss the project deadline.")]),
    ("Hardware", [(HINDI_VOICE, "मुझे एक नया laptop चाहिए"),
                  (ENGLISH_VOICE, "because the current one is too slow.")]),
    ("Request", [(ENGLISH_VOICE, "Please send me the report"),
                 (HINDI_VOICE, "आज शाम तक, धन्यवाद।")]),
    ("Deploy", [(HINDI_VOICE, "हमारी team ने"),
                (ENGLISH_VOICE, "deployed the new feature"),
                (HINDI_VOICE, "पिछले हफ़्ते।")]),
    ("Risk", [(HINDI_VOICE, "Budget बढ़ाना पड़ेगा"),
              (ENGLISH_VOICE, "otherwise the timeline will slip.")]),
]


def silence(seconds: float) -> bytes:
    return b"\x00" * int(16000 * 2 * seconds)


def build_pcm(segments: list[tuple[str, str]]) -> bytes:
    pcm = b""
    for voice, text in segments:
        pcm += say_to_pcm(text, voice)
        pcm += silence(GAP_SECONDS)
    return pcm


async def transcribe(url: str, pcm: bytes, language_hint: str = "hi-en") -> str:
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "language_hint": language_hint}))

        final_text = {"value": ""}

        async def receiver():
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "final":
                    final_text["value"] = msg["text"]
                elif msg.get("type") == "saved":
                    return

        recv = asyncio.create_task(receiver())
        for i in range(0, len(pcm), 3200):
            await ws.send(pcm[i : i + 3200])
            await asyncio.sleep(0.1)
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(recv, timeout=30)
        except asyncio.TimeoutError:
            recv.cancel()
        return final_text["value"]


async def main() -> None:
    print("| # | Spoken (as generated) | Transcript returned by Gemini |")
    print("|---|------------------------|-------------------------------|")
    for idx, (label, segments) in enumerate(CASES, start=1):
        spoken = " / ".join(text for _, text in segments)
        pcm = build_pcm(segments)
        try:
            text = await transcribe(DEFAULT_URL, pcm)
        except Exception as exc:  # noqa: BLE001
            text = f"ERROR: {exc}"
        print(f"| {idx} | {label}: {spoken} | {text} |", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
