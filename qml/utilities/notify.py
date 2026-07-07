#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Sailfile - system notifications via org.freedesktop.Notifications
# (FR-20: only for failures / action required, never for success).
# Works both in the app and in the headless timer helper.
#
# SPDX-License-Identifier: Apache-2.0

import os

import common

log = common.make_logger("notify")


def _session_bus():
    import dbus
    try:
        return dbus.SessionBus()
    except Exception:
        # Headless helper: the session bus address may not be in the
        # environment; use the well-known SFOS socket path.
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
        return dbus.bus.BusConnection("unix:path=%s/dbus/user_bus_socket" % runtime)


def send(summary, body=""):
    """Show a system notification. Never raises."""
    try:
        import dbus
        bus = _session_bus()
        obj = bus.get_object("org.freedesktop.Notifications",
                             "/org/freedesktop/Notifications")
        iface = dbus.Interface(obj, "org.freedesktop.Notifications")
        iface.Notify("Sailfile", dbus.UInt32(0), "icon-lock-warning",
                     summary, body, [],
                     {"x-nemo-preview-summary": summary,
                      "x-nemo-preview-body": body},
                     -1)
        log("notification sent: %s" % summary)
        return True
    except Exception as e:
        log("notification failed: %s" % e)
        return False
