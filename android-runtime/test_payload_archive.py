"""Prevent unsafe archive members from reaching root package extraction.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

SPEC = importlib.util.spec_from_file_location("payload_checker", Path(__file__).with_name("check-runtime-payload.py"))
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PayloadArchiveTests(unittest.TestCase):
    def scan(self, members):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.size = 4 if kind == tarfile.REGTYPE else 0
                archive.addfile(info, io.BytesIO(b"test") if info.size else None)
        output.seek(0)
        with tarfile.open(fileobj=output) as archive:
            return CHECKER.scan_archive(archive)

    def test_traversal_absolute_and_unrelated_members_are_rejected(self):
        for name in ("../etc/passwd", "/etc/passwd", "usr/bin/waydroid", CHECKER.OVERLAY + "../escape"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                self.scan([(name, tarfile.REGTYPE)])

    def test_links_and_duplicate_paths_are_rejected(self):
        name = CHECKER.OVERLAY + "system/system/lib64/libinputreader.so"
        for members in ([(name, tarfile.SYMTYPE)], [(name, tarfile.REGTYPE)] * 2):
            with self.subTest(members=members), self.assertRaisesRegex(ValueError, "Unsafe"):
                self.scan(members)

    def test_partial_runtime_cannot_pass_the_reviewed_manifest(self):
        with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
            CHECKER.validate_generation({}, {}, {CHECKER.OVERLAY + "required.so": "hash"})


if __name__ == "__main__":
    unittest.main()
