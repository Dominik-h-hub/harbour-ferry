#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - sync pair storage.
# Pairs are kept in a JSON file in the app data directory. Each pair:
#   id, type ("folder"|"file"), local (absolute path; for "file" the file
#   itself), remote (remote directory path), paused, needs_resync,
#   filters_hash, last_run (ISO), last_ok (bool|None), last_message.
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import threading
import time

import common

log = common.make_logger("pairs")

_LOCK = threading.Lock()


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


def get_pair(pair_id):
    for pair in list_pairs():
        if pair["id"] == pair_id:
            return pair
    return None


def add_pair(pair_type, local_path, remote_path):
    pair = {
        "id": "pair-%d" % int(time.time() * 1000),
        "type": pair_type,
        "local": local_path,
        "remote": remote_path,
        "paused": False,
        "needs_resync": True,   # first run uses --resync
        "filters_hash": "",
        "last_run": "",
        "last_ok": None,
        "last_message": "",
    }
    with _LOCK:
        data = _load()
        data["pairs"].append(pair)
        _save(data)
        stored = len(data["pairs"])
    log("pair added: %s (%s) %s <-> %s (%d pair(s) stored)"
        % (pair["id"], pair_type, local_path, remote_path, stored))
    return pair


def update_pair(pair_id, fields):
    with _LOCK:
        data = _load()
        for pair in data["pairs"]:
            if pair["id"] == pair_id:
                pair.update(fields)
                _save(data)
                log("pair %s updated: %s" % (pair_id, sorted(fields.keys())))
                return pair
    log("pair %s not found for update" % pair_id)
    return None


def delete_pair(pair_id):
    with _LOCK:
        data = _load()
        before = len(data["pairs"])
        data["pairs"] = [p for p in data["pairs"] if p["id"] != pair_id]
        _save(data)
    log("pair %s deleted (%d -> %d)" % (pair_id, before, len(data["pairs"])))
    return True


def delete_all_pairs():
    """Remove every sync pair and the global run metadata.

    Used when the account goes away (removed or switched to another backend):
    the pairs point at remote paths of that account, so a new account starts
    from scratch. Returns the number of pairs that were removed.
    """
    with _LOCK:
        data = _load()
        removed = len(data["pairs"])
        _save(_empty_store())
    log("all sync pairs deleted (%d)" % removed)
    return removed


def set_last_global_run(timestamp):
    with _LOCK:
        data = _load()
        data["last_global_run"] = timestamp
        _save(data)
    log("last global run recorded: %s" % timestamp)


def set_last_skip(reason):
    """record that a run was skipped (shown as a banner, no error)."""
    with _LOCK:
        data = _load()
        data["last_skip"] = reason
        _save(data)
    log("run skipped: %s" % reason)


def clear_last_skip():
    with _LOCK:
        data = _load()
        if data["last_skip"]:
            data["last_skip"] = ""
            _save(data)
            log("skip banner cleared")
