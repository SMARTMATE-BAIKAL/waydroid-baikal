# Copyright 2021 Erfan Abdi
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import shutil
import time
import tools.config
import tools.helpers.props
import tools.helpers.ipc
from tools.helpers.systemd import start_session
from tools.interfaces import IPlatform
from tools.interfaces import IStatusBarService
import dbus

def install(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        tmp_dir = tools.config.session_defaults["waydroid_data"] + "/waydroid_tmp"
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)

        shutil.copyfile(args.PACKAGE, tmp_dir + "/base.apk")
        platformService = IPlatform.get_service(args)
        if platformService:
            platformService.installApp("/data/waydroid_tmp/base.apk")
        else:
            logging.error("Failed to access IPlatform service")
        os.remove(tmp_dir + "/base.apk")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except (dbus.DBusException, KeyError):
        logging.error("WayDroid session is stopped")

def remove(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        platformService = IPlatform.get_service(args)
        if platformService:
            ret = platformService.removeApp(args.PACKAGE)
            if ret != 0:
                logging.error("Failed to uninstall package: {}".format(args.PACKAGE))
        else:
            logging.error("Failed to access IPlatform service")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except dbus.DBusException:
        logging.error("WayDroid session is stopped")

def maybeLaunchLater(args, launchNow):
    try:
        session = tools.helpers.ipc.DBusSessionService()
    except dbus.DBusException as error:
        if error.get_dbus_name() not in ("org.freedesktop.DBus.Error.ServiceUnknown",
                                        "org.freedesktop.DBus.Error.NameHasNoOwner"):
            raise
        logging.info("Starting the Waydroid session service")
        start_session(args.timeout)
        session = tools.helpers.ipc.DBusSessionService()
    try:
        tools.helpers.ipc.DBusContainerService().Unfreeze()
    except Exception:
        logging.error("Failed to unfreeze container. Trying to launch anyways...")
    # The session owns its D-Bus name while Android is still booting. The
    # deferred reply is driven by userUnlocked, without readiness polling.
    session.WaitForReady(timeout=args.timeout)
    launchNow()

def launch(args):
    def justLaunch():
        platformService = IPlatform.get_service(args)
        if platformService:
            platformService.setprop("waydroid.active_apps", args.PACKAGE)
            platformService.launchApp(args.PACKAGE)
            multiwin = platformService.getprop(
                "persist.waydroid.multi_windows", "false")
            if multiwin == "false":
                platformService.settingsPutString(
                    2, "policy_control", "immersive.status=*")
            else:
                platformService.settingsPutString(
                    2, "policy_control", "immersive.full=*")
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justLaunch)

def list(args):
    try:
        tools.helpers.ipc.DBusSessionService()

        cm = tools.helpers.ipc.DBusContainerService()
        session = cm.GetSession()
        if session["state"] == "FROZEN":
            cm.Unfreeze()

        platformService = IPlatform.get_service(args)
        if platformService:
            appsList = platformService.getAppsInfo()
            for app in appsList:
                print("Name: " + app["name"])
                print("packageName: " + app["packageName"])
                print("categories:")
                for cat in app["categories"]:
                    print("\t" + cat)
        else:
            logging.error("Failed to access IPlatform service")

        if session["state"] == "FROZEN":
            cm.Freeze()
    except dbus.DBusException:
        logging.error("WayDroid session is stopped")

def showFullUI(args):
    def justShow():
        platformService = IPlatform.get_service(args)
        if platformService:
            platformService.setprop("waydroid.active_apps", "Waydroid")
            platformService.settingsPutString(2, "policy_control", "null*")
            # HACK: Refresh display contents
            statusBarService = IStatusBarService.get_service(args)
            if statusBarService:
                statusBarService.expand()
                time.sleep(0.5)
                statusBarService.collapse()
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justShow)

def intent(args):
    def justLaunch():
        platformService = IPlatform.get_service(args)
        if platformService:
            ret = platformService.launchIntent(args.ACTION, args.URI)
            if ret == "":
                return
            pkg = ret if ret != "android" else "Waydroid"
            platformService.setprop("waydroid.active_apps", pkg)
            multiwin = platformService.getprop(
                "persist.waydroid.multi_windows", "false")
            if multiwin == "false":
                platformService.settingsPutString(
                    2, "policy_control", "immersive.status=*")
            else:
                platformService.settingsPutString(
                    2, "policy_control", "immersive.full=*")
        else:
            logging.error("Failed to access IPlatform service")
    maybeLaunchLater(args, justLaunch)
