#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - sync pair storage.
# Pairs are kept in a JSON file in the app data directory. Each pair:
#   id, name (optional display name; empty = derived from the local path),
#   type ("folder"|"file"), mode ("bisync"|"push"|"pull"), local (absolute
#   path; for "file" the file itself), remote (remote directory path),
#   paused, needs_resync, filters_hash, last_run (ISO), last_ok
#   (bool|None), last_message, last_verified (unix time of the last
#   successful "push" run; sync_engine._push_passes() re-uploads what
#   changed locally after it), skip_transferred (push/pull only: files
#   already transferred are not transferred again - see transfer_list).
#
# SPDX-License-Identifier: Apache-2.0

import contextlib
import json
import os
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only, absent on dev desktops
    fcntl = None

import common
import transfer_list

log = common.make_logger("pairs")

# Sync direction. "bisync" is rclone bisync as before, "push" a one-way
# rclone copy from local to remote that never deletes on the remote side,
# "pull" the same the other way round: remote to local, never deleting
# locally. Pairs written before the mode existed carry no field at all, so
# readers must go through pair_mode() instead of reading pair["mode"]
# directly - a missing default would silently turn an old two-way pair into
# an upload.
MODE_BISYNC = "bisync"
MODE_PUSH = "push"
MODE_PULL = "pull"
MODES = (MODE_BISYNC, MODE_PUSH, MODE_PULL)

# Direction marker for log lines, local side on the left.
_ARROWS = {MODE_BISYNC: "<->", MODE_PUSH: "->", MODE_PULL: "<-"}


def mode_arrow(mode):
    return _ARROWS.get(mode, "<->")

_LOCK = threading.Lock()

# The app process and helper/sync_helper.py both mutate this store, and every
# mutation is a load/modify/save cycle - a thread lock would let one process
# overwrite what the other just wrote. The lock file is separate from the
# store on purpose: _save() replaces the store by rename, so a lock held on
# the store's own inode would be invisible to the next writer.
_LOCK_TIMEOUT = 5.0     # seconds to wait for the other process
_LOCK_RETRY = 0.05


def _lock_path():
    return os.path.join(common.data_dir(), "sync-pairs.lock")


@contextlib.contextmanager
def _transaction():
    """Serialize one load/modify/save cycle - against threads and processes.

    Waits for the other process, unlike the sync run lock: a transaction is a
    small JSON rewrite and over in milliseconds. It gives up after
    _LOCK_TIMEOUT rather than freezing the UI behind a stuck holder; the
    write itself stays atomic either way, so the worst case is a lost update
    instead of a damaged store.
    """
    with _LOCK:
        fd = _acquire_store_lock()
        try:
            yield
        finally:
            _release_store_lock(fd)


def _acquire_store_lock():
    if fcntl is None:
        return None
    try:
        os.makedirs(common.data_dir(), exist_ok=True)
        fd = os.open(_lock_path(), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        log("could not open the store lock (%s) - continuing without it" % e)
        return None
    deadline = time.time() + _LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.time() >= deadline:
                log("WARNING: store lock still held after %.1fs - continuing "
                    "without it" % _LOCK_TIMEOUT)
                os.close(fd)
                return None
            time.sleep(_LOCK_RETRY)


def _release_store_lock(fd):
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as e:
        log("could not release the store lock: %s" % e)
    try:
        os.close(fd)
    except OSError:
        pass


def _store_path():
    return os.path.join(common.data_dir(), "sync-pairs.json")


def _empty_store():
    return {"pairs": [], "last_global_run": "", "last_skip": ""}


def _load():
    path = _store_path()
    if not os.path.exists(path):
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in _empty_store().items():
            data.setdefault(key, value)
        return data
    except (OSError, ValueError) as e:
        log("could not read pair store (%s) - starting empty" % e)
        return _empty_store()


def _save(data):
    """Write the store atomically (temp file + rename).

    A plain rewrite has a window in which the file is truncated but not yet
    written; a crash or a second writer hitting that window leaves invalid
    JSON, and _load() answers that with an empty store - every sync pair
    gone. The rename is atomic, so readers see either the old or the new
    file, never a half-written one.

    Silent on purpose: the callers log what they did, including the
    resulting pair count. Logging here would put a bare "store written"
    line in front of every action line.
    """
    os.makedirs(common.data_dir(), exist_ok=True)
    path = _store_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def list_pairs():
    with _LOCK:
        data = _load()
    return data["pairs"]


def get_store():
    """Pairs plus global metadata for the main page."""
    with _LOCK:
        return _load()


def pair_mode(pair):
    """The sync direction of a pair; bisync for pairs stored without one."""
    mode = (pair or {}).get("mode")
    return mode if mode in MODES else MODE_BISYNC


def get_pair(pair_id):
    for pair in list_pairs():
        if pair["id"] == pair_id:
            return pair
    return None


def display_name(pair):
    """The name shown for a pair: its own name, else the local base name.

    Pairs written before names existed carry no field at all.
    """
    name = ((pair or {}).get("name") or "").strip()
    if name:
        return name
    local = ((pair or {}).get("local") or "").rstrip("/")
    return os.path.basename(local) or (pair or {}).get("id", "")


# A transfer list only describes the pair it was built for: other paths,
# another direction or the option switched off and on again make it
# meaningless, so a change of any of these drops it.
_TRANSFER_LIST_KEYS = ("type", "mode", "local", "remote", "skip_transferred")


def transfer_rev(pair):
    """Revision of the settings a transfer list is valid for.

    Bumped by update_pair whenever one of _TRANSFER_LIST_KEYS changes. Deleting
    the list alone is not enough: a run that started before the edit still
    holds the old settings and saves its list after the edit. That list
    carries the old revision and transfer_list.load() ignores it.
    """
    try:
        return int((pair or {}).get("transfer_rev") or 0)
    except (TypeError, ValueError):
        return 0


def add_pair(pair_type, local_path, remote_path, mode=MODE_BISYNC, name="",
             skip_transferred=False):
    if mode not in MODES:
        log("unknown sync mode %r - storing as %s" % (mode, MODE_BISYNC))
        mode = MODE_BISYNC
    pair = {
        "id": "pair-%d" % int(time.time() * 1000),
        "name": (name or "").strip(),
        "type": pair_type,
        "mode": mode,
        "local": local_path,
        "remote": remote_path,
        "paused": False,
        "needs_resync": True,   # first run uses --resync
        "filters_hash": "",
        "last_run": "",
        "last_ok": None,
        "last_message": "",
        # Two-way pairs have nothing to skip - bisync keeps its own listing.
        "skip_transferred": bool(skip_transferred) and mode != MODE_BISYNC,
    }
    with _transaction():
        data = _load()
        data["pairs"].append(pair)
        _save(data)
        stored = len(data["pairs"])
    log("pair added: %s (%s %s) %s %s %s (%d pair(s) stored)"
        % (pair["id"], mode, pair_type, local_path,
           mode_arrow(mode), remote_path, stored))
    return pair


def update_pair(pair_id, fields):
    if "name" in fields:
        fields = dict(fields, name=(fields["name"] or "").strip())
    with _transaction():
        data = _load()
        for pair in data["pairs"]:
            if pair["id"] == pair_id:
                stale = any(key in fields and fields[key] != pair.get(key)
                            for key in _TRANSFER_LIST_KEYS)
                pair.update(fields)
                if stale:
                    pair["transfer_rev"] = transfer_rev(pair) + 1
                _save(data)
                log("pair %s updated: %s" % (pair_id, sorted(fields.keys())))
                if stale:
                    # The revision bump is what invalidates a list a running
                    # sync saves later; deleting just tidies up now.
                    transfer_list.delete(pair_id)
                return pair
    log("pair %s not found for update" % pair_id)
    return None


def delete_pair(pair_id):
    with _transaction():
        data = _load()
        before = len(data["pairs"])
        data["pairs"] = [p for p in data["pairs"] if p["id"] != pair_id]
        _save(data)
    transfer_list.delete(pair_id)
    log("pair %s deleted (%d -> %d)" % (pair_id, before, len(data["pairs"])))
    return True


def delete_all_pairs():
    """Remove every sync pair and the global run metadata.

    Used when the account goes away (removed or switched to another backend):
    the pairs point at remote paths of that account, so a new account starts
    from scratch. Returns the number of pairs that were removed.
    """
    with _transaction():
        data = _load()
        removed = len(data["pairs"])
        _save(_empty_store())
    transfer_list.delete_all()
    log("all sync pairs deleted (%d)" % removed)
    return removed


def set_last_global_run(timestamp):
    with _transaction():
        data = _load()
        data["last_global_run"] = timestamp
        _save(data)
    log("last global run recorded: %s" % timestamp)


def set_last_skip(reason):
    """record that a run was skipped (shown as a banner, no error)."""
    with _transaction():
        data = _load()
        data["last_skip"] = reason
        _save(data)
    log("run skipped: %s" % reason)


def clear_last_skip():
    with _transaction():
        data = _load()
        if data["last_skip"]:
            data["last_skip"] = ""
            _save(data)
            log("skip banner cleared")
