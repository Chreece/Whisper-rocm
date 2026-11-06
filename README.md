# Whisper ROCm

Wyoming speech recognition for Home Assistant on AMD GPUs, using the released
[Wyoming faster-whisper](https://github.com/OHF-Voice/wyoming-faster-whisper),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), and the **official
[CTranslate2 ROCm wheel](https://github.com/OpenNMT/CTranslate2/releases)**.

Image: `ghcr.io/chreece/whisper-rocm:latest` · Platform: Linux amd64.

The default is the full **large-v3** model, Greek (`el`), FP16, and beam size 2.
The RX 7900 XTX is covered by the upstream wheel's native `gfx1100` kernels.
The container includes ROCm userspace; the host supplies the AMD kernel driver
and the `/dev/kfd` and `/dev/dri` devices. No PyTorch installation is needed.

## Use

Use the included [compose.yml](compose.yml), or replace the old locally built
image in an existing stack with:

```yaml
image: ghcr.io/chreece/whisper-rocm:latest
```

Fetch all image updates with the same command every time:

```bash
docker compose pull
```

Apply downloaded images to the running containers with:

```bash
docker compose up -d
```

`pull` downloads images; Docker intentionally does not restart containers when
pulling. `up -d` recreates services whose images changed. There is no local
Whisper build step, Git checkout, version-number edit, or model download on
every update.

The server listens on port **10300**. Add a Wyoming Protocol integration in Home
Assistant using this host and port. The first start downloads the selected model
to the persistent `/data` volume; subsequent starts reuse it. Preserve an
existing stack's `whisper_data` volume and Compose project name when migrating.

All upstream CLI arguments and `WYO_WHISPER_*` variables remain supported. The
image defaults preserve `CT2_CUDA_ALLOCATOR=cub_caching` and
`GPU_MAX_HW_QUEUES=1`. The device name **`cuda` is also used by CTranslate2's ROCm
backend**; changing it to `rocm` is not required.

## Automatic upstream updates

GitHub Actions runs daily at **03:31 UTC**, on pushes to `main`, and on manual
dispatch. Each run:

1. Resolves the latest stable releases of the three upstream projects to exact
   Git commits and Python artifact checksums.
2. Reads the ROCm version from that CTranslate2 release, then resolves the
   corresponding official AMD Ubuntu 24.04 base image to its immutable digest.
3. Checks the official ROCm archive's SHA256 and selects exactly one wheel for
   the running Python ABI. That wheel is a direct pip input, so a CUDA-only
   PyPI wheel cannot silently replace it.
4. Builds a candidate and checks Python dependency consistency, HIP library
   linkage, native `gfx1100` kernel inclusion, audio decoding and the upstream
   CLIs. It then runs a real Wyoming server with a tiny model on the CI CPU and
   checks Describe/Info and a transcription round trip.
5. Publishes `latest` and a unique `build-<run>-<attempt>` tag only after those
   checks succeed. If resolution, installation or validation fails, the
   previous published `latest` is kept.

The exact `sources.lock.json`, installed package list, pip resolution report and
verification results are saved as workflow artifacts. The same provenance files
are inside each image under `/opt/whisper-rocm/`. Daily builds also refresh
compatible system and Python dependencies. GitHub scheduled jobs may be delayed;
`latest` means the newest successfully validated build, not an untested upstream
commit.

After a successful publication, the workflow also commits that exact source
lock and its resolution date back to `main`. It refuses to overwrite concurrent
changes. These useful daily provenance updates keep the repository active so
GitHub does not disable its schedule after 60 days of inactivity. The repository
`GITHUB_TOKEN` does not trigger another push workflow for its own commit.

faster-whisper 1.2.1 uses an `av.open` argument removed in PyAV 19. The resolver
inspects the exact resolved source and adds `av<19` only while the removed API
is used; the cap disappears automatically when upstream stops using it. The
audio decoding check protects against further API changes.

GitHub-hosted runners have no AMD GPU. CI verifies the image and its CPU protocol
path; it does **not** claim to validate recognition accuracy or GPU performance.

## Check the actual GPU

With the service running, this checks native device visibility and FP16 support
without loading a second Whisper model:

```bash
docker compose exec wyoming-whisper python3 /opt/whisper-rocm/check_gpu.py --expect-gfx gfx1100
```

For a different supported GPU, omit `--expect-gfx gfx1100` or supply its native
architecture. The check fails clearly if the devices are unavailable or the
expected architecture is absent; it does not substitute CPU inference.

The standard Docker health check asks the server for Wyoming Info and requires
an ASR program, rather than only testing whether a TCP port is open.

## Maintainer setup and local builds

The workflow needs `contents: write` and `packages: write`, provided by its
repository `GITHUB_TOKEN`; no personal access token is required. On a new GHCR
package, verify that its visibility is **Public** so anonymous `docker compose
pull` works. GitHub defaults newly created personal-account packages to private;
linking a public repository alone does not guarantee public package visibility.

For development only:

```bash
python3 -m unittest discover -s tests -v
bash scripts/build.sh
```

`sources.lock.json` is generated and refreshed after successful builds; it is
not maintained as a permanently pinned list.
To reproduce a published build, download its source lock from the workflow
artifact and supply its `runtime.image` as the `ROCM_BASE` build argument. The
published image digest remains the exact deployable artifact, including resolved
transitive dependencies and system packages.

Upstream references: [CTranslate2 ROCm build](https://github.com/OpenNMT/CTranslate2/blob/master/docker/Dockerfile_rocm),
[ROCm wheel build](https://github.com/OpenNMT/CTranslate2/blob/master/python/tools/prepare_build_environment_linux_rocm.sh),
[PyAV 19 release](https://github.com/PyAV-Org/PyAV/releases/tag/v19.0.0),
[Docker Compose pull](https://docs.docker.com/reference/cli/docker/compose/pull/),
[GHCR visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

