#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - local file browser backend.
# The browser opens on an overview: the standard user folders first, then
# everything else that lives directly in the home directory, then mounted
# removable media, and last an entry into the file system root. Hidden
# entries are left out unless the caller asks for them (the pull-down switch
# in FileBrowserPage).
#
# The visible area used to end at the standard user folders, because Sailjail
# would not have granted more. The app runs unsandboxed (Sandboxing=Disabled
# in harbour-ferry.desktop, so that restriction only kept users from
# syncing folders they own - what may be read is the file system's decision
# now, and an unreadable folder simply reports why.
#
# SPDX-License-Identifier: Apache-2.0

import datetime
import os

import common

log = common.make_logger("filebrowser")

# Shown first and in this order, as far as they exist.
USER_FOLDERS = ["Documents", "Downloads", "Music", "Pictures", "Videos"]

SYSTEM_ROOT = "/"


def _media_root():
    user = os.path.basename(common.home_dir().rstrip("/"))
    return "/run/media/%s" % user


def _entry(name, path, special=""):
    """One row of the overview page.

    Every entry carries the same keys on purpose: the QML ListModel takes its
    roles from the first row it is given and ignores keys that only turn up
    later.
    """
    return {"name": name, "path": path, "is_dir": True, "size": -1,
            "mtime": "", "special": special}


def _home_folders(show_hidden):
    """The standard folders, followed by the rest of the home directory."""
    home = common.home_dir()
    folders = []
    already_listed = set()
    for name in USER_FOLDERS:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            folders.append(_entry(name, path))
            already_listed.add(name)
    try:
        for name in sorted(os.listdir(home), key=lambda n: n.lower()):
            if name in already_listed:
                continue
            if name.startswith(".") and not show_hidden:
                continue
            path = os.path.join(home, name)
            if os.path.isdir(path):
                folders.append(_entry(name, path))
    except OSError as e:
        log("cannot list the home directory: %s" % e)
    return folders


def _media_folders():
    folders = []
    media_root = _media_root()
    try:
        if os.path.isdir(media_root):
            for name in sorted(os.listdir(media_root)):
                path = os.path.join(media_root, name)
                if os.path.isdir(path):
                    folders.append(_entry("SD card: %s" % name, path))
    except OSError as e:
        log("cannot list removable media: %s" % e)
    return folders


def list_roots(show_hidden=False):
    """The overview page: home folders, removable media, the system root."""
    show_hidden = bool(show_hidden)
    entries = _home_folders(show_hidden) + _media_folders()
    # Last, and marked as special: the page translates its name and it is the
    # one entry that leaves the user's own files behind.
    entries.append(_entry("System files", SYSTEM_ROOT, special="system"))
    log("roots (hidden=%s): %s"
        % (show_hidden, [e["name"] for e in entries]))
    return {"ok": True, "entries": entries}


def list_dir(path, show_hidden=False):
    """List one directory. Hidden entries only when they were asked for."""
    show_hidden = bool(show_hidden)
    entries = []
    try:
        for item in os.scandir(path):
            if item.name.startswith(".") and not show_hidden:
                continue
            try:
                stat = item.stat()
                is_dir = item.is_dir()
            except OSError:
                # A dangling symlink or something the user may not stat:
                # skipping it keeps one broken entry from failing the folder.
                continue
            entries.append({
                "name": item.name,
                "path": item.path,
                "is_dir": is_dir,
                "size": -1 if is_dir else stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime)
                         .strftime("%Y-%m-%d %H:%M"),
                "special": "",
            })
    except OSError as e:
        log("cannot list %r: %s" % (path, e))
        # strerror alone ("Permission denied") reads better than the full
        # repr, which repeats the path the header already shows.
        return {"ok": False,
                "message": "Folder could not be read: %s" % (e.strerror or e)}
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    log("listed %r (hidden=%s): %d entries" % (path, show_hidden, len(entries)))
    return {"ok": True, "entries": entries}
