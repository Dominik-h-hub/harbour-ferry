#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - list of the files a one-way pair has already transferred.
#
# Used by push and pull pairs with "skip_transferred" set (see
# sync_engine._transfer_once): a file in this list is not transferred again,
# even after it was deleted on the destination side - unless its size on the
# source side changes. One JSON file per pair:
#   {"version": 1, "rev": <n>, "files": {"<path relative to the source root>": <size>}}
#
# "rev" is the pair's transfer_rev (sync_pairs) the list was built for. An
# edit of the pair's paths/direction bumps it, so a run that started before
# the edit and saves its results afterwards writes a list that load() then
# ignores - those results describe the old destination. A missing "rev"
# counts as 0, the value of pairs that were never edited that way.
#
# It lives in its own directory so My Backup can take the whole directory
# (harbour-ferry.desktop, BackupPathList): without it, a restored device would
# transfer everything again that was deleted on the destination side.
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil

import common

log = common.make_logger("transfer")

_VERSION = 1


def list_dir():
    return os.path.join(common.data_dir(), "transferred")


def tmp_dir():
    """Scratch files of a run (file list, rclone report) - not backed up."""
    return os.path.join(common.data_dir(), "transfer-tmp")


def _path(pair_id):
    return os.path.join(list_dir(), "%s.json" % pair_id)


def load(pair_id, rev=0):
    """The stored {path: size} dict, or None when there is no list yet.

    None and {} mean different things: None is a first run (or a reset),
    which compares against the destination; {} is a list that is simply
    empty because the source is. A list built for another revision of the
    pair (see the header) is None as well.
    """
    path = _path(pair_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        # A damaged list is treated like no list: the next run compares
        # against the destination again and transfers only what is missing
        # there - the safe direction, nothing is lost.
        log("transfer list %s unreadable (%s) - starting over" % (path, e))
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        log("transfer list %s has no file table - starting over" % path)
        return None
    stored_rev = data.get("rev", 0)
    if stored_rev != rev:
        log("transfer list %s is for revision %r, pair is at %r - starting"
            " over" % (path, stored_rev, rev))
        return None
    return files


def save(pair_id, files, rev=0):
    """Store the list, tagged with the pair revision it was built for."""
    path = _path(pair_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": _VERSION, "rev": rev, "files": files}, f,
                  ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def delete(pair_id):
    """Forget what the pair transferred. Returns True if a list existed."""
    try:
        os.remove(_path(pair_id))
    except FileNotFoundError:
        return False
    log("transfer list of %s deleted" % pair_id)
    return True


def delete_all():
    for path in (list_dir(), tmp_dir()):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    log("all transfer lists deleted")
