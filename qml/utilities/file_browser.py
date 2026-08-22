#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - local file browser backend (FR-06, AD-07c).
# Visible area is restricted to the standard user folders (Documents,
# Downloads, Music, Pictures, Videos) and mounted removable media; the
# browser starts on an overview of these roots.
#
# SPDX-License-Identifier: Apache-2.0

import datetime
import os

import common

log = common.make_logger("filebrowser")

USER_FOLDERS = ["Documents", "Downloads", "Music", "Pictures", "Videos"]


def _media_root():
    user = os.path.basename(common.home_dir().rstrip("/"))
    return "/run/media/%s" % user


def _allowed_roots():
    roots = []
    home = common.home_dir()
    for name in USER_FOLDERS:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            roots.append((name, path))
    media_root = _media_root()
    try:
        if os.path.isdir(media_root):
            for entry in sorted(os.listdir(media_root)):
                path = os.path.join(media_root, entry)
                if os.path.isdir(path):
                    roots.append(("SD card: %s" % entry, path))
    except OSError as e:
        log("cannot list removable media: %s" % e)
    return roots


def _is_allowed(path):
    real = os.path.realpath(path)
    for _, root in _allowed_roots():
        root_real = os.path.realpath(root)
        if real == root_real or real.startswith(root_real + os.sep):
            return True
    return False


def list_roots():
    """The overview page: standard user folders + removable media."""
    roots = [{"name": name, "path": path, "is_dir": True, "size": -1, "mtime": ""}
             for name, path in _allowed_roots()]
    log("roots: %s" % [r["name"] for r in roots])
    return {"ok": True, "entries": roots}


def list_dir(path):
    """List a directory inside the allowed area. Hidden files are skipped."""
    if not _is_allowed(path):
        log("access denied outside allowed roots: %r" % path)
        return {"ok": False, "message": "Access is restricted to the standard user folders"}
    entries = []
    try:
        for item in os.scandir(path):
            if item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                is_dir = item.is_dir()
            except OSError:
                continue
            entries.append({
                "name": item.name,
                "path": item.path,
                "is_dir": is_dir,
                "size": -1 if is_dir else stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime)
                         .strftime("%Y-%m-%d %H:%M"),
            })
    except OSError as e:
        log("cannot list %r: %s" % (path, e))
        return {"ok": False, "message": "Folder could not be read: %s" % e}
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    log("listed %r: %d entries" % (path, len(entries)))
    return {"ok": True, "entries": entries}
