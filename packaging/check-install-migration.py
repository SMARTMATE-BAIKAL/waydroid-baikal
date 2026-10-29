#!/usr/bin/env python3
"""Verify that package migration preserves the native LXC device policy.

Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""

import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("install", type=Path)
    args = parser.parse_args()
    native = (
        "lxc.hook.version = 1\n"
        "lxc.hook.mount = /usr/lib/another-package/mount-hook\n"
        "lxc.include = /var/lib/waydroid/lxc/waydroid/config_input_devices\n"
        "lxc.cgroup2.devices.deny = a\n"
    )
    legacy = "lxc.hook.mount = /usr/local/lib/waydroid-sync-input-devices\n"
    with tempfile.TemporaryDirectory(prefix="waydroid-migration-check-") as directory:
        config = Path(directory) / "config"
        policy = Path(directory) / "config_input_devices"
        policy.write_text("# Native policy\nlxc.cgroup2.devices.deny = a\n")
        for original in (native, native + legacy):
            config.write_text(original)
            for _ in range(2):
                subprocess.run(
                    ["bash", "-c", 'source "$1"; _migrate_legacy_input_config "$2"',
                     "migration-check", str(args.install.resolve()), str(config)],
                    check=True,
                )
                if config.read_text() != native:
                    raise RuntimeError("Migration changed native LXC policy or another hook")
                if policy.read_text() != "# Native policy\nlxc.cgroup2.devices.deny = a\n":
                    raise RuntimeError("Migration changed the generated device policy")
    print("Package migration: native policy preserved; legacy hook retired idempotently")


if __name__ == "__main__":
    main()
