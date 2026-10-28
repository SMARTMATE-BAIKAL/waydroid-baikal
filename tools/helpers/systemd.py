# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""Activate the native session unit without taking over its lifecycle."""

import dbus
import dbus.mainloop.glib
from gi.repository import GLib


SESSION_UNIT = "waydroid-session.service"
MANAGER_NAME = "org.freedesktop.systemd1"
MANAGER_PATH = "/org/freedesktop/systemd1"
MANAGER_INTERFACE = "org.freedesktop.systemd1.Manager"


def start_session(timeout):
    """Wait for the systemd start job, including its Type=notify readiness."""
    if timeout <= 0:
        raise ValueError("Session activation timeout must be positive")

    # A private connection scopes Subscribe() to this request. Closing it also
    # releases the subscription, without disturbing other clients in this process.
    bus = dbus.SessionBus(private=True, mainloop=dbus.mainloop.glib.DBusGMainLoop())
    bus.set_exit_on_disconnect(False)
    loop = GLib.MainLoop()
    matches = []
    timer = None
    finished = False
    failure = None
    job_path = None
    early_results = {}

    def complete(error=None):
        nonlocal finished, failure
        if finished:
            return
        finished = True
        failure = error
        loop.quit()

    def job_finished(result):
        if result == "done":
            complete()
        else:
            complete(RuntimeError(f"Failed to start {SESSION_UNIT}: {result}"))

    def job_removed(job_id, path, unit, result):
        if finished or unit != SESSION_UNIT:
            return
        if job_path is None:
            # A fast job can finish before the asynchronous StartUnit reply.
            early_results[str(path)] = str(result)
        elif path == job_path:
            job_finished(str(result))

    def started(path):
        nonlocal job_path
        if finished:
            return
        job_path = str(path)
        if job_path in early_results:
            job_finished(early_results[job_path])
        early_results.clear()

    def subscribed():
        if not finished:
            try:
                manager.StartUnit(SESSION_UNIT, "replace", reply_handler=started,
                                  error_handler=complete, timeout=timeout)
            except Exception as error:
                complete(error)

    def manager_changed(name, old_owner, new_owner):
        if old_owner and old_owner != new_owner:
            complete(RuntimeError("The user service manager disconnected during session activation"))

    def timed_out():
        nonlocal timer
        timer = None
        complete(TimeoutError(f"Timed out while starting {SESSION_UNIT}"))
        return GLib.SOURCE_REMOVE

    try:
        manager = dbus.Interface(bus.get_object(
            MANAGER_NAME, MANAGER_PATH, introspect=False), MANAGER_INTERFACE)
        # Install signal matches and subscribe before queuing a job. This also
        # covers a start job shared with another launcher or the login target.
        matches.append(bus.add_signal_receiver(
            job_removed, signal_name="JobRemoved", dbus_interface=MANAGER_INTERFACE,
            bus_name=MANAGER_NAME, path=MANAGER_PATH))
        matches.append(bus.add_signal_receiver(
            manager_changed, signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus", bus_name="org.freedesktop.DBus",
            path="/org/freedesktop/DBus", arg0=MANAGER_NAME))
        matches.append(bus.add_signal_receiver(
            lambda: complete(RuntimeError("The session bus disconnected during session activation")),
            signal_name="Disconnected", dbus_interface="org.freedesktop.DBus.Local"))
        timer = GLib.timeout_add(max(1, int(timeout * 1000)), timed_out)
        manager.Subscribe(reply_handler=subscribed, error_handler=complete, timeout=timeout)
        if not finished:
            loop.run()
        if failure is not None:
            raise failure
    finally:
        if timer is not None:
            GLib.source_remove(timer)
        for match in matches:
            match.remove()
        bus.close()
