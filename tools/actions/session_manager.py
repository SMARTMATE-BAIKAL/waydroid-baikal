# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import signal
import tools.config
import tools.helpers.ipc
from tools.helpers.session import Readiness, notify
from tools import services
import dbus
import dbus.service
import dbus.exceptions
from gi.repository import GLib
import copy


def dispatch(callback, *args):
    def invoke():
        callback(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(invoke)


class DbusSessionManager(dbus.service.Object):
    def __init__(self, looper, bus, object_path, args):
        self.args = args
        self.looper = looper
        self.stopping = False
        self.container_started = False
        self.stop_container_on_exit = True
        self.failure = None
        self.readiness = Readiness(dispatch, self.notify_ready)
        dbus.service.Object.__init__(self, bus, object_path)

    def notify_ready(self):
        notify("READY=1\nSTATUS=Android user session is ready")

    def fail(self, message):
        if self.failure is None:
            self.failure = message
        self.readiness.stop(message)
        dispatch(self.Stop)

    @dbus.service.method("id.waydro.SessionManager", in_signature='', out_signature='',
                         async_callbacks=('reply_handler', 'error_handler'))
    def WaitForReady(self, reply_handler, error_handler):
        def failed(message):
            error_handler(dbus.exceptions.DBusException(
                message, name="id.waydro.SessionManager.NotReady"))
        self.readiness.wait(reply_handler, failed)

    @dbus.service.method("id.waydro.SessionManager", in_signature='', out_signature='')
    def Stop(self):
        do_stop(self.args, self.looper)

def handle_disconnect(args, looper):
    do_stop(args, looper)

def service(args, looper, initialize):
    bus = dbus.SessionBus()
    bus.set_exit_on_disconnect(False)
    disconnect = bus.add_signal_receiver(lambda: handle_disconnect(args, looper),
                                         signal_name='Disconnected',
                                         dbus_interface='org.freedesktop.DBus.Local')
    pending = True

    def start_services():
        nonlocal pending
        pending = False
        initialize()
        return GLib.SOURCE_REMOVE

    startup_source = GLib.idle_add(start_services)
    try:
        looper.run()
    finally:
        if pending:
            GLib.source_remove(startup_source)
        disconnect.remove()

def start(args, unlocked_cb=None, background=True):
    mainloop = GLib.MainLoop()
    bus = dbus.SessionBus()
    # Export before claiming the name, so competing launchers can immediately
    # queue WaitForReady rather than observing an absent SessionManager object.
    manager = DbusSessionManager(mainloop, bus, '/SessionManager', args)
    try:
        _name = dbus.service.BusName("id.waydro.Session", bus, do_not_queue=True)
    except dbus.exceptions.NameExistsException:
        manager.remove_from_connection()
        if unlocked_cb:
            tools.helpers.ipc.DBusSessionService().WaitForReady(timeout=args.timeout)
            unlocked_cb()
            return
        raise RuntimeError("A Waydroid session is already running")

    args.session_manager = manager
    signal_sources = []

    def sigint_handler(data):
        do_stop(args, mainloop)
        return GLib.SOURCE_CONTINUE

    def sigusr_handler(data):
        manager.stop_container_on_exit = False
        do_stop(args, mainloop)
        return GLib.SOURCE_CONTINUE

    try:
        session = copy.copy(tools.config.session_defaults)

        # TODO: also support WAYLAND_SOCKET?
        wayland_display = session["wayland_display"]
        if wayland_display == "None" or not wayland_display:
            logging.warning('WAYLAND_DISPLAY is not set, defaulting to "wayland-0"')
            wayland_display = session["wayland_display"] = "wayland-0"

        if os.path.isabs(wayland_display):
            wayland_socket_path = wayland_display
        else:
            xdg_runtime_dir = session["xdg_runtime_dir"]
            if xdg_runtime_dir == "None" or not xdg_runtime_dir:
                raise RuntimeError("XDG_RUNTIME_DIR is not set; please don't start a Waydroid session with 'sudo'!")
            wayland_socket_path = os.path.join(xdg_runtime_dir, wayland_display)
        if not os.path.exists(wayland_socket_path):
            raise RuntimeError(f"Wayland socket '{wayland_socket_path}' doesn't exist; are you running a Wayland compositor?")

        os.makedirs(session["waydroid_data"], exist_ok=True)
        dpi = tools.helpers.props.host_get(args, "ro.sf.lcd_density")
        if dpi == "":
            dpi = os.getenv("GRID_UNIT_PX")
            dpi = str(int(dpi) * 20) if dpi is not None else "0"
        session["lcd_density"] = dpi
        session["background_start"] = "true" if background else "false"

        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal_sources.append(GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, sigint_handler, None))
        signal_sources.append(GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, sigusr_handler, None))

        if unlocked_cb:
            manager.readiness.wait(unlocked_cb, manager.fail)

        def initialize():
            if manager.stopping:
                return
            try:
                notify("STATUS=Starting Android user session")
                tools.helpers.ipc.DBusContainerService().Start(session)
                manager.container_started = True
                if manager.stopping:
                    return
                services.user_manager.start(args, session, manager.readiness.android_ready, manager.fail)
                if manager.stopping:
                    return
                services.clipboard_manager.start(args)
                if manager.stopping:
                    return
                services.notification_manager.start(args, session)
                manager.readiness.host_ready()
            except Exception as error:
                logging.exception("Failed to start Waydroid session")
                manager.fail(str(error))

        # Start initialization from the running loop. An early stop/failure can
        # then quit it reliably, including when Android reports readiness early.
        service(args, mainloop, initialize)
        if manager.failure:
            raise RuntimeError(manager.failure)
        if not manager.readiness.ready:
            raise RuntimeError("Waydroid session stopped before Android became ready")
    finally:
        do_stop(args, mainloop)
        if manager.container_started and manager.stop_container_on_exit:
            stop_container(quit_session=False)
        for source in signal_sources:
            GLib.source_remove(source)
        manager.remove_from_connection()

def do_stop(args, looper):
    manager = getattr(args, "session_manager", None)
    if manager:
        if manager.stopping:
            return
        manager.stopping = True
        manager.readiness.stop()
        try:
            notify("STOPPING=1\nSTATUS=Stopping Android user session")
        except OSError as error:
            logging.warning("Failed to notify the service manager: %s", error)
    services.user_manager.stop(args)
    services.clipboard_manager.stop(args)
    services.notification_manager.stop(args)
    looper.quit()

def stop(args):
    try:
        tools.helpers.ipc.DBusSessionService().Stop()
    except dbus.DBusException:
        stop_container(quit_session=True)

def stop_container(quit_session):
    try:
        tools.helpers.ipc.DBusContainerService().Stop(quit_session)
    except dbus.DBusException:
        pass
