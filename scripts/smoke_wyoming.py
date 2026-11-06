#!/usr/bin/env python3
"""Exercise the real Wyoming server and a tiny model on the CI runner's CPU."""

import asyncio
import json

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming_faster_whisper.health_check import check


async def main():
    uri = "tcp://127.0.0.1:10300"
    await asyncio.wait_for(check(uri), timeout=15)
    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Transcribe(language="en").event())
        await client.write_event(AudioStart(rate=16000, width=2, channels=1).event())
        await client.write_event(AudioChunk(rate=16000, width=2, channels=1, audio=b"\0\0" * 16000).event())
        await client.write_event(AudioStop().event())

        async def transcript():
            while True:
                event = await client.read_event()
                if event is None:
                    raise RuntimeError("Wyoming closed before returning a transcript")
                if event.type == "error":
                    raise RuntimeError(f"Wyoming returned an error: {event.data}")
                if Transcript.is_type(event.type):
                    return

        await asyncio.wait_for(transcript(), timeout=120)
    print(json.dumps({"wyoming_describe_info": "passed", "cpu_tiny_model_roundtrip": "passed",
                      "physical_gpu_tested": False}))


if __name__ == "__main__":
    asyncio.run(main())

