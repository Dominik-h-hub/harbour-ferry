#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - bisync engine (FR-11..FR-17).
# Runs rclone bisync per sync pair with the flag set from the requirements:
#   --size-only -v --stats 0 --stats-log-level NOTICE (FR-11)
#   --conflict-resolve newer --conflict-loser num      (FR-12a)
#   --resync on first run / after state errors         (FR-13)
#   --max-delete 50                                    (FR-14)
# Single-file pairs sync the parent folder with an include filter (FR-10).
# Runs are strictly serial (FR-17). Logs go to per-pair files (NFR-04).
#
# SPDX-License-Identifier: Apache-2.0

import datetime
import hashlib
import os
import re
import subprocess
import threading

import common
import config_manager
import enc_libraries
import network
import notify
import settings_manager
import sync_pairs

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    HAVE_PYOTHERSIDE = False

log = common.make_logger("sync")

RUN_TIMEOUT = 3600

_run_lock = threading.Lock()   # FR-17: no parallel runs


def _send(event, payload):
    if HAVE_PYOTHERSIDE:
        pyotherside.send(event, payload)
    else:
        log("(event) %s: %r" % (event, payload))


def _logs_dir():
    return os.path.join(common.data_dir(), "logs")


def _workdir():
    return os.path.join(common.data_dir(), "bisync")


def log_path(pair_id):
    return os.path.join(_logs_dir(), "%s.log" % pair_id)


def _rotate_log(pair_id):
    """Keep the previous run's log as .prev (NFR-04)."""
    current = log_path(pair_id)
    if os.path.exists(current):
        previous = current + ".prev"
        try:
            if os.path.exists(previous):
                os.remove(previous)
            os.rename(current, previous)
        except OSError as e:
            log("log rotation failed: %s" % e)


def get_log(pair_id):
    """Return the current (and previous) log content for the UI."""
    parts = []
    for suffix in ("", ".prev"):
        path = log_path(pair_id) + suffix
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                parts.append("===== %s =====\n%s" % (os.path.basename(path), content))
            except OSError as e:
                parts.append("log %s unreadable: %s" % (path, e))
    return "\n".join(parts) if parts else "No log yet."


def _filters_content(pair):
    if pair["type"] == "file":
        filename = os.path.basename(pair["local"])
        return "+ /%s\n- **\n" % filename
    # FR-15: user-editable global excludes. A change in this content changes
    # the filters hash and correctly forces a resync.
    excludes = settings_manager.get("excludes")
    return "".join("- %s\n" % pattern for pattern in excludes)


def _filters_path(pair_id):
    return os.path.join(common.data_dir(), "filters", "%s.txt" % pair_id)


def _prepare_filters(pair):
    """Write the filters file; a content change forces a resync (bisync
    requires --resync after filter changes)."""
    content = _filters_content(pair)
    content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
    path = _filters_path(pair["id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    filters_changed = (content_hash != pair.get("filters_hash", ""))
    return path, content_hash, filters_changed


def _classify_failure(output):
    lowered = output.lower()
    if "--resync" in lowered or "cannot find prior" in lowered \
            or "prior listing" in lowered or "empty prior" in lowered:
        return "needs_resync", "Sync state invalid - the next run will resync"
    if "too many deletes" in lowered or "max-delete" in lowered \
            or "safety abort" in lowered:
        return "safety_abort", ("Sync stopped: unusually many changes detected "
                                "(safety limit). Pair paused - review and sync "
                                "manually.")
    return "failed", config_manager.friendly_error(output)


def run_pair(pair_id, force=False, check_network=True):
    """Run bisync for one pair. Blocking; call from a worker thread or via
    run_pair_async. Returns the updated pair dict."""
    pair = sync_pairs.get_pair(pair_id)
    if pair is None:
        return {"ok": False, "message": "Pair not found"}
    if check_network:
        allowed, reason = network.allowed_by_rule(settings_manager.get("network_rule"))
        if not allowed:
            # FR-19a: skipped, not failed - banner instead of error.
            sync_pairs.set_last_skip("%s (%s)" % (
                reason, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
            _send("sync-status", {"pair": pair_id, "running": False,
                                  "ok": None, "message": "Skipped: %s" % reason})
            return {"skipped": True, "message": reason}
    if force:
        # FR-14: user confirmed the big change; unpause for this run.
        sync_pairs.update_pair(pair_id, {"paused": False, "safety_abort": False})
        pair = sync_pairs.get_pair(pair_id)
    with _run_lock:
        return _run_pair_locked(pair, force)


def _run_pair_locked(pair, force):
    pair_id = pair["id"]
    _send("sync-status", {"pair": pair_id, "running": True, "message": "Syncing..."})
    log("=== sync run start: %s (%s) %s <-> %s force=%s ==="
        % (pair_id, pair["type"], pair["local"], pair["remote"], force))

    local_dir = pair["local"] if pair["type"] == "folder" \
        else os.path.dirname(pair["local"])
    # Encrypted-library aware target (FR-04).
    remote_target = enc_libraries.build_target(config_manager.REMOTE_NAME,
                                               pair["remote"])

    filters_path, filters_hash, filters_changed = _prepare_filters(pair)
    resync = bool(pair.get("needs_resync")) or filters_changed
    if filters_changed:
        log("filters changed - forcing resync")

    args = ["bisync", remote_target, local_dir,
            "--size-only", "-v", "--stats", "0", "--stats-log-level", "NOTICE",
            "--conflict-resolve", "newer", "--conflict-loser", "num",
            "--max-delete", str(int(settings_manager.get("max_delete"))),
            "--workdir", _workdir(),
            "--filters-file", filters_path]
    if resync:
        args.append("--resync")
        log("running with --resync (first run or recovery, FR-13)")
    if force:
        args.append("--force")
        log("running with --force (single run only, FR-14a)")

    os.makedirs(_workdir(), exist_ok=True)
    os.makedirs(_logs_dir(), exist_ok=True)
    _rotate_log(pair_id)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        cmd, env = config_manager.build_rclone_command(args)
    except RuntimeError as e:
        return _finish_run(pair_id, now, False, str(e), {"needs_resync": True})

    try:
        with open(log_path(pair_id), "w", encoding="utf-8") as log_file:
            log_file.write("# ferry sync run %s\n# %s\n" % (now, " ".join(args)))
            log_file.flush()
            proc = subprocess.run(common.encode_cmd(cmd), stdout=log_file,
                                  stderr=subprocess.STDOUT,
                                  timeout=RUN_TIMEOUT, env=env)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        return _finish_run(pair_id, now, False, "Sync timed out", {})
    except Exception as e:
        return _finish_run(pair_id, now, False, "Sync failed to start: %s" % e, {})

    with open(log_path(pair_id), encoding="utf-8", errors="replace") as f:
        output = f.read()
    log("bisync finished rc=%d, log %d bytes" % (rc, len(output)))

    if rc == 0:
        extra = {"needs_resync": False, "filters_hash": filters_hash}
        # FR-12: detect conflict copies created by --conflict-loser num and
        # notify the user (action required - review the copies).
        conflicts = sorted(set(re.findall(r"[^\s'\"]+\.conflict\d*", output)))
        if conflicts:
            names = ", ".join(os.path.basename(c) for c in conflicts[:5])
            message = "OK - %d conflict file(s): %s" % (len(conflicts), names)
            log("conflicts detected: %s" % names)
            notify.send("Ferry: sync conflict",
                        "%s - please review" % names)
            return _finish_run(pair_id, now, True, message, extra)
        return _finish_run(pair_id, now, True, "OK", extra)

    kind, message = _classify_failure(output)
    extra = {"filters_hash": filters_hash}
    if kind == "needs_resync":
        extra["needs_resync"] = True
    elif kind == "safety_abort":
        extra["paused"] = True
        extra["safety_abort"] = True
    return _finish_run(pair_id, now, False, message, extra)


def _finish_run(pair_id, timestamp, ok, message, extra_fields):
    fields = {"last_run": timestamp, "last_ok": ok, "last_message": message}
    if ok:
        fields["safety_abort"] = False
    fields.update(extra_fields)
    pair = sync_pairs.update_pair(pair_id, fields)
    log("=== sync run end: %s ok=%s message=%s ===" % (pair_id, ok, message))
    if not ok:
        # FR-20: notifications only on failure / action required.
        name = os.path.basename((pair or {}).get("local", "")) or pair_id
        if extra_fields.get("safety_abort"):
            notify.send("Ferry: sync stopped", "%s: unusually many changes "
                        "- confirmation required" % name)
        else:
            notify.send("Ferry: sync failed", "%s: %s" % (name, message))
    _send("sync-status", {"pair": pair_id, "running": False,
                          "ok": ok, "message": message})
    return pair if pair else {"ok": ok, "message": message}


def run_pair_async(pair_id, force=False):
    """Fire-and-forget run for the UI ('sync this pair', FR-16)."""
    threading.Thread(target=run_pair, args=(pair_id, force),
                     name="ferry-sync").start()
    return True


def run_all_now():
    """Synchronous full run (FR-16/FR-17), honoring the network rule
    (FR-19/FR-19a). Used by the timer helper and the async UI wrapper."""
    allowed, reason = network.allowed_by_rule(settings_manager.get("network_rule"))
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not allowed:
        # FR-19a: skip silently - banner in the app, no error, no notification.
        sync_pairs.set_last_skip("%s (%s)" % (reason, timestamp))
        _send("sync-all-finished", {"timestamp": timestamp,
                                    "skipped": True, "reason": reason})
        log("global sync run skipped: %s" % reason)
        return {"skipped": True, "reason": reason}

    pairs = sync_pairs.list_pairs()
    log("global sync run: %d pair(s)" % len(pairs))
    for pair in pairs:
        if pair.get("paused"):
            log("skipping paused pair %s" % pair["id"])
            continue
        run_pair(pair["id"], check_network=False)
    sync_pairs.clear_last_skip()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sync_pairs.set_last_global_run(timestamp)
    _send("sync-all-finished", {"timestamp": timestamp})
    log("global sync run finished")
    return {"skipped": False}


def run_all_async():
    threading.Thread(target=run_all_now, name="ferry-sync-all").start()
    return True
