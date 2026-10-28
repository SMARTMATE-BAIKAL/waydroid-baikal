# SPDX-License-Identifier: GPL-3.0-or-later
# Maintainer: Timofei Granovskii <t.stifix@gmail.com>
"""Event-driven session readiness and the systemd notification protocol."""

import os
import socket
import threading


def notify(message):
    """Notify the service manager directly, if this process is supervised."""
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), address)


class Readiness:
    """Require both Android and host initialization before releasing clients."""

    def __init__(self, dispatch, on_ready):
        self._lock = threading.RLock()
        self._dispatch = dispatch
        self._on_ready = on_ready
        self._waiters = []
        self._android_ready = False
        self._host_ready = False
        self.ready = False
        self.stopped = False
        self.error = None

    def wait(self, success, failure):
        with self._lock:
            if not self.ready and not self.stopped:
                self._waiters.append((success, failure))
                return
        self._dispatch(self._complete, success, failure)

    def _complete(self, success, failure):
        with self._lock:
            error = self.error if self.stopped else None
        if error:
            failure(error)
        else:
            success()

    def android_ready(self):
        self._set_ready(android=True)

    def host_ready(self):
        self._set_ready(android=False)

    def _set_ready(self, android):
        with self._lock:
            if self.stopped or self.ready:
                return
            if android:
                self._android_ready = True
            else:
                self._host_ready = True
            if not (self._android_ready and self._host_ready):
                return
            self._on_ready()
            self.ready = True
            waiters, self._waiters = self._waiters, []
        for success, failure in waiters:
            self._dispatch(self._complete, success, failure)

    def stop(self, message="Waydroid session stopped before the request completed"):
        with self._lock:
            if self.stopped:
                return
            self.stopped = True
            self.error = message
            waiters, self._waiters = self._waiters, []
        for success, failure in waiters:
            self._dispatch(self._complete, success, failure)
