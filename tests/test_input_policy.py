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

    def test_existing_container_migrates_without_losing_other_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            policy = root / "config_input_devices"
            preserved = [
                "lxc.rootfs.path = /var/lib/waydroid/rootfs",
                "lxc.hook.post-stop = /dev/null",
                "lxc.mount.entry = /dev/dri/renderD128 dev/dri/renderD128 none bind 0 0",
                "lxc.cgroup2.devices.allow = c 13:73 rw",
            ]
            config.write_text("\n".join(preserved + [
                "lxc.hook.mount = /usr/local/lib/waydroid-sync-input-devices",
                "lxc.include = " + str(policy),
            ]) + "\n")
            config.chmod(0o640)
            lxc.ensure_input_config(root)
            result = config.read_text()
            self.assertTrue(all(line in result for line in preserved))
            self.assertNotIn("waydroid-sync-input-devices", result)
            self.assertEqual(result.splitlines()[-1], "lxc.include = " + str(policy))
            self.assertEqual(result.count("lxc.include = " + str(policy)), 1)
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)
            inode = config.stat().st_ino
            lxc.ensure_input_config(root)
            self.assertEqual(config.read_text(), result)
            self.assertEqual(config.stat().st_ino, inode)
            self.assertIn("devices.deny = a", policy.read_text())

    def test_existing_policy_is_replaced_before_container_start(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").write_text("lxc.rootfs.path = /rootfs\n")
            policy = root / "config_input_devices"
            policy.write_text("lxc.cgroup2.devices.allow = a\n")
            lxc.ensure_input_config(root)
            self.assertNotIn("devices.allow = a", policy.read_text())
            self.assertNotIn(" c 13:", policy.read_text())


if __name__ == "__main__":
    unittest.main()
