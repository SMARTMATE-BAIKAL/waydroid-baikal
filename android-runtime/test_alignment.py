"""Payload-preservation tests for the build-time archive aligner."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "boot_extension_builder", Path(__file__).with_name("build-boot-extension.py")
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ArchiveAlignmentTest(unittest.TestCase):
    def test_preserves_mixed_payloads_metadata_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.jar"
            output = Path(directory) / "aligned.jar"
            with zipfile.ZipFile(source, "w") as archive:
                archive.comment = b"Keep the original archive comment"
                for index, name in enumerate(
                    (
                        "classes2.dex",
                        "classes.dex",
                        "classes4.dex",
                        "classes3.dex",
                        "ресурс",
                    )
                ):
                    info = zipfile.ZipInfo(name, (2026, 10, 28, 0, 0, 0))
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, bytes(range(256)) * (index + 1) + b"x")
                archive.writestr(
                    "META-INF/MANIFEST.MF",
                    b"unchanged\n" * 30,
                    compress_type=zipfile.ZIP_DEFLATED,
                )
            manifest = BUILDER.align_archive(source, output)
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(
                output
            ) as aligned:
                self.assertEqual(original.comment, aligned.comment)
                for before, after in zip(original.infolist(), aligned.infolist()):
                    self.assertEqual(before.date_time, after.date_time)
                    self.assertEqual(before.external_attr, after.external_attr)
                    self.assertEqual(before.compress_type, after.compress_type)
            self.assertEqual(len(manifest), 6)


if __name__ == "__main__":
    unittest.main()
