#!/usr/bin/env python3
"""Resolve released upstream code, ROCm artifacts, and the AMD base by digest.

This writes only build inputs. It never moves a published image tag. Missing
artifacts, changed upstream layouts, or incompatible dependencies fail the build
and leave the previous published image available.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORIES = {
    "ctranslate2": "OpenNMT/CTranslate2",
    "wyoming-faster-whisper": "OHF-Voice/wyoming-faster-whisper",
    "faster-whisper": "SYSTRAN/faster-whisper",
}
USER_AGENT = "Chreece-Whisper-rocm-upstream-resolver"
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def fetch(url, headers=None):
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if url.startswith("https://api.github.com/"):
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request_headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=60) as response:
        return response.read(), response.headers


def fetch_json(url, headers=None):
    body, _ = fetch(url, headers)
    return json.loads(body)


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected an upstream SHA256 digest")
    return value


def release_version(release):
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Only published stable releases may update latest")
    match = re.fullmatch(r"v?([0-9]+\.[0-9]+\.[0-9]+)", release["tag_name"])
    if match is None:
        raise ValueError(f"Unrecognized stable release tag: {release['tag_name']!r}")
    return match.group(1)


def release_asset(release, repository, filename):
    matches = [item for item in release["assets"] if item["name"] == filename]
    if len(matches) != 1 or matches[0].get("state") != "uploaded":
        raise ValueError(f"Expected one complete upstream asset: {filename}")
    asset = matches[0]
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise ValueError(f"Upstream did not provide a SHA256 digest for {filename}")
    prefix = f"https://github.com/{repository}/releases/download/{release['tag_name']}/"
    if not asset["browser_download_url"].startswith(prefix):
        raise ValueError("Release asset is outside the resolved upstream release")
    return {"url": asset["browser_download_url"], "sha256": sha256(digest[7:])}


def source_revision(repository, tag):
    ref = fetch_json(
        f"https://api.github.com/repos/{repository}/git/ref/tags/{quote(tag, safe='')}"
    )["object"]
    for _ in range(5):
        if ref["type"] == "commit" and re.fullmatch(r"[0-9a-f]{40}", ref["sha"]):
            return ref["sha"]
        if ref["type"] != "tag":
            break
        ref = fetch_json(
            f"https://api.github.com/repos/{repository}/git/tags/{ref['sha']}"
        )["object"]
    raise ValueError(f"Could not resolve {repository} {tag} to a commit")


def rocm_version(dockerfile):
    matches = re.findall(r"^ARG ROCM_VERSION=([0-9]+\.[0-9]+\.[0-9]+)\s*$", dockerfile, re.M)
    if len(matches) != 1:
        raise ValueError("CTranslate2 changed its ROCm version declaration; review is needed")
    return matches[0]


def audio_constraints(source):
    """Drop the PyAV cap automatically when upstream stops using removed APIs."""
    removed_keywords = {"metadata_errors", "metadata_encoding", "stream_options"}
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "av"
            and node.func.attr == "open"
            and any(keyword.arg in removed_keywords for keyword in node.keywords)
        ):
            return ["av<19"]
    return []


def pypi_wheel(project, version):
    metadata = fetch_json(f"https://pypi.org/pypi/{project}/{version}/json")
    if metadata["info"]["version"] != version:
        raise ValueError("PyPI returned a different version from the upstream release")
    filename = f"{project.replace('-', '_')}-{version}-py3-none-any.whl"
    matches = [item for item in metadata["urls"] if item["filename"] == filename and not item["yanked"]]
    if len(matches) != 1:
        raise ValueError(f"Expected a released wheel for {project} {version}")
    wheel = matches[0]
    if not wheel["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("PyPI wheel does not use the expected artifact host")
    return {"url": wheel["url"], "sha256": sha256(wheel["digests"]["sha256"])}


def resolve_base(version):
    # Ubuntu 24.04 supplies Python 3.12, which upstream tests with its ROCm wheel.
    repository = "rocm/dev-ubuntu-24.04"
    token = fetch_json(
        f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull"
    )["token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}

    def manifest(reference):
        body, response_headers = fetch(
            f"https://registry-1.docker.io/v2/{repository}/manifests/{reference}", headers
        )
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if response_headers.get("Docker-Content-Digest", digest) != digest:
            raise ValueError("AMD base manifest digest does not match its content")
        return json.loads(body), digest

    value, digest = manifest(version)
    if "manifests" in value:
        matches = [item for item in value["manifests"] if item.get("platform", {}).get("os") == "linux" and item.get("platform", {}).get("architecture") == "amd64"]
        if len(matches) != 1:
            raise ValueError("AMD base must contain exactly one Linux amd64 image")
        value, digest = manifest(matches[0]["digest"])
        if digest != matches[0]["digest"]:
            raise ValueError("AMD image index does not match its selected manifest")
    config = fetch_json(
        f"https://registry-1.docker.io/v2/{repository}/blobs/{value['config']['digest']}", headers
    )
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise ValueError("The resolved AMD image is not Linux amd64")
    return f"docker.io/{repository}:{version}@{digest}"


def resolve():
    components = {}
    releases = {}
    for project, repository in REPOSITORIES.items():
        release = fetch_json(f"https://api.github.com/repos/{repository}/releases/latest")
        version = release_version(release)
        releases[project] = release
        components[project] = {
            "repository": repository,
            "version": version,
            "tag": release["tag_name"],
            "revision": source_revision(repository, release["tag_name"]),
        }
    ct2 = components["ctranslate2"]
    ct2["archive"] = release_asset(
        releases["ctranslate2"], ct2["repository"], "rocm-python-wheels-Linux.zip"
    )
    runtime_source = f"https://raw.githubusercontent.com/{ct2['repository']}/{ct2['revision']}/docker/Dockerfile_rocm"
    runtime_text = fetch(runtime_source)[0].decode("utf-8")
    runtime_version = rocm_version(runtime_text)
    wyoming = components["wyoming-faster-whisper"]
    wyoming["wheel"] = release_asset(
        releases["wyoming-faster-whisper"], wyoming["repository"],
        f"wyoming_faster_whisper-{wyoming['version']}-py3-none-any.whl",
    )
    faster = components["faster-whisper"]
    faster["wheel"] = pypi_wheel("faster-whisper", faster["version"])
    audio_url = f"https://raw.githubusercontent.com/{faster['repository']}/{faster['revision']}/faster_whisper/audio.py"
    audio_source = fetch(audio_url)[0]
    return {
        "schema": 1,
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "components": components,
        "runtime": {
            "image": resolve_base(runtime_version),
            "rocm_version": runtime_version,
            "source": runtime_source,
            "source_sha256": hashlib.sha256(runtime_text.encode()).hexdigest(),
            "architecture": "amd64",
        },
        "constraints": audio_constraints(audio_source.decode("utf-8")),
        "audio_compatibility_source": {
            "url": audio_url,
            "sha256": hashlib.sha256(audio_source).hexdigest(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("sources.lock.json"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    lock = resolve()
    # Atomic replace: an interrupted resolution cannot leave a half-written lock.
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"base={lock['runtime']['image']}\n")
    print(json.dumps({"base": lock["runtime"]["image"], "versions": {key: value["version"] for key, value in lock["components"].items()}, "constraints": lock["constraints"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Upstream resolution failed; published images are unchanged: {error}", file=sys.stderr)
        raise SystemExit(1) from error

