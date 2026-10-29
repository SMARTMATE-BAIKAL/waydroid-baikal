"""Reject partial or stale native input generations before installation.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from native_validation import native_artifact_hashes, require_native_verification
from runtime_validation import capture_provenance, file_digest


class NativeGenerationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.candidate = Path(self.directory.name)
        self.manifest = {"results": {}}
        for arch in ("arm", "arm64"):
            path = self.candidate / arch / "libinputreader.so"
            path.parent.mkdir()
            path.write_bytes(("reviewed input " + arch).encode())
            self.manifest["results"][arch] = {
                "artifact": arch + "/libinputreader.so", "sha256": file_digest(path),
            }
        path = self.candidate / "native-input-manifest.json"
        path.write_text(json.dumps(self.manifest))
        self.verification = {
            "provenance": capture_provenance(
                self.candidate, {path.name: file_digest(path)},
                native_artifact_hashes(self.manifest),
            ),
            "hwc_sha256": "reviewed-hwc",
            "input_init_sha256": "reviewed-init",
            "results": {arch: {"returncode": 0} for arch in ("arm", "arm64")},
        }

    def check(self):
        return require_native_verification(self.candidate, self.verification, "reviewed-hwc", "reviewed-init")

    def test_matched_native_generation(self):
        self.assertEqual(self.check(), self.manifest)

    def test_rebuilt_library_rejects_old_verification(self):
        (self.candidate / "arm/libinputreader.so").write_bytes(b"rebuilt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.check()

    def test_changed_manifest_rejects_old_verification(self):
        path = self.candidate / "native-input-manifest.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.check()

    def test_partial_android_abi_is_rejected(self):
        del self.manifest["results"]["arm"]
        with self.assertRaisesRegex(ValueError, "Both Android input ABIs"):
            native_artifact_hashes(self.manifest)

    def test_path_escape_is_rejected(self):
        self.manifest["results"]["arm"]["artifact"] = "../libinputreader.so"
        with self.assertRaisesRegex(ValueError, "artifact path"):
            native_artifact_hashes(self.manifest)

    def test_unmatched_hwc_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HWC differs"):
            require_native_verification(self.candidate, self.verification, "another-hwc", "reviewed-init")

    def test_unmatched_android_init_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Android init differs"):
            require_native_verification(self.candidate, self.verification, "reviewed-hwc", "another-init")

    def test_missing_or_failed_loader_check_is_rejected(self):
        for results in ({}, {"arm": {"returncode": 1}, "arm64": {"returncode": 0}}):
            with self.subTest(results=results):
                report = copy.deepcopy(self.verification)
                report["results"] = results
                with self.assertRaisesRegex(ValueError, "loader verification is missing"):
                    require_native_verification(self.candidate, report, "reviewed-hwc", "reviewed-init")


if __name__ == "__main__":
    unittest.main()
