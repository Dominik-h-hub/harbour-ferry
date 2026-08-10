#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - network state via ConnMan on the system D-Bus (FR-19/FR-19a).
#
# SPDX-License-Identifier: Apache-2.0

import common

log = common.make_logger("network")


def get_status():
    """Return {"online": bool|None, "type": str}. online=None means the
    state could not be determined (ConnMan unreachable)."""
    try:
        import dbus
        bus = dbus.SystemBus()
        manager = dbus.Interface(bus.get_object("net.connman", "/"),
                                 "net.connman.Manager")
        state = str(manager.GetProperties().get("State", ""))
        conn_type = "unknown"
        for _, service in manager.GetServices():
            if str(service.get("State", "")) in ("online", "ready"):
                conn_type = str(service.get("Type", "unknown"))
                break
        online = state == "online" or conn_type in ("wifi", "ethernet", "cellular")
        log("network state: %s, active service type: %s" % (state, conn_type))
        return {"online": online, "type": conn_type}
    except Exception as e:
        log("ConnMan query failed: %s" % e)
        return {"online": None, "type": "unknown"}


def allowed_by_rule(rule):
    """Check the FR-19 network rule. Returns (allowed, reason)."""
    status = get_status()
    if status["online"] is None:
        # Fail open: rclone will report real connectivity errors itself.
        return True, "network state unknown - proceeding"
    if not status["online"]:
        return False, "no internet connection"
    if rule == "wifi" and status["type"] == "cellular":
        return False, "on mobile data (Wi-Fi only rule)"
    return True, "connected via %s" % status["type"]
