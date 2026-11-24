"""End-to-end voice test without a microphone.

Streams 16 kHz mono PCM16 WAV files to /ws/voice in real time (40 ms frames), like the browser does,
and prints the agent events. With --barge-in, the second WAV is streamed while the agent is speaking
to exercise interruption handling.

    uv run python scripts/voice_probe.py q1.wav [q2.wav --barge-in] [--session ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave

import httpx
import websockets

FRAME_MS = 40


def load_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16k mono s16"
        return w.readframes(w.getnframes())


async def stream_audio(ws: websockets.ClientConnection, pcm: bytes, label: str) -> None:
    step = 16000 * 2 * FRAME_MS // 1000
    print(f"[client] streaming {label} ({len(pcm) / 32000:.1f}s)")
    for i in range(0, len(pcm), step):
        await ws.send(pcm[i : i + step])
        await asyncio.sleep(FRAME_MS / 1000)
    silence = b"\x00" * step  # trailing silence lets server-side VAD detect end of speech
    for _ in range(int(1200 / FRAME_MS)):
        await ws.send(silence)
        await asyncio.sleep(FRAME_MS / 1000)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--session")
    ap.add_argument("--barge-in", action="store_true")
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    sid = a.session or httpx.post(f"{a.base}/api/sessions").json()["session_id"]
    url = a.base.replace("http", "ws", 1) + f"/ws/voice?session_id={sid}"
    print("session", sid)
    t0 = time.perf_counter()
    audio_bytes = 0
    speaking = asyncio.Event()
    turns_done = 0

    async with websockets.connect(url, max_size=None, origin="http://localhost:3000") as ws:  # type: ignore[arg-type]

        async def reader() -> None:
            nonlocal audio_bytes, turns_done
            async for msg in ws:
                if isinstance(msg, bytes):
                    audio_bytes += len(msg)
                    speaking.set()
                    continue
                e = json.loads(msg)
                t = e.pop("type")
                ts = f"{time.perf_counter() - t0:6.2f}s"
                if t == "transcript" and not e["final"]:
                    continue
                if t == "trace":
                    tr = e["trace"]
                    print(f"{ts} trace: outcome={ {k: tr['outcome'].get(k) for k in ('voice_response_latency_ms', 'tools', 'interrupted')} } "
                          f"audio_in/out={tr['totals']['audio_input_tokens']}/{tr['totals']['audio_output_tokens']} cost=${tr['totals']['cost_usd']:.5f}")
                    continue
                print(f"{ts} {t}: {e}")
                if t == "turn.end":
                    turns_done += 1
                if t == "voice.state" and e["state"] == "closed":
                    return

        rtask = asyncio.create_task(reader())
        await asyncio.sleep(1.5)
        await stream_audio(ws, load_pcm(a.wavs[0]), a.wavs[0])
        if len(a.wavs) > 1:
            if a.barge_in:
                await asyncio.wait_for(speaking.wait(), 30)
                speaking.clear()
                await asyncio.sleep(1.2)  # let the agent talk for a moment, then interrupt
            else:
                while turns_done < 1:
                    await asyncio.sleep(0.2)
            await stream_audio(ws, load_pcm(a.wavs[1]), a.wavs[1] + (" [BARGE-IN]" if a.barge_in else ""))
        # keep streaming silence so the agent can finish answering
        deadline = time.perf_counter() + 25
        while time.perf_counter() < deadline and turns_done < len(a.wavs) + 1:
            await ws.send(b"\x00" * 1280)
            await asyncio.sleep(FRAME_MS / 1000)
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(rtask, 15)
    print(f"received {audio_bytes / 48000:.1f}s of agent audio")


if __name__ == "__main__":
    asyncio.run(main())
