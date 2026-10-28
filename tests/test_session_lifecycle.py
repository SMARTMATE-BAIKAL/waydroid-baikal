# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""Run with WAYDROID_TEST_PRIVATE_BUS=1 dbus-run-session -- python -m unittest discover -s tests."""

import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from tools.actions import app_manager, session_manager
from tools.helpers.session import Readiness, notify
from tools.services import notification_manager


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.notified = Mock()
        self.ready = Mock()
        self.failed = Mock()
        self.state = Readiness(lambda fn, *args: self.pending.append((fn, args)), self.notified)
        self.state.wait(self.ready, self.failed)

    def flush(self):
        while self.pending:
            callback, args = self.pending.pop(0)
            callback(*args)

    def test_android_first_requires_host_completion(self):
        self.state.android_ready()
        self.flush()
        self.notified.assert_not_called()
        self.ready.assert_not_called()
        self.state.host_ready()
        self.flush()
        self.notified.assert_called_once_with()
        self.ready.assert_called_once_with()

    def test_host_first_requires_android_completion(self):
        self.state.host_ready()
        self.flush()
        self.notified.assert_not_called()
        self.state.android_ready()
        self.state.android_ready()
        self.state.host_ready()
        self.flush()
        self.notified.assert_called_once_with()
        self.ready.assert_called_once_with()

    def test_stopped_startup_never_becomes_ready(self):
        self.state.stop("initialization failed")
        self.state.android_ready()
        self.state.host_ready()
        self.flush()
        self.notified.assert_not_called()
        self.ready.assert_not_called()
        self.failed.assert_called_once_with("initialization failed")

    def test_stop_invalidates_queued_launch(self):
        self.state.android_ready()
        self.state.host_ready()
        self.state.stop("session stopped")
        self.flush()
        self.ready.assert_not_called()
        self.failed.assert_called_once_with("session stopped")

    def test_new_waiter_after_readiness(self):
        self.state.android_ready()
        self.state.host_ready()
        second = Mock()
        self.state.wait(second, self.failed)
        self.flush()
        second.assert_called_once_with()
        self.failed.assert_not_called()

    def test_notification_failure_does_not_release_clients(self):
        self.notified.side_effect = OSError("notify socket disappeared")
        self.state.android_ready()
        with self.assertRaises(OSError):
            self.state.host_ready()
        self.flush()
        self.assertFalse(self.state.ready)
        self.ready.assert_not_called()

    def test_notify_sends_native_unix_datagram(self):
        with tempfile.TemporaryDirectory() as directory:
            address = str(Path(directory) / "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(address)
                receiver.settimeout(1)
                with patch.dict(os.environ, {"NOTIFY_SOCKET": address}):
                    notify("READY=1\nSTATUS=Ready")
                self.assertEqual(receiver.recv(128), b"READY=1\nSTATUS=Ready")

    def test_notify_supports_abstract_socket(self):
        address = f"waydroid-test-{os.getpid()}"
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind("\0" + address)
            receiver.settimeout(1)
            with patch.dict(os.environ, {"NOTIFY_SOCKET": "@" + address}):
                notify("READY=1")
            self.assertEqual(receiver.recv(128), b"READY=1")


class LaunchTests(unittest.TestCase):
    def test_stopped_session_activates_native_unit_before_launch(self):
        events = []
        missing = dbus.exceptions.DBusException(
            "no session", name="org.freedesktop.DBus.Error.NameHasNoOwner")
        session = Mock()
        container = Mock()
        session.WaitForReady.side_effect = lambda **kwargs: events.append("ready")
        container.Unfreeze.side_effect = lambda: events.append("unfreeze")
        with patch.object(app_manager.tools.helpers.ipc, "DBusSessionService", side_effect=[missing, session]), \
                patch.object(app_manager.tools.helpers.ipc, "DBusContainerService", return_value=container), \
                patch.object(app_manager, "start_session", side_effect=lambda timeout: events.append("start-unit")) as activate, \
                patch.object(session_manager, "start") as unmanaged:
            app_manager.maybeLaunchLater(SimpleNamespace(timeout=30), lambda: events.append("launch"))
        self.assertEqual(events, ["start-unit", "unfreeze", "ready", "launch"])
        activate.assert_called_once_with(30)
        unmanaged.assert_not_called()

    def test_activation_failure_does_not_launch_or_start_unmanaged_session(self):
        missing = dbus.exceptions.DBusException(
            "no session", name="org.freedesktop.DBus.Error.ServiceUnknown")
        launch = Mock()
        with patch.object(app_manager.tools.helpers.ipc, "DBusSessionService", side_effect=missing), \
                patch.object(app_manager, "start_session", side_effect=RuntimeError("unit failed")), \
                patch.object(session_manager, "start") as unmanaged:
            with self.assertRaisesRegex(RuntimeError, "unit failed"):
                app_manager.maybeLaunchLater(SimpleNamespace(timeout=30), launch)
        launch.assert_not_called()
        unmanaged.assert_not_called()

    def test_bus_error_does_not_start_a_session(self):
        denied = dbus.exceptions.DBusException(
            "access denied", name="org.freedesktop.DBus.Error.AccessDenied")
        with patch.object(app_manager.tools.helpers.ipc, "DBusSessionService", side_effect=denied), \
                patch.object(app_manager, "start_session") as activate:
            with self.assertRaises(dbus.exceptions.DBusException):
                app_manager.maybeLaunchLater(SimpleNamespace(timeout=30), Mock())
        activate.assert_not_called()

    def test_app_and_intent_select_only_the_application(self):
        args = SimpleNamespace(timeout=30, PACKAGE="org.example.app", ACTION="android.intent.action.VIEW", URI="example:test")
        for action in (app_manager.launch, app_manager.intent):
            with self.subTest(action=action.__name__):
                platform = Mock()
                platform.getprop.return_value = "true"
                platform.launchIntent.return_value = args.PACKAGE
                with patch.object(app_manager, "maybeLaunchLater", side_effect=lambda args, callback: callback()), \
                        patch.object(app_manager.IPlatform, "get_service", return_value=platform):
                    action(args)
                platform.setprop.assert_called_once_with("waydroid.active_apps", args.PACKAGE)

    def test_explicit_show_full_ui_still_selects_android_desktop(self):
        platform = Mock()
        with patch.object(app_manager, "maybeLaunchLater", side_effect=lambda args, callback: callback()), \
                patch.object(app_manager.IPlatform, "get_service", return_value=platform), \
                patch.object(app_manager.IStatusBarService, "get_service", return_value=None):
            app_manager.showFullUI(SimpleNamespace(timeout=30))
        platform.setprop.assert_called_once_with("waydroid.active_apps", "Waydroid")
        platform.settingsPutString.assert_called_once_with(2, "policy_control", "null*")

    def test_existing_session_waits_before_launch(self):
        events = []
        session = Mock()
        container = Mock()
        session.WaitForReady.side_effect = lambda **kwargs: events.append("ready")
        container.Unfreeze.side_effect = lambda: events.append("unfreeze")
        with patch.object(app_manager.tools.helpers.ipc, "DBusSessionService", return_value=session), \
                patch.object(app_manager.tools.helpers.ipc, "DBusContainerService", return_value=container):
            app_manager.maybeLaunchLater(SimpleNamespace(timeout=30), lambda: events.append("launch"))
        self.assertEqual(events, ["unfreeze", "ready", "launch"])

    def test_readiness_failure_does_not_create_second_session(self):
        session = Mock()
        session.WaitForReady.side_effect = dbus.exceptions.DBusException(
            "startup failed", name="id.waydro.SessionManager.NotReady")
        launch = Mock()
        with patch.object(app_manager.tools.helpers.ipc, "DBusSessionService", return_value=session), \
                patch.object(app_manager.tools.helpers.ipc, "DBusContainerService"), \
                patch.object(session_manager, "start") as start:
            with self.assertRaises(dbus.exceptions.DBusException):
                app_manager.maybeLaunchLater(SimpleNamespace(timeout=30), launch)
        start.assert_not_called()
        launch.assert_not_called()

    def test_duplicate_start_waits_for_owner(self):
        events = []
        session = Mock()
        session.WaitForReady.side_effect = lambda **kwargs: events.append("ready")
        with patch.object(session_manager, "DbusSessionManager"), \
                patch.object(session_manager.dbus, "SessionBus"), \
                patch.object(session_manager.dbus.service, "BusName", side_effect=dbus.exceptions.NameExistsException("id.waydro.Session")), \
                patch.object(session_manager.tools.helpers.ipc, "DBusSessionService", return_value=session):
            session_manager.start(SimpleNamespace(timeout=30), lambda: events.append("launch"))
        self.assertEqual(events, ["ready", "launch"])


@unittest.skipUnless(os.getenv("WAYDROID_TEST_PRIVATE_BUS") == "1", "requires an isolated dbus-run-session")
class SessionLoopTests(unittest.TestCase):
    def setUp(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        (path / "wayland-test").touch()
        self.args = SimpleNamespace(timeout=5)
        defaults = dict(session_manager.tools.config.session_defaults)
        defaults.update(xdg_runtime_dir=str(path), wayland_display="wayland-test", waydroid_data=str(path / "data"))
        self.addCleanup(patch.stopall)
        patch.object(session_manager.tools.config, "session_defaults", defaults).start()
        patch.object(session_manager.tools.helpers.props, "host_get", return_value="0").start()
        self.container = patch.object(session_manager.tools.helpers.ipc, "DBusContainerService").start().return_value
        self.notifications = patch.object(session_manager, "notify").start()
        self.user_start = patch.object(session_manager.services.user_manager, "start").start()
        self.clip_start = patch.object(session_manager.services.clipboard_manager, "start").start()
        self.notification_start = patch.object(session_manager.services.notification_manager, "start").start()
        for manager in (session_manager.services.user_manager, session_manager.services.clipboard_manager,
                        session_manager.services.notification_manager):
            patch.object(manager, "stop").start()
        self.timed_out = False

        def timeout():
            self.timed_out = True
            if hasattr(self.args, "session_manager"):
                self.args.session_manager.fail("test timed out")
            return GLib.SOURCE_CONTINUE

        self.timeout_source = GLib.timeout_add_seconds(5, timeout)
        self.addCleanup(GLib.source_remove, self.timeout_source)

    def test_ready_follows_all_host_initialization(self):
        events = []

        def user_start(args, session, ready, failed):
            events.append("android")
            ready()
            GLib.idle_add(args.session_manager.Stop)

        self.user_start.side_effect = user_start
        self.clip_start.side_effect = lambda args: events.append("clipboard")
        self.notification_start.side_effect = lambda args, session: events.append("notifications")
        self.notifications.side_effect = lambda message: events.append("ready") if message.startswith("READY=1") else None
        session_manager.start(self.args)
        self.assertEqual(events, ["android", "clipboard", "notifications", "ready"])
        self.container.Stop.assert_called_once_with(False)
        self.assertFalse(self.timed_out)

    def test_container_start_failure_is_not_success(self):
        self.container.Start.side_effect = dbus.exceptions.DBusException("container failed")
        with self.assertRaisesRegex(RuntimeError, "container failed"):
            session_manager.start(self.args)
        self.user_start.assert_not_called()
        self.assertFalse(any(call.args[0].startswith("READY=1") for call in self.notifications.call_args_list))
        self.assertFalse(self.timed_out)

    def test_dbus_request_is_released_by_unlock_event(self):
        events = []
        failures = []
        client_bus = dbus.SessionBus(private=True)
        client_bus.set_exit_on_disconnect(False)
        self.addCleanup(client_bus.close)

        def user_start(args, session, ready, failed):
            original_wait = args.session_manager.readiness.wait

            def wait(success, failure):
                original_wait(success, failure)
                events.append("queued")
                ready()

            args.session_manager.readiness.wait = wait
            proxy = dbus.Interface(client_bus.get_object(
                "id.waydro.Session", "/SessionManager", introspect=False,
                follow_name_owner_changes=True), "id.waydro.SessionManager")

            def launched():
                events.append("launched")
                args.session_manager.Stop()

            def error(exception):
                failures.append(exception)
                args.session_manager.Stop()

            proxy.WaitForReady(reply_handler=launched, error_handler=error, timeout=5)

        self.user_start.side_effect = user_start
        session_manager.start(self.args)
        self.assertEqual(events, ["queued", "launched"])
        self.assertEqual(failures, [])
        self.assertFalse(self.timed_out)

    def test_early_stop_does_not_enter_a_second_loop(self):
        self.user_start.side_effect = lambda args, *rest: args.session_manager.Stop()
        with self.assertRaisesRegex(RuntimeError, "before Android became ready"):
            session_manager.start(self.args)
        self.clip_start.assert_not_called()
        self.notification_start.assert_not_called()
        self.container.Stop.assert_called_once_with(False)
        self.assertFalse(self.timed_out)


@unittest.skipUnless(os.getenv("WAYDROID_TEST_PRIVATE_BUS") == "1", "requires an isolated dbus-run-session")
class NotificationStartupTests(unittest.TestCase):
    def test_missing_plasma_does_not_prevent_notification_bridge_start(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        args = SimpleNamespace()
        with patch.object(notification_manager.threading, "Thread") as thread:
            notification_manager.start(args, {})
            thread.return_value.start.assert_called_once_with()
            notification_manager.stop(args)
        self.assertEqual(notification_manager.bus_signals, [])


if __name__ == "__main__":
    unittest.main()
