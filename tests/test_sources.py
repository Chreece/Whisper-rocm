import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


resolver = module("resolve_upstreams")
installer = module("install_locked")


class SourceTests(unittest.TestCase):
    def test_prereleases_cannot_replace_latest(self):
        with self.assertRaises(ValueError):
            resolver.release_version({"tag_name": "v5.0.0", "prerelease": True})

    def test_release_asset_requires_digest_and_complete_upload(self):
        release = {"tag_name": "v4.8.5", "assets": [{"name": "rocm.zip", "state": "uploaded", "browser_download_url": "https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.5/rocm.zip"}]}
        with self.assertRaises(ValueError):
            resolver.release_asset(release, "OpenNMT/CTranslate2", "rocm.zip")
        release["assets"][0]["digest"] = "sha256:" + "a" * 64
        self.assertEqual(resolver.release_asset(release, "OpenNMT/CTranslate2", "rocm.zip")["sha256"], "a" * 64)
        release["assets"][0]["state"] = "new"
        with self.assertRaises(ValueError):
            resolver.release_asset(release, "OpenNMT/CTranslate2", "rocm.zip")

    def test_rocm_runtime_follows_upstream_version(self):
        self.assertEqual(resolver.rocm_version("ARG ROCM_VERSION=7.2.1\nFROM example\n"), "7.2.1")
        self.assertEqual(resolver.rocm_version("ARG ROCM_VERSION=8.0.0\n"), "8.0.0")
        with self.assertRaises(ValueError):
            resolver.rocm_version("FROM some-unrelated-runtime\n")

    def test_pyav_cap_removed_when_upstream_removes_old_api(self):
        self.assertEqual(resolver.audio_constraints('av.open(audio, metadata_errors="ignore")'), ["av<19"])
        self.assertEqual(resolver.audio_constraints('av.open(audio, mode="r")'), [])
        self.assertEqual(resolver.audio_constraints('# av.open(audio, metadata_errors="ignore")\nav.open(audio)'), [])

    def test_rocm_selection_uses_exact_python_abi_and_version(self):
        wheels = [
            "temp-linux/ctranslate2-4.8.5-cp312-cp312-manylinux_2_28_x86_64.whl",
            "temp-linux/ctranslate2-4.8.5-cp313-cp313-manylinux_2_28_x86_64.whl",
            "temp-windows/ctranslate2-4.8.5-cp312-cp312-win_amd64.whl",
        ]
        self.assertEqual(installer.select_rocm_wheel(wheels, "4.8.5", (3, 12)), wheels[0])
        with self.assertRaises(ValueError):
            installer.select_rocm_wheel(wheels, "4.8.4", (3, 12))
        with self.assertRaises(ValueError):
            installer.select_rocm_wheel(wheels + wheels[:1], "4.8.5", (3, 12))

    def test_checksum_mismatch_refuses_artifact_and_removes_it(self):
        artifact = {"url": "https://example.invalid/rocm.zip", "sha256": "a" * 64}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(installer, "urlopen", return_value=io.BytesIO(b"wrong artifact")):
                with self.assertRaises(ValueError):
                    installer.download(artifact, target)
            self.assertFalse((target / "rocm.zip").exists())
            artifact["sha256"] = hashlib.sha256(b"verified artifact").hexdigest()
            with patch.object(installer, "urlopen", return_value=io.BytesIO(b"verified artifact")):
                self.assertEqual(installer.download(artifact, target).read_bytes(), b"verified artifact")

    def test_resolution_failure_preserves_previous_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.lock.json"
            path.write_text('{"previous": "known-good"}\n')
            with patch.object(resolver, "resolve", side_effect=ValueError("missing ROCm wheel")), patch("sys.argv", ["resolve_upstreams.py", "--output", str(path)]):
                with self.assertRaises(ValueError):
                    resolver.main()
            self.assertEqual(path.read_text(), '{"previous": "known-good"}\n')


if __name__ == "__main__":
    unittest.main()

