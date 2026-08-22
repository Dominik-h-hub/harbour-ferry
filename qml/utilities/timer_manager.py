#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - systemd user timer control (AD-03, FR-18).
# The interval is applied via a drop-in override for the packaged timer
# unit; "manual" disables the timer entirely.
#
# SPDX-License-Identifier: Apache-2.0

import os
import shutil

import common

log = common.make_logger("timer")

TIMER_UNIT = "harbour-ferry-sync.timer"
# OnCalendar expressions: monotonic timers (OnUnitActiveSec) stall while the
# device is suspended; calendar timers with Persistent=true catch up missed
# runs as soon as the device wakes (observed on the Fairphone 4).
INTERVALS = {
    "5min": "*:0/5",
    "15min": "*:0/15",
    "30min": "*:0/30",
    "1h": "*-*-* *:00:00",
    "6h": "*-*-* 0/6:00:00",
    "12h": "*-*-* 0,12:00:00",
}


def _dropin_dir():
    base = os.environ.get("XDG_CONFIG_HOME",
                          os.path.join(common.home_dir(), ".config"))
    return os.path.join(base, "systemd", "user", "%s.d" % TIMER_UNIT)


def _systemctl(args, timeout=20):
    return common.run_cmd(["systemctl", "--user"] + args,
                          timeout=timeout, logger=log)


def apply_interval(key):
    """Apply the FR-18 interval setting to the systemd timer.

    Returns {ok, message}.
    """
    if key == "manual":
        log("interval set to manual - disabling timer")
        rc, out = _systemctl(["disable", "--now", TIMER_UNIT])
        shutil.rmtree(_dropin_dir(), ignore_errors=True)
        _systemctl(["daemon-reload"])
        if rc != 0:
            return {"ok": False, "message": "Disabling the timer failed: %s" % out[:200]}
        return {"ok": True, "message": "Background sync disabled"}

    calendar = INTERVALS.get(key)
    if calendar is None:
        return {"ok": False, "message": "Unknown interval %r" % key}

    os.makedirs(_dropin_dir(), exist_ok=True)
    override = os.path.join(_dropin_dir(), "override.conf")
    with open(override, "w", encoding="utf-8") as f:
        f.write("# Written by Ferry (FR-18)\n"
                "[Timer]\n"
                "OnBootSec=\n"
                "OnUnitActiveSec=\n"
                "OnCalendar=\n"
                "OnCalendar=%s\n"
                "Persistent=true\n" % calendar)
    log("timer override written: OnCalendar=%s (persistent)" % calendar)

    rc, out = _systemctl(["daemon-reload"])
    if rc != 0:
        return {"ok": False, "message": "daemon-reload failed: %s" % out[:200]}
    rc, out = _systemctl(["enable", "--now", TIMER_UNIT])
    if rc != 0:
        return {"ok": False, "message": "Enabling the timer failed: %s" % out[:200]}
    log("timer enabled (%s -> OnCalendar=%s)" % (key, calendar))
    return {"ok": True, "message": "Background sync enabled (%s)" % key}


def get_status():
    """Timer status for the UI (FR-20: next planned run)."""
    rc, out = _systemctl(["is-active", TIMER_UNIT], timeout=10)
    active = (rc == 0 and out.strip() == "active")
    next_run = ""
    rc, out = _systemctl(["list-timers", "--all", "--no-pager"], timeout=15)
    if rc == 0:
        for line in out.splitlines():
            if TIMER_UNIT in line:
                next_run = " ".join(line.split()[:5])
                break
    return {"active": active, "next_run": next_run}
