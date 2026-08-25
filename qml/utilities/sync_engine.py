#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - sync engine.
# Two modes per sync pair (see sync_pairs.pair_mode):
#   "bisync" - rclone bisync with the flag set from the requirements:
#     --size-only -v --stats 0 --stats-log-level NOTICE
#     --conflict-loser num, plus --conflict-resolve newer where the
#     backend has modification times (see _modtime_supported)
#     --resync on first run / after state errors
#     --max-delete 50
#   "push"   - one-way rclone copy local -> remote. copy never deletes on
#     the destination, so none of the bisync safety machinery applies.
#     Backends without modtimes need a second rclone pass to see changes
#     that keep the file size - see _push_passes().
# Single-file pairs sync the parent folder with an include filter (bisync
# only - push always covers a whole folder).
# Runs are strictly serial. Logs go to per-pair files.
#
# SPDX-License-Identifier: Apache-2.0

import contextlib
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only, absent on dev desktops
    fcntl = None

import backend_manager
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

# runs are strictly serial. Two independent runners exist - the app
# process (manual "Sync now") and helper/sync_helper.py, started by the systemd
# timer - so a thread lock alone is not enough: both could drive rclone bisync
# and rewrite the pair store at the same time. The RLock serializes threads
# inside one process, the flock below serializes the processes. RLock (not
# Lock) because run_all_now() holds the guard across its per-pair run_pair()
# calls.
_run_lock = threading.RLock()
_lock_fd = None
_lock_depth = 0

BUSY_REASON = "another sync run is already in progress"

# Slack added to the catch-up window of a one-way pair (see _push_passes).
# The device clock can drift between two runs, so the window starts a few
# minutes before the last verified run rather than exactly at it.
CATCHUP_MARGIN = 300


class _RunLockBusy(Exception):
    """Raised when another process holds the run lock."""


def _lock_path():
    return os.path.join(common.data_dir(), "sync.lock")


def _acquire_file_lock():
    """Take the exclusive inter-process lock, or raise _RunLockBusy.

    flock is used on purpose: the kernel drops it when the holder exits, so a
    killed run (or a device reboot mid-sync) cannot leave a stale lock behind
    that would block every later run.
    """
    if fcntl is None:
        log("no fcntl on this platform - inter-process lock skipped")
        return None
    os.makedirs(common.data_dir(), exist_ok=True)
    fd = os.open(_lock_path(), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise _RunLockBusy()
    # The pid is purely diagnostic: it names the holder in the lock file.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        pass
    return fd


def _release_file_lock():
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except OSError as e:
        log("could not release run lock: %s" % e)
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None


@contextlib.contextmanager
def _exclusive_run():
    """Hold the run lock for a complete run - threads and processes alike.

    Waits for other threads of this process, but never for another process:
    a run can take up to RUN_TIMEOUT, and both callers prefer skipping over
    blocking - the timer helper would only pile up, and the app would hang a
    worker thread. Raises _RunLockBusy instead.
    """
    global _lock_fd, _lock_depth
    with _run_lock:
        if _lock_depth == 0:
            _lock_fd = _acquire_file_lock()
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
            if _lock_depth == 0:
                _release_file_lock()


def _send(event, payload):
    if HAVE_PYOTHERSIDE:
        pyotherside.send(event, payload)
    else:
        log("(event) %s: %r" % (event, payload))


def _logs_dir():
    return os.path.join(common.data_dir(), "logs")


def _workdir():
    return os.path.join(common.data_dir(), "bisync")


def _filters_dir():
    return os.path.join(common.data_dir(), "filters")


def log_path(pair_id):
    return os.path.join(_logs_dir(), "%s.log" % pair_id)


def reset_state():
    """Drop the bisync working state, the filter files and the run logs.

    Called when the account is removed or switched: the stored listings
    describe the remote side of the old account, and bisync would act on that
    stale picture if a new pair ever reused the same paths.
    """
    removed = []
    for path in (_workdir(), _filters_dir(), _logs_dir()):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(os.path.basename(path))
    log("sync state reset (removed: %s)" % (removed or "nothing"))
    return removed


def _rotate_log(pair_id):
    """Keep the previous run's log as .prev."""
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
                # rclone repeats the target in its own messages.
                parts.append("===== %s =====\n%s"
                             % (os.path.basename(path),
                                common.mask_secrets(content)))
            except OSError as e:
                parts.append("log %s unreadable: %s" % (path, e))
    return "\n".join(parts) if parts else "No log yet."


def _modtime_supported():
    """Whether the configured backend carries modification times.

    bisync can only pick the newer file of a conflict if both sides have
    usable modtimes. Seafile has none, and rclone then drops the flag with a
    NOTICE on every run - so it is only passed where it does something. The
    account summary comes from the config cache, not from a fresh rclone
    call.
    """
    try:
        summary = config_manager.get_account_summary() or {}
        return backend_manager.supports_modtime(summary.get("backend_id"))
    except Exception as e:
        log("modtime capability unknown (%s) - assuming none" % e)
        return False


# rclone reads filter rules as globs, so every metacharacter in a file name
# has to be escaped: "notes*.txt" would otherwise match every notes file,
# "report[1].txt" would be read as a character class, and a lone brace
# makes rclone abort the run with "mismatched '{' and '}' in glob".
_GLOB_META = "\\*?[]{}"


def _escape_glob(name):
    """Turn a file name into a rule that matches exactly that name."""
    return "".join("\\" + c if c in _GLOB_META else c for c in name)


def _filters_content(pair):
    if pair["type"] == "file":
        filename = os.path.basename(pair["local"])
        if "\n" in filename or "\r" in filename:
            # One rule per line - a line break would split the name into
            # two rules, the second of which filters something else.
            raise ValueError("The file name contains a line break, which"
                             " cannot be expressed as a sync filter."
                             " Please rename the file.")
        return "+ /%s\n- **\n" % _escape_glob(filename)
    # user-editable global excludes. A change in this content changes
    # the filters hash and correctly forces a resync.
    excludes = settings_manager.get("excludes")
    return "".join("- %s\n" % pattern for pattern in excludes)


def _filters_path(pair_id):
    return os.path.join(_filters_dir(), "%s.txt" % pair_id)


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


def _push_passes(pair, args):
    """The rclone runs of a one-way pair, in the order they have to happen.

    A backend with modtimes needs one: rclone's default comparison (size and
    modification time) sees every change a backup has to carry.

    Without modtimes it needs two. Seafile has neither modtimes nor hashes,
    FTP has modtimes only where the server supports MFMT - all rclone can
    compare there is the file size, so an edit that leaves the byte length
    unchanged would never be uploaded: the backup would silently keep the old
    content while the run reports success. The local side does have modtimes,
    and the changes rclone cannot see are exactly the files touched since the
    last verified run - those go up first and unconditionally
    (--ignore-times), which leaves the new and size-changed files to the size
    pass. First is deliberate: afterwards the sizes match, so the second pass
    does not send the same file again.
    """
    if _modtime_supported():
        return [args]
    size_pass = args + ["--size-only"]
    catchup = args + ["--ignore-times"]
    if not pair.get("last_run"):
        # Nothing has been uploaded yet - the size pass sends the whole
        # folder anyway, and re-sending it right after would be pointless.
        log("backend without modtimes - first run, comparing by size")
        return [size_pass]
    try:
        age = int(time.time() - float(pair["last_verified"])) + CATCHUP_MARGIN
    except (KeyError, TypeError, ValueError):
        # A pair last synced by a version that only compared sizes: what sits
        # on the remote side cannot be trusted, so verify all of it once.
        log("backend without modtimes and no verified run - re-uploading the"
            " whole folder once")
        return [catchup, size_pass]
    age = max(age, CATCHUP_MARGIN)
    log("backend without modtimes - re-uploading files changed in the last"
        " %ds, then comparing by size" % age)
    return [catchup + ["--max-age", "%ds" % age], size_pass]


def _classify_failure(output, remote_path="", allow_resync=True):
    # A locked library is not a broken sync - the run never had a chance.
    # This also registers the library, so the next run is refused up front.
    library = enc_libraries.encrypted_library(output, remote_path)
    if library:
        return "locked", ("Library %r is encrypted - unlock it in the remote"
                          " browser, then sync again" % library)
    lowered = output.lower()
    if allow_resync and ("--resync" in lowered or "cannot find prior" in lowered
                         or "prior listing" in lowered
                         or "empty prior" in lowered):
        # One-way pairs have no prior listing to repair, so the flag would
        # only be set and never cleared again.
        return "needs_resync", "Sync state invalid - the next run will resync"
    if "too many deletes" in lowered or "max-delete" in lowered \
            or "safety abort" in lowered:
        return "safety_abort", ("Sync stopped: unusually many changes detected "
                                "(safety limit). Pair paused - review and sync "
                                "manually.")
    return "failed", config_manager.friendly_error(
        output, "Sync failed - open the log for details")


def run_pair(pair_id, force=False, check_network=True):
    """Run one pair in its configured mode. Blocking; call from a worker
    thread or via run_pair_async. Returns the updated pair dict."""
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
    try:
        with _exclusive_run():
            return _run_pair_locked(pair, force)
    except _RunLockBusy:
        # A run is already in progress here or in the timer helper; the
        # pair keeps its previous state - no error, no notification.
        log("pair %s not run: %s" % (pair_id, BUSY_REASON))
        _send("sync-status", {"pair": pair_id, "running": False,
                              "ok": None, "message": "Skipped: %s" % BUSY_REASON})
        return {"skipped": True, "message": BUSY_REASON}


def _run_pair_locked(pair, force):
    pair_id = pair["id"]
    mode = sync_pairs.pair_mode(pair)
    two_way = (mode == sync_pairs.MODE_BISYNC)
    _send("sync-status", {"pair": pair_id, "running": True, "message": "Syncing..."})
    log("=== sync run start: %s (%s %s) %s %s %s force=%s ==="
        % (pair_id, mode, pair["type"], pair["local"],
           "<->" if two_way else "->", pair["remote"], force))

    # An encrypted library without its key cannot be synced: bisync would
    # spend its retries on errors and leave half a listing behind.
    locked = enc_libraries.locked_library(pair["remote"])
    if locked:
        log("pair %s targets the locked library %r - not running"
            % (pair_id, locked))
        return _finish_run(pair_id,
                           datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                           False,
                           "Library %r is locked - enter its password in the"
                           " remote browser" % locked,
                           {"needs_resync": True})

    local_dir = pair["local"] if pair["type"] == "folder" \
        else os.path.dirname(pair["local"])
    # Encrypted-library aware target.
    remote_target = enc_libraries.build_target(config_manager.REMOTE_NAME,
                                               pair["remote"])

    try:
        filters_path, filters_hash, filters_changed = _prepare_filters(pair)
    except ValueError as e:
        # Without a usable rule the run would sync the wrong set of
        # files, so it does not start at all.
        log("pair %s has an unusable file name: %s" % (pair_id, e))
        return _finish_run(pair_id,
                           datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                           False, str(e), {})
    if two_way:
        resync = bool(pair.get("needs_resync")) or filters_changed
        if filters_changed:
            log("filters changed - forcing resync")
        args = ["bisync", remote_target, local_dir,
                "--size-only", "-v", "--stats", "0",
                "--stats-log-level", "NOTICE",
                "--conflict-loser", "num",
                "--max-delete", str(int(settings_manager.get("max_delete"))),
                "--workdir", _workdir(),
                "--filters-file", filters_path]
        if _modtime_supported():
            # the newer file wins, the older one stays as a numbered copy.
            args += ["--conflict-resolve", "newer"]
        else:
            # without modtimes there is no "newer" - both versions are
            # kept as conflict copies and the user is notified about them below.
            log("backend without modtimes - conflicts keep both versions")
        if resync:
            args.append("--resync")
            log("running with --resync (first run or recovery)")
        if force:
            args.append("--force")
            log("running with --force (single run only)")
        os.makedirs(_workdir(), exist_ok=True)
        passes = [args]
    else:
        # One-way upload. copy (not sync) is deliberate: it never deletes on
        # the remote side, which is what a backup pair promises. That also
        # makes the bisync apparatus pointless here - there is no prior
        # listing (--workdir, --resync), no conflict to resolve and nothing
        # for --max-delete to guard. A filter change needs no resync either,
        # only a fresh hash.
        # --filter-from, not bisync's --filters-file: that flag exists only
        # on the bisync subcommand, and copy aborts with "unknown flag".
        # The rule file itself is the same in both cases.
        args = ["copy", local_dir, remote_target,
                "-v", "--stats", "0", "--stats-log-level", "NOTICE",
                "--filter-from", filters_path]
        passes = _push_passes(pair, args)

    os.makedirs(_logs_dir(), exist_ok=True)
    _rotate_log(pair_id)

    started = time.time()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        commands = [config_manager.build_rclone_command(a) for a in passes]
    except RuntimeError as e:
        return _finish_run(pair_id, now, False, str(e), {"needs_resync": True})

    rc = 0
    try:
        with open(log_path(pair_id), "w", encoding="utf-8") as log_file:
            log_file.write("# ferry sync run %s\n" % now)
            for pass_args, (cmd, env) in zip(passes, commands):
                # The remote target may carry the library key - never write it.
                log_file.write("# %s\n"
                               % common.mask_secrets(" ".join(pass_args)))
                log_file.flush()
                # RUN_TIMEOUT is the budget of the whole run: two passes must
                # not be able to hold the run lock for twice as long.
                left = RUN_TIMEOUT - (time.time() - started)
                proc = subprocess.run(common.encode_cmd(cmd), stdout=log_file,
                                      stderr=subprocess.STDOUT,
                                      timeout=max(left, 1), env=env)
                rc = proc.returncode
                if rc != 0:
                    # The run is reported as failed either way, and a second
                    # pass on a broken connection only costs time.
                    break
    except subprocess.TimeoutExpired:
        return _finish_run(pair_id, now, False, "Sync timed out", {})
    except Exception as e:
        return _finish_run(pair_id, now, False, "Sync failed to start: %s" % e, {})

    with open(log_path(pair_id), encoding="utf-8", errors="replace") as f:
        output = f.read()
    log("%s finished rc=%d, log %d bytes" % (args[0], rc, len(output)))
    if rc != 0:
        # Without this the reason lives only in the per-pair log file, and a
        # failed run is just an exit code in the app log.
        log("%s said: %s" % (args[0], output[-900:].replace("\n", " | ")))

    if rc == 0:
        extra = {"filters_hash": filters_hash}
        if two_way:
            extra["needs_resync"] = False
            # Detect conflict copies created by --conflict-loser num and
            # notify the user (action required - review the copies).
            conflicts = sorted(set(re.findall(r"[^\s'\"]+\.conflict\d*", output)))
            if conflicts:
                names = ", ".join(os.path.basename(c) for c in conflicts[:5])
                message = "OK - %d conflict file(s): %s" % (len(conflicts), names)
                log("conflicts detected: %s" % names)
                notify.send("Ferry: sync conflict",
                            "%s - please review" % names)
                return _finish_run(pair_id, now, True, message, extra)
        else:
            # Everything local is on the remote side as of this moment, so
            # the next catch-up pass only has to look at what changed after
            # it. The run's start, not its end: a file written while rclone
            # was already listing may have been missed.
            extra["last_verified"] = int(started)
        return _finish_run(pair_id, now, True, "OK", extra)

    kind, message = _classify_failure(output, pair["remote"],
                                      allow_resync=two_way)
    extra = {"filters_hash": filters_hash}
    if kind in ("needs_resync", "locked"):
        # Nothing was written - the next attempt has to start over.
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
        # Notifications only on failure / action required.
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
    """Fire-and-forget run for the UI ('sync this pair')."""
    threading.Thread(target=run_pair, args=(pair_id, force),
                     name="ferry-sync").start()
    return True


def run_all_now():
    """Synchronous full run, honoring the network rule. Used by the timer helper and the async UI wrapper."""
    allowed, reason = network.allowed_by_rule(settings_manager.get("network_rule"))
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not allowed:
        # skip silently - banner in the app, no error, no notification.
        sync_pairs.set_last_skip("%s (%s)" % (reason, timestamp))
        _send("sync-all-finished", {"timestamp": timestamp,
                                    "skipped": True, "reason": reason})
        log("global sync run skipped: %s" % reason)
        return {"skipped": True, "reason": reason}

    try:
        # Held across all pairs so the other runner cannot slip between them
        # and rewrite the pair store while this run is still going.
        with _exclusive_run():
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
    except _RunLockBusy:
        # Same as the network skip: no error, no notification, try again at
        # the next timer tick.
        _send("sync-all-finished", {"timestamp": timestamp,
                                    "skipped": True, "reason": BUSY_REASON})
        log("global sync run skipped: %s" % BUSY_REASON)
        return {"skipped": True, "reason": BUSY_REASON}
    _send("sync-all-finished", {"timestamp": timestamp})
    log("global sync run finished")
    return {"skipped": False}


def run_all_async():
    threading.Thread(target=run_all_now, name="ferry-sync-all").start()
    return True
