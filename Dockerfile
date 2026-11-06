# syntax=docker/dockerfile:1
# scripts/build.sh and CI resolve this to an official AMD image by digest.
ARG ROCM_BASE=rocm/dev-ubuntu-24.04:7.2.1
FROM ${ROCM_BASE}

ARG DEBIAN_FRONTEND=noninteractive
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/Chreece/Whisper-rocm" \
      org.opencontainers.image.title="Wyoming Whisper on AMD ROCm" \
      org.opencontainers.image.description="Upstream Wyoming and faster-whisper with the official CTranslate2 ROCm wheel" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"

# CTranslate2's HIP backend links HIP BLAS and HIP random, with rocBLAS and
# rocRAND underneath. No PyTorch, CUDA wheel, compiler build, or GPU is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates hip-runtime-amd hipblas hiprand rocrand rocminfo \
        libgomp1 python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:/opt/rocm/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/rocm/lib:/opt/rocm/lib/llvm/lib" \
    PYTHONUNBUFFERED="1" \
    PIP_DISABLE_PIP_VERSION_CHECK="1"

WORKDIR /opt/whisper-rocm
COPY sources.lock.json ./sources.lock.json
COPY scripts/install_locked.py scripts/verify_image.py scripts/check_gpu.py scripts/smoke_wyoming.py ./
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 install_locked.py sources.lock.json \
    && python3 verify_image.py \
    && mkdir -p /data

# CLI arguments and WYO_WHISPER_* variables remain standard upstream options.
# The model downloads once into /data. No model or host configuration is baked in.
ENV HF_HOME="/data" \
    XDG_CACHE_HOME="/data" \
    MODELSCOPE_CACHE="/data/modelscope" \
    CT2_CUDA_ALLOCATOR="cub_caching" \
    GPU_MAX_HW_QUEUES="1" \
    WYO_WHISPER_URI="tcp://0.0.0.0:10300" \
    WYO_WHISPER_MODEL="large-v3" \
    WYO_WHISPER_LANGUAGE="el" \
    WYO_WHISPER_DEVICE="cuda" \
    WYO_WHISPER_COMPUTE_TYPE="float16" \
    WYO_WHISPER_BEAM_SIZE="2" \
    WYO_WHISPER_DATA_DIR="/data" \
    WYO_WHISPER_DOWNLOAD_DIR="/data"

EXPOSE 10300
HEALTHCHECK --interval=30s --timeout=20s --start-period=5m --retries=5 \
    CMD ["python3", "-m", "wyoming_faster_whisper.health_check", "--timeout", "15"]
ENTRYPOINT ["python3", "-m", "wyoming_faster_whisper"]

