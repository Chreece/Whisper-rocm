#!/usr/bin/env python3
"""Install only the resolved ROCm artifact, with checksum and ABI checks."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


def download(artifact, directory):
    digest = artifact["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("An artifact is missing a valid SHA256 digest")
    filename = Path(unquote(urlparse(artifact["url"]).path)).name
    if not filename or filename in {".", ".."}:
        raise ValueError("Invalid artifact filename")
    destination = directory / filename
    checksum = hashlib.sha256()
    request = Request(artifact["url"], headers={"User-Agent": "Chreece-Whisper-rocm-builder"})
    with urlopen(request, timeout=120) as response, destination.open("wb") as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            checksum.update(chunk)
            output.write(chunk)
    if checksum.hexdigest() != digest:
        destination.unlink()
        raise ValueError(f"SHA256 mismatch for {filename}; refusing to install")
    return destination


def select_rocm_wheel(names, version, python_version):
    abi = f"cp{python_version[0]}{python_version[1]}"
    pattern = re.compile(rf"ctranslate2-{re.escape(version)}-{abi}-{abi}-manylinux[^/]*x86_64\.whl")
    matches = [name for name in names if pattern.fullmatch(Path(name).name)]
    if len(matches) != 1:
        raise ValueError(f"Expected one official ROCm {abi} x86_64 wheel; found {len(matches)}")
    return matches[0]


def install(lock_file):
    if platform.machine() != "x86_64":
        raise RuntimeError("This ROCm image targets Linux amd64")
    lock = json.loads(lock_file.read_text())
    if lock.get("schema") != 1:
        raise ValueError("Unsupported source lock schema")
    root = lock_file.resolve().parent
    components = lock["components"]
    ct2 = components["ctranslate2"]
    with tempfile.TemporaryDirectory(prefix="whisper-rocm-") as temp:
        directory = Path(temp)
        archive_path = download(ct2["archive"], directory)
        with zipfile.ZipFile(archive_path) as archive:
            member = select_rocm_wheel(archive.namelist(), ct2["version"], sys.version_info)
            wheel = directory / Path(member).name
            # Extract only this basename: never trust archive paths for extraction.
            with archive.open(member) as source, wheel.open("wb") as output:
                shutil.copyfileobj(source, output)
        wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        artifacts = [wheel]
        for name in ("wyoming-faster-whisper", "faster-whisper"):
            artifacts.append(download(components[name]["wheel"], directory))
        constraints = root / "constraints.txt"
        constraints.write_text(
            f"ctranslate2=={ct2['version']}\n" + "".join(f"{line}\n" for line in lock["constraints"])
        )
        # The ROCm wheel is a direct requirement in this very transaction. pip
        # cannot substitute the same-version CUDA wheel from PyPI.
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--only-binary=:all:",
             "--constraint", str(constraints), "--report", str(root / "pip-report.json"),
             *(str(path) for path in artifacts)],
            check=True,
        )
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    packages = subprocess.check_output([sys.executable, "-m", "pip", "list", "--format=json"], text=True)
    (root / "installed-packages.json").write_text(packages)
    (root / "rocm-wheel.json").write_text(json.dumps({
        "filename": wheel.name,
        "sha256": wheel_digest,
        "archive_sha256": ct2["archive"]["sha256"],
        "python": platform.python_version(),
        "gpu_tested_during_build": False,
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    install(parser.parse_args().lock)

