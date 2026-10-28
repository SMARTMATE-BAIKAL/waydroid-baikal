# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import config
from tools.helpers import images


class SessionPropertyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(work=str(self.root))
        self.base = self.root / "waydroid_base.prop"
        self.output = self.root / "waydroid.prop"
        self.base.write_text("ro.hardware=waydroid\nro.sf.lcd_density=240\n")
        self.session = dict(config.session_defaults)

    def generate(self, background):
        self.session["background_start"] = background
        with patch.object(images, "which", return_value="/usr/bin/waydroid-sensord"):
            images.make_prop(self.args, self.session, str(self.output))
        return self.output.read_text().splitlines()

    def test_background_session_skips_only_the_invisible_boot_animation(self):
        before = self.base.read_bytes()
        props = self.generate("true")
        self.assertIn("debug.sf.nobootanimation=1", props)
        self.assertIn("waydroid.background_start=true", props)
        self.assertIn("ro.hardware=waydroid", props)
        self.assertIn("ro.sf.lcd_density=240", props)
        self.assertEqual(self.base.read_bytes(), before)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o644)

    def test_background_policy_replaces_conflicting_base_animation_property(self):
        self.base.write_text("ro.hardware=waydroid\ndebug.sf.nobootanimation=0\n")
        props = self.generate("true")
        animation = [
            line for line in props if line.startswith("debug.sf.nobootanimation=")
        ]
        self.assertEqual(animation, ["debug.sf.nobootanimation=1"])

    def test_foreground_session_preserves_explicit_animation_policy(self):
        for value in ("0", "1"):
            with self.subTest(value=value):
                self.base.write_text(
                    "ro.hardware=waydroid\ndebug.sf.nobootanimation=" + value + "\n"
                )
                props = self.generate("false")
                animation = [
                    line
                    for line in props
                    if line.startswith("debug.sf.nobootanimation=")
                ]
                self.assertEqual(animation, ["debug.sf.nobootanimation=" + value])

    def test_background_policy_does_not_leak_into_a_later_foreground_session(self):
        self.generate("true")
        props = self.generate("false")
        self.assertIn("waydroid.background_start=false", props)
        self.assertFalse(
            any(line.startswith("debug.sf.nobootanimation=") for line in props)
        )


if __name__ == "__main__":
    unittest.main()
