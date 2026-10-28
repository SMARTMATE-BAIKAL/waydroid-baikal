# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import config, helpers
from tools.actions import container_manager
from tools.helpers import images, lxc


class ImagePreparationTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(work="/test-waydroid")
        self.images_dir = "/test-waydroid/images"
        self.cfg = {"waydroid": {"mount_overlays": "True", "images_path": self.images_dir}}

    def test_session_reuses_prepared_images_and_binds_its_own_properties(self):
        session = {"user_id": "1001"}
        with (
            patch.object(lxc, "status", return_value="STOPPED") as status,
            patch.object(images, "_mount_rootfs_images") as mount_images,
            patch.object(helpers.mount, "ismount", return_value=True),
            patch.object(images, "make_prop") as make_prop,
            patch.object(helpers.mount, "bind_file") as bind_file,
        ):
            images.prepare_rootfs(self.args, self.images_dir)
            make_prop.assert_not_called()
            bind_file.assert_not_called()
            images.mount_rootfs(self.args, self.images_dir, session)
            mount_images.assert_called_once_with(self.args, self.images_dir)
            status.assert_called_once_with(self.args, strict=True)
            make_prop.assert_called_once_with(self.args, session, "/test-waydroid/waydroid.prop")
            bind_file.assert_called_once()

    def test_running_container_is_never_remounted(self):
        with (
            patch.object(lxc, "status", return_value="RUNNING"),
            patch.object(images, "_mount_rootfs_images") as mount_images,
        ):
            with self.assertRaisesRegex(RuntimeError, "container is running"):
                images.prepare_rootfs(self.args, self.images_dir)
            mount_images.assert_not_called()

    def test_unknown_container_state_does_not_authorize_remounting(self):
        with (
            patch.object(lxc, "status", side_effect=OSError("lxc-info failed")),
            patch.object(images, "_mount_rootfs_images") as mount_images,
        ):
            with self.assertRaisesRegex(OSError, "lxc-info failed"):
                images.prepare_rootfs(self.args, self.images_dir)
            mount_images.assert_not_called()

    def test_overlay_failure_preserves_configuration_and_cleans_partial_mounts(self):
        with (
            patch.object(config, "load", return_value=self.cfg),
            patch.object(config, "save") as save,
            patch.object(lxc, "status", return_value="STOPPED"),
            patch.object(helpers.mount, "mount"),
            patch.object(helpers.mount, "mount_overlay", side_effect=RuntimeError("overlay failed")),
            patch.object(helpers.mount, "umount_all") as unmount,
        ):
            with self.assertRaisesRegex(RuntimeError, "overlay failed"):
                images.prepare_rootfs(self.args, self.images_dir)
            save.assert_not_called()
            self.assertEqual(self.cfg["waydroid"]["mount_overlays"], "True")
            self.assertIsNone(self.args.prepared_rootfs)
            unmount.assert_called_once_with(self.args, config.defaults["rootfs"])

    def test_unmount_invalidates_reuse_even_if_cleanup_fails(self):
        self.args.prepared_rootfs = self.images_dir
        with patch.object(helpers.mount, "umount_all", side_effect=RuntimeError("busy")):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                images.umount_rootfs(self.args)
        self.assertIsNone(self.args.prepared_rootfs)

    def test_strict_lxc_status_propagates_the_actual_query_failure(self):
        with patch.object(helpers.run, "user", side_effect=OSError("lxc-info failed")):
            with self.assertRaisesRegex(OSError, "lxc-info failed"):
                lxc.status(self.args, strict=True)


class HostPreparationTests(unittest.TestCase):
    def test_host_preparation_never_starts_an_android_session(self):
        args = SimpleNamespace()
        with (
            patch.object(container_manager.actions.initializer, "is_initialized", return_value=True),
            patch.object(container_manager, "prepare_drivers_once") as drivers,
            patch.object(config, "load", return_value={"waydroid": {"images_path": "/images"}}),
            patch.object(images, "prepare_rootfs") as prepare,
            patch.object(lxc, "start") as start,
            patch.object(lxc, "generate_session_lxc_config") as session_config,
        ):
            container_manager.prepare_host(args)
            drivers.assert_called_once_with(args)
            prepare.assert_called_once_with(args, "/images")
            start.assert_not_called()
            session_config.assert_not_called()

    def test_uninitialized_install_keeps_the_initializer_available(self):
        with (
            patch.object(container_manager.actions.initializer, "is_initialized", return_value=False),
            patch.object(container_manager, "prepare_drivers_once") as drivers,
            patch.object(images, "prepare_rootfs") as prepare,
        ):
            container_manager.prepare_host(SimpleNamespace())
            drivers.assert_not_called()
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
