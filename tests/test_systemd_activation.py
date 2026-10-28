# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""Exercise actual D-Bus ordering in an isolated dbus-run-session."""

import os
import unittest

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from tools.helpers import systemd


class ActivationArgumentTests(unittest.TestCase):
    def test_nonpositive_timeout_is_rejected(self):
        for timeout in (0, -1):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                systemd.start_session(timeout)


class FakeManager(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, systemd.MANAGER_PATH)
        self.subscribed = False
        self.subscribe_error = None
        self.calls = []
        self.on_start = None

    @dbus.service.method(systemd.MANAGER_INTERFACE, in_signature="", out_signature="")
    def Subscribe(self):
        if self.subscribe_error:
            raise self.subscribe_error
        self.subscribed = True

    @dbus.service.method(systemd.MANAGER_INTERFACE, in_signature="ss", out_signature="o",
                         async_callbacks=("reply", "error"))
    def StartUnit(self, unit, mode, reply, error):
        self.calls.append((str(unit), str(mode), self.subscribed))
        self.on_start(reply, error)

    @dbus.service.signal(systemd.MANAGER_INTERFACE, signature="uoss")
    def JobRemoved(self, job_id, path, unit, result):
        pass


@unittest.skipUnless(os.getenv("WAYDROID_TEST_PRIVATE_BUS") == "1", "requires an isolated dbus-run-session")
class ActivationBusTests(unittest.TestCase):
    job_path = "/org/freedesktop/systemd1/job/7"

    def setUp(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus(private=True)
        self.bus.set_exit_on_disconnect(False)
        self.name = dbus.service.BusName(systemd.MANAGER_NAME, self.bus, do_not_queue=True)
        self.manager = FakeManager(self.bus)
        self.addCleanup(self.bus.close)
        self.addCleanup(self.manager.remove_from_connection)
        self.addCleanup(setattr, self, "name", None)

    def finish(self, result="done", path=None, unit=None):
        self.manager.JobRemoved(7, path or self.job_path, unit or systemd.SESSION_UNIT, result)
        return GLib.SOURCE_REMOVE

    def test_waits_for_the_subscribed_start_job(self):
        def start(reply, error):
            reply(self.job_path)
            GLib.idle_add(self.finish)

        self.manager.on_start = start
        systemd.start_session(2)
        self.assertEqual(self.manager.calls, [(systemd.SESSION_UNIT, "replace", True)])

    def test_completion_before_start_reply_is_not_lost(self):
        def start(reply, error):
            self.finish()
            reply(self.job_path)

        self.manager.on_start = start
        systemd.start_session(2)

    def test_unrelated_jobs_do_not_release_the_launcher(self):
        def start(reply, error):
            self.finish(path="/org/freedesktop/systemd1/job/8")
            self.finish(unit="unrelated.service")
            reply(self.job_path)
            GLib.idle_add(self.finish, "failed")

        self.manager.on_start = start
        with self.assertRaisesRegex(RuntimeError, "failed"):
            systemd.start_session(2)

    def test_unsuccessful_job_results_are_errors(self):
        for result in ("timeout", "dependency", "canceled", "skipped"):
            with self.subTest(result=result):
                def start(reply, error):
                    self.finish(result)
                    reply(self.job_path)

                self.manager.on_start = start
                with self.assertRaisesRegex(RuntimeError, result):
                    systemd.start_session(2)

    def test_start_method_error_is_propagated(self):
        def start(reply, error):
            error(dbus.exceptions.DBusException("unit missing", name="org.freedesktop.systemd1.NoSuchUnit"))

        self.manager.on_start = start
        with self.assertRaisesRegex(dbus.exceptions.DBusException, "unit missing"):
            systemd.start_session(2)

    def test_subscription_error_does_not_start_a_job(self):
        self.manager.subscribe_error = dbus.exceptions.DBusException(
            "access denied", name="org.freedesktop.DBus.Error.AccessDenied")
        with self.assertRaisesRegex(dbus.exceptions.DBusException, "access denied"):
            systemd.start_session(2)
        self.assertEqual(self.manager.calls, [])

    def test_missing_completion_has_a_bounded_timeout(self):
        self.manager.on_start = lambda reply, error: reply(self.job_path)
        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            systemd.start_session(0.05)

    def test_service_manager_disappearance_is_an_error(self):
        def disappear():
            self.bus.release_name(systemd.MANAGER_NAME)
            return GLib.SOURCE_REMOVE

        def start(reply, error):
            reply(self.job_path)
            GLib.idle_add(disappear)

        self.manager.on_start = start
        with self.assertRaisesRegex(RuntimeError, "service manager disconnected"):
            systemd.start_session(2)


if __name__ == "__main__":
    unittest.main()
