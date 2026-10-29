# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.helpers import lxc


class InputPolicyTests(unittest.TestCase):
    def test_policy_is_default_deny_and_uses_rwm(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "config_input_devices"
            lxc.write_input_cgroup_config(output)
            text = output.read_text(encoding="ascii")

        lines = text.splitlines()
        deny = lines.index("lxc.cgroup2.devices.deny = a")
        self.assertTrue(all(line.endswith(" rwm") for line in lines[deny + 1:]))
        self.assertNotIn("c 13:*", text)
        self.assertNotIn(" c 13:", text)


if __name__ == "__main__":
    unittest.main()
