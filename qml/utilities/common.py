#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - shared helpers for the Python modules (paths, logging,
# subprocess execution, rclone binary lookup).
#
# SPDX-License-Identifier: Apache-2.0

import os
import platform
import shutil
import subprocess

APP_NAME = "harbour-ferry"
APP_VERSION = "0.2"


def make_logger(tag):
    """Create a debug log function (DEV-02) that never crashes on
    ASCII-only stdout (C locale under the SDK debugger)."""
    prefix = "[ferry:%s]" % tag

    def log(msg):
        line = "%s %s" % (prefix, msg)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    return log


_log = make_logger("common")


def home_dir():
    return os.path.expanduser("~")


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME", os.path.join(home_dir(), ".config"))
    return os.path.join(base, APP_NAME, APP_NAME)


def data_dir():
    base = os.environ.get("XDG_DATA_HOME", os.path.join(home_dir(), ".local", "share"))
    return os.path.join(base, APP_NAME, APP_NAME)


def app_install_dir():
    # __file__ is .../qml/utilities/common.py -> install root is two levels up.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_rclone():
    """Locate the rclone binary: bundled first, then PATH (AD-07a).

    Returns (path_or_None, origin_description).
    """
    machine = platform.machine()
    candidates = [
        os.path.join(app_install_dir(), "bin", "rclone"),
        os.path.join(app_install_dir(), "bin", "rclone-%s" % machine),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c, "bundled"
    path_rclone = shutil.which("rclone")
    if path_rclone:
        return path_rclone, "system PATH"
    return None, "not found (checked: %s, PATH)" % ", ".join(candidates)


def encode_cmd(cmd):
    """Encode argv to UTF-8 bytes on POSIX. The embedded interpreter may run
    with an ASCII locale (SDK debugger) and would otherwise crash on
    non-ASCII characters in paths (e.g. German umlauts)."""
    if os.name != "posix":
        return cmd
    return [c.encode("utf-8") if isinstance(c, str) else c for c in cmd]


def run_cmd(cmd, timeout=30, input_text=None, env_extra=None, logger=None,
            log_args=True):
    """Run a command, capture output, never raise. Returns (rc, output).

    Output is decoded as UTF-8 with replacement (locale may be ASCII).
    Set log_args=False when the command line contains secret values.
    """
    log = logger or _log
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log("exec: %s" % (" ".join(cmd) if log_args else "%s [args hidden]" % cmd[0]))
    try:
        proc = subprocess.run(
            encode_cmd(cmd),
            input=input_text.encode("utf-8") if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        log("exec done: rc=%d, %d bytes output" % (proc.returncode, len(out)))
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        log("exec timeout after %ss" % timeout)
        return -1, "TIMEOUT after %ss" % timeout
    except FileNotFoundError as e:
        log("exec failed: %s" % e)
        return -2, "NOT FOUND: %s" % e
    except Exception as e:
        log("exec failed: %s" % e)
        return -3, "ERROR: %s" % e
