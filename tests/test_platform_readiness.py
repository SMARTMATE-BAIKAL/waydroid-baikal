# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.interfaces import IPlatform
from tools.services import user_manager


class PlatformReplyTests(unittest.TestCase):
    def setUp(self):
        self.platform = object.__new__(IPlatform.IPlatform)
        self.platform.client = Mock()

    def set_reply(self, integers):
        reply = Mock()
        reply.init_reader.return_value.read_int32.side_effect = integers
        self.platform.client.transact_sync_reply.return_value = reply, 0

    def test_successful_empty_app_list_is_valid(self):
        self.set_reply([(True, 0), (True, 0)])
        self.assertEqual(self.platform.getAppsInfo(strict=True), [])

    def test_transport_error_is_not_an_empty_app_list(self):
        self.platform.client.transact_sync_reply.return_value = None, -1
        with self.assertRaisesRegex(RuntimeError, "Binder transaction failed"):
            self.platform.getAppsInfo(strict=True)

    def test_android_exception_is_not_an_empty_app_list(self):
        self.set_reply([(True, -1)])
        with self.assertRaisesRegex(RuntimeError, "exception -1"):
            self.platform.getAppsInfo(strict=True)

    def test_truncated_reply_is_not_an_empty_app_list(self):
        self.set_reply([(True, 0), (False, 0)])
        with self.assertRaisesRegex(RuntimeError, "truncated Binder reply"):
            self.platform.getAppsInfo(strict=True)

    def test_negative_count_is_rejected(self):
        self.set_reply([(True, 0), (True, -1)])
        with self.assertRaisesRegex(RuntimeError, "invalid application count"):
            self.platform.getAppsInfo(strict=True)

    def test_readiness_service_lookup_does_not_poll(self):
        with patch.object(IPlatform.helpers.drivers, "loadBinderNodes"), \
                patch.object(IPlatform.gbinder, "ServiceManager") as factory, \
                patch.object(IPlatform.time, "sleep") as sleep:
            factory.return_value.is_present.return_value = True
            factory.return_value.get_service_sync.return_value = None, -1
            args = SimpleNamespace(BINDER_DRIVER="binder", SERVICE_MANAGER_PROTOCOL="aidl", BINDER_PROTOCOL="aidl")
            self.assertIsNone(IPlatform.get_service(args, wait=False))
        sleep.assert_not_called()


class UserReadinessTests(unittest.TestCase):
    def test_rpc_failure_preserves_launchers_and_does_not_report_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apps = root / "applications"
            apps.mkdir()
            desktop = apps / "waydroid.example.desktop"
            original = "[Desktop Entry]\nName=Preserved application\nExec=waydroid app launch example\n"
            desktop.write_text(original)
            state_dir = root / "state"
            state_dir.mkdir()
            args = SimpleNamespace()
            session = {"xdg_data_home": str(root), "waydroid_user_state": str(state_dir), "waydroid_data": str(root / "data")}
            ready, failed = Mock(), Mock()
            platform = Mock()
            platform.getAppsInfo.side_effect = RuntimeError("Binder transaction failed")

            def add_service(args, unlocked, changed):
                unlocked(0)
                user_manager.stopping = True

            with patch.object(user_manager.IPlatform, "get_service", return_value=platform), \
                    patch.object(user_manager.IUserMonitor, "add_service", side_effect=add_service):
                user_manager.start(args, session, ready, failed)
                args.user_manager.join(timeout=2)
            self.assertFalse(args.user_manager.is_alive())
            self.assertEqual(desktop.read_text(), original)
            ready.assert_not_called()
            failed.assert_called_once_with("Binder transaction failed")
            platform.getAppsInfo.assert_called_once_with(strict=True)


if __name__ == "__main__":
    unittest.main()
