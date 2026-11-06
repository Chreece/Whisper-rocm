#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python3 scripts/resolve_upstreams.py
rocm_base=$(python3 -c 'import json; print(json.load(open("sources.lock.json"))["runtime"]["image"])')
docker build --pull --build-arg "ROCM_BASE=$rocm_base" -t whisper-rocm:local .

