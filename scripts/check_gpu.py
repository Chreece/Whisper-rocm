#!/usr/bin/env python3
"""Check the real AMD device after deployment, without loading a second model."""

import argparse
import json
import os
import re
import subprocess


def main():
    import ctranslate2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-gfx", help="Require a native GPU architecture, e.g. gfx1100")
    args = parser.parse_args()
    if args.expect_gfx and os.environ.get("HSA_OVERRIDE_GFX_VERSION"):
        raise RuntimeError("Native GPU verification requires HSA_OVERRIDE_GFX_VERSION to be unset")
    count = ctranslate2.get_cuda_device_count()
    if count < 1:
        raise RuntimeError("No ROCm GPU visible: check /dev/kfd, /dev/dri and device permissions")
    compute = sorted(ctranslate2.get_supported_compute_types("cuda", device_index=0))
    if "float16" not in compute:
        raise RuntimeError(f"ROCm device 0 does not support float16: {compute}")
    info = subprocess.check_output(["rocminfo"], text=True, stderr=subprocess.STDOUT, timeout=30)
    architectures = sorted(set(re.findall(r"\bgfx[0-9a-f]+\b", info)))
    if args.expect_gfx and args.expect_gfx not in architectures:
        raise RuntimeError(f"Expected native {args.expect_gfx}; found {architectures}")
    print(json.dumps({"ctranslate2": ctranslate2.__version__, "gpu_count": count,
                      "compute_types": compute, "architectures": architectures}, indent=2))


if __name__ == "__main__":
    main()

