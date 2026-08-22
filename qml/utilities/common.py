#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - shared helpers for the Python modules (paths, logging,
# subprocess execution, rclone binary lookup).
#
# SPDX-License-Identifier: Apache-2.0

import os
import platform
import re
import shutil
import subprocess

# The app version lives in rpm/harbour-ferry.spec and reaches Python through
# the build (see version.py). It is re-exported here so that modules already
# importing common keep working with common.APP_VERSION.
from version import APP_VERSION, APP_RELEASE, APP_VERSION_FULL

APP_NAME = "harbour-ferry"


# An encrypted library is addressed through a connection string that carries
# its key: "remote,library=X,library_key=Y:path". rclone's "obscure" is
# reversible with "rclone reveal", so such a key in a log file is the library
# password in plain text - and logs get pasted into bug reports.
_SECRET_RE = re.compile(r'library_key=(?:"(?:[^"]|"")*"|[^,:\s]*)')


def mask_secrets(text):
    """Replace rclone library keys in text with a placeholder."""
    return _SECRET_RE.sub("library_key=***", text)


def make_logger(tag):
    """Create a debug log function that never crashes on
    ASCII-only stdout (C locale under the SDK debugger).

    Library keys are masked centrally here, so no caller has to remember it.
    """
    prefix = "[ferry:%s]" % tag

    def log(msg):
        line = mask_secrets("%s %s" % (prefix, msg))
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


# The lookup result of a found binary (process cache): every rclone call
# would otherwise stat the bundled candidates and search PATH again.
_rclone_binary = None


def find_rclone():
    """Locate the rclone binary: bundled first, then PATH.

    Returns (path_or_None, origin_description). A successful lookup is cached
    for the process lifetime; a failure is not, so a binary showing up later
    (PATH change, package install) is still picked up.
    """
    global _rclone_binary
    if _rclone_binary is not None:
        return _rclone_binary
    machine = platform.machine()
    candidates = [
        os.path.join(app_install_dir(), "bin", "rclone"),
        os.path.join(app_install_dir(), "bin", "rclone-%s" % machine),
    ]
    for c in candidates:
        if os.path.isfile(c):
            _rclone_binary = (c, "bundled")
            return _rclone_binary
    path_rclone = shutil.which("rclone")
    if path_rclone:
        _rclone_binary = (path_rclone, "system PATH")
        return _rclone_binary
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
