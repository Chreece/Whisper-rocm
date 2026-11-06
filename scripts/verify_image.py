#!/usr/bin/env python3
"""GPU-free release gate: versions, HIP linkage, gfx1100 kernels, audio and CLI."""

import importlib.metadata
import io
import json
import re
import subprocess
import sys
import wave
from pathlib import Path


def main():
    import ctranslate2
    import ctranslate2._ext
    import numpy as np
    from faster_whisper.audio import decode_audio

    root = Path(__file__).resolve().parent
    lock = json.loads((root / "sources.lock.json").read_text())
    versions = {}
    for project, source in lock["components"].items():
        version = importlib.metadata.version(project)
        if version != source["version"]:
            raise RuntimeError(f"Wrong {project}: {version} != {source['version']}")
        versions[project] = version
    linkage = subprocess.check_output(["ldd", ctranslate2._ext.__file__], text=True)
    if "not found" in linkage or "libamdhip64" not in linkage:
        raise RuntimeError(f"CTranslate2 must link the complete ROCm runtime:\n{linkage}")
    libraries = re.findall(r"libctranslate2\S*\s+=>\s+(\S+)", linkage)
    if not libraries or not any(b"gfx1100" in Path(path).read_bytes() for path in libraries):
        raise RuntimeError("The installed ROCm library lacks native gfx1100 kernels")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 1600)
    audio.seek(0)
    decoded = decode_audio(audio)
    if decoded.shape != (1600,) or not np.isfinite(decoded).all():
        raise RuntimeError("faster-whisper could not decode a PCM WAV correctly")
    for module in ("wyoming_faster_whisper", "wyoming_faster_whisper.health_check"):
        subprocess.run([sys.executable, "-m", module, "--help"], check=True, stdout=subprocess.DEVNULL, timeout=30)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"versions": versions, "rocm_linked": True, "gfx1100_kernels": True,
                      "audio_decode": "passed", "upstream_cli": "passed",
                      "physical_gpu_tested": False}, indent=2))


if __name__ == "__main__":
    main()

