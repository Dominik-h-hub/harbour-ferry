#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry background sync helper.
# Started by the systemd user timer; runs all sync pairs through the same
# engine as the app (network rule and skip behavior included)
# and exits afterwards. Also writes the diagnostics marker file.
#
# SPDX-License-Identifier: Apache-2.0

import datetime
import json
import os
import sys

LOG_PREFIX = "[ferry-helper]"

# Make the app's Python modules importable.
_INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_INSTALL_ROOT, "qml", "utilities"))

import common
import sync_engine


def log(msg):
    line = "%s %s" % (LOG_PREFIX, msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def write_marker():
    """Diagnostics marker proving the timer chain works."""
    payload = {
        "written_at": datetime.datetime.now().isoformat(),
        "pid": os.getpid(),
        "home": os.path.expanduser("~"),
    }
    try:
        os.makedirs(common.data_dir(), exist_ok=True)
        path = os.path.join(common.data_dir(), "sync-marker.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        log("marker written: %s" % path)
    except OSError as e:
        log("cannot write marker: %s" % e)


def main():
    log("helper started, pid=%d" % os.getpid())
    write_marker()
    try:
        result = sync_engine.run_all_now()
        log("helper finished: %s" % result)
    except Exception as e:
        log("helper FAILED: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
