#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - single source of the app version.
#
# The version is defined in rpm/harbour-ferry.spec and nowhere else. At build
# time qmake reads "Version:" and "Release:" from the spec and generates
# _version.py next to this module (see harbour-ferry.pro); here it is only
# read back. When the app runs from a source tree that was never built, the
# spec is parsed directly instead, so a standalone "python3 diagnostics.py"
# still reports the real version.
#
# SPDX-License-Identifier: Apache-2.0

import os
import re

# Reported when neither the generated file nor the spec can be read - a
# packaging bug, and visible as such instead of silently claiming a version.
UNKNOWN_VERSION = "0.1"

_SPEC_RELPATH = os.path.join("rpm", "harbour-ferry.spec")
_SPEC_FIELD_RE = re.compile(r"^(Version|Release):\s*(\S+)")


def _find_spec():
    """Look for rpm/harbour-ferry.spec upwards from this file (source tree)."""
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        candidate = os.path.join(directory, _SPEC_RELPATH)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def _from_spec():
    """Parse Version/Release out of the spec file, (None, None) if absent."""
    path = _find_spec()
    if not path:
        return None, None
    version = release = None
    try:
        # The spec has CRLF line endings; \S+ stops before the CR.
        with open(path, "r", encoding="utf-8") as spec:
            for line in spec:
                match = _SPEC_FIELD_RE.match(line)
                if not match:
                    continue
                if match.group(1) == "Version":
                    version = match.group(2)
                else:
                    release = match.group(2)
    except (IOError, OSError, UnicodeDecodeError):
        return None, None
    return version, release


def _from_generated():
    """Read the values qmake generated at build time, (None, None) if absent."""
    try:
        import _version
    except ImportError:
        return None, None
    return (getattr(_version, "APP_VERSION", None),
            getattr(_version, "APP_RELEASE", None))


def _detect():
    for version, release in (_from_generated(), _from_spec()):
        if version:
            return version, release or ""
    return UNKNOWN_VERSION, ""


APP_VERSION, APP_RELEASE = _detect()

# "0.2-1" - Version and Release together, as the RPM and the release tag
# spell it. APP_VERSION alone stays the plain "0.2".
APP_VERSION_FULL = APP_VERSION + ("-" + APP_RELEASE if APP_RELEASE else "")


def app_version_full():
    """The version string for the UI ("0.3-1").

    PyOtherSide calls functions, not module attributes, so the QML side
    needs this wrapper around APP_VERSION_FULL.
    """
    return APP_VERSION_FULL


if __name__ == "__main__":
    print(APP_VERSION_FULL)
