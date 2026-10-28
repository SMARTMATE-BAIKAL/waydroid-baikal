# Copyright 2021 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pwd

#
# Exported functions
#
from tools.config.load import load, load_channels
from tools.config.save import save

#
# Exported variables (internal configuration)
#
version = "1.6.3"
tools_src = os.path.normpath(os.path.realpath(__file__) + "/../../..")

# Keys saved in the config file (mostly what we ask in 'waydroid init')
config_keys = ["arch",
               "images_path",
               "vendor_type",
               "system_datetime",
               "vendor_datetime",
               "suspend_action",
               "mount_overlays",
               "auto_adb"]

# Config file/commandline default values
# $WORK gets replaced with the actual value for args.work (which may be
# overridden on the commandline)
defaults = {
    "arch": "arm64",
    "work": "/var/lib/waydroid",
    "vendor_type": "MAINLINE",
    "system_datetime": "0",
    "vendor_datetime": "0",
    "preinstalled_images_paths": [
        "/etc/waydroid-extra/images",
        "/usr/share/waydroid-extra/images",
    ],
    "suspend_action": "freeze",
    "mount_overlays": "True",
    "auto_adb": "False",
    "container_xdg_runtime_dir": "/run/xdg",
    "container_wayland_display": "wayland-0",
}
defaults["images_path"] = defaults["work"] + "/images"
defaults["rootfs"] = defaults["work"] + "/rootfs"
defaults["overlay"] = defaults["work"] + "/overlay"
defaults["overlay_rw"] = defaults["work"] + "/overlay_rw"
defaults["overlay_work"] = defaults["work"] + "/overlay_work"
defaults["data"] = defaults["work"] + "/data"
defaults["lxc"] = defaults["work"] + "/lxc"
defaults["host_perms"] = defaults["work"] + "/host-permissions"
defaults["container_pulse_runtime_path"] = defaults["container_xdg_runtime_dir"] + "/pulse"

session_defaults = {
    "user_name": pwd.getpwuid(os.getuid()).pw_name,
    "user_id": str(os.getuid()),
    "group_id": str(os.getgid()),
    "host_user": os.path.expanduser("~"),
    "pid": str(os.getpid()),
    "xdg_data_home": str(os.environ.get('XDG_DATA_HOME', os.path.expanduser("~") + "/.local/share")),
    "xdg_runtime_dir": str(os.environ.get('XDG_RUNTIME_DIR')),
    "wayland_display": str(os.environ.get('WAYLAND_DISPLAY')),
    "pulse_runtime_path": str(os.environ.get('PULSE_RUNTIME_PATH')),
    "state": "STOPPED",
    "lcd_density": "0",
    "background_start": "true"
}
session_defaults["waydroid_user_state"] = session_defaults["xdg_data_home"] + "/waydroid"
session_defaults["waydroid_data"] = session_defaults["waydroid_user_state"] + "/data"
if session_defaults["pulse_runtime_path"] == "None":
    session_defaults["pulse_runtime_path"] = session_defaults["xdg_runtime_dir"] + "/pulse"


def _userdata_path(path):
    # LXC mount entries are whitespace-delimited and must stay on one line.
    def invalid_characters(value):
        return any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)

    if not path or not os.path.isabs(path) or invalid_characters(path):
        raise ValueError("Configured userdata must be an absolute path without whitespace or control characters")
    try:
        resolved = os.path.realpath(path, strict=True)
    except OSError as error:
        raise ValueError("Configured userdata directory is unavailable: {}".format(path)) from error
    if invalid_characters(resolved):
        raise ValueError("Configured userdata resolves to a path with whitespace or control characters")
    if resolved == os.path.sep or not os.path.isdir(resolved):
        raise ValueError("Configured userdata must be an existing directory other than the filesystem root")
    return resolved


def configured_userdata(args, user_id):
    """Return an explicit userdata mapping from the system configuration."""
    uid = str(user_id)
    if not uid.isascii() or not uid.isdecimal() or int(uid) > 0xFFFFFFFF:
        raise ValueError("Invalid Unix user ID for userdata selection")
    cfg = load(args)
    path = cfg.get("userdata", str(int(uid)), raw=True, fallback=None)
    return _userdata_path(path) if path is not None else None


def configure_session(args):
    """Select Android data without moving the user's desktop integration."""
    path = configured_userdata(args, session_defaults["user_id"])
    if path is None:
        path = session_defaults["waydroid_user_state"] + "/data"
    session_defaults["waydroid_data"] = path


def validate_session_userdata(args, session):
    """Enforce the system mapping after the D-Bus caller UID was verified."""
    path = configured_userdata(args, session["user_id"])
    if path is None:
        return
    if _userdata_path(session.get("waydroid_data", "")) != path:
        raise ValueError("Session userdata does not match the configured directory for this user")
    session["waydroid_data"] = path


channels_defaults = {
    "config_path": "/usr/share/waydroid-extra/channels.cfg",
    "system_channel": "https://ota.waydro.id/system",
    "vendor_channel": "https://ota.waydro.id/vendor",
    "rom_type": "lineage",
    "system_type": "VANILLA"
}
channels_config_keys = ["system_channel",
                        "vendor_channel",
                        "rom_type",
                        "system_type"]
