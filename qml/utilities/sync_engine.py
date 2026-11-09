#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - sync engine.
# Three modes per sync pair (see sync_pairs.pair_mode):
#   "bisync" - rclone bisync with the flag set from the requirements:
#     --size-only -v --stats 0 --stats-log-level NOTICE
#     --conflict-loser num, plus --conflict-resolve newer where the
#     backend has modification times (see _modtime_supported)
#     --resync on first run / after state errors
#     --max-delete 50 (folder pairs; single-file pairs run with --force
#     instead, see _run_pair_locked)
#   "push"   - one-way rclone copy local -> remote. copy never deletes on
#     the destination, so none of the bisync safety machinery applies.
#     Backends without modtimes need a second rclone pass to see changes
#     that keep the file size - see _push_passes().
#   "pull"   - one-way rclone copy remote -> local, never deleting locally.
#     Without modtimes it can only compare sizes - see _pull_pass().
# Single-file pairs sync the parent folder with an include filter (bisync
# only - push and pull always cover a whole folder).
# Runs are strictly serial. Logs go to per-pair files.
#
# SPDX-License-Identifier: Apache-2.0

import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import signal
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
import transfer_list

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    HAVE_PYOTHERSIDE = False

log = common.make_logger("sync")

RUN_TIMEOUT = 3600

# How long a run that hit RUN_TIMEOUT gets to stop by itself. rclone answers
# SIGINT with a graceful shutdown - bisync finishes the step it is in, saves
# its listings and removes its lock file. SIGKILL (what subprocess.run's own
# timeout sends) skips all of that and leaves a lock behind that blocks every
# later run of the pair.
STOP_GRACE = 120

# bisync's --max-lock. Without it a lock never expires (rclone writes an
# expiry ~200 years ahead), so one interrupted run - app closed or killed,
# device switched off, timeout - blocks the pair until the file is deleted by
# hand. A running bisync renews its lock while it works, so a long run is not
# cut short by this: only a lock nobody renews any more runs out.
MAX_LOCK = "15m"

# Budget of the reachability probe in front of a resync (_remote_listable).
# A flat listing of one folder answers in seconds; anything slower means the
# connection is not in a state to carry a resync either.
PROBE_TIMEOUT = 60

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


# Without pyotherside the UI events are logged instead - useful on a dev
# desktop, noise in the background helper, whose output already has the
# run start/end lines and its own result lines. The helper switches it off.
LOG_EVENTS = True


def _send(event, payload):
    if HAVE_PYOTHERSIDE:
        pyotherside.send(event, payload)
    elif LOG_EVENTS:
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
    # The transfer lists describe the old account's destination as well.
    if os.path.isdir(transfer_list.list_dir()):
        transfer_list.delete_all()
        removed.append(os.path.basename(transfer_list.list_dir()))
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


def _pull_pass(args):
    """The rclone run of a download-only pair.

    With modtimes, rclone's default comparison (size and modification time)
    sees every remote change. Without them only the size is left, and
    unlike an upload there is no way around it: the catch-up trick of
    _push_passes() relies on the local modtimes of the *source*, and here
    the source is the remote, which has none. An edit on the remote side
    that keeps the byte length is therefore not downloaded - the same gap a
    two-way pair has on such a backend. --size-only only states it
    explicitly; rclone would fall back to it anyway.
    """
    if _modtime_supported():
        return args
    log("backend without modtimes - download compares by size")
    return args + ["--size-only"]


# Up to this many files a run with a transfer list looks each one up on the
# destination (--no-traverse) instead of listing the destination folder: a
# handful of new photos against a folder of thousands is answered in seconds
# instead of minutes. A first run, a reset or a big batch lists as usual.
NO_TRAVERSE_MAX = 500

# Sigils of rclone's --combined report that mean "the destination now has
# this file": = identical already, + was missing and was copied, * differed
# and was copied. A failed copy is reported a second time with "!".
_REPORT_DONE = "=+*"
_REPORT_FAILED = "!"


def _rclone_list(path, filters_path, log_file, timeout):
    """Recursive file listing of one side as (rc, {relative path: size}).

    rclone's messages go to the run log; the listing itself is parsed. The
    same rclone filter file as the copy applies, so both sides see the same
    set of files.
    """
    args = ["lsjson", path, "-R", "--files-only", "--no-modtime",
            "--no-mimetype", "--filter-from", filters_path]
    log_file.write("# %s\n" % common.mask_secrets(" ".join(args)))
    log_file.flush()
    cmd, env = config_manager.build_rclone_command(args)
    # lsjson takes no lock, so the hard kill of run()'s timeout is harmless.
    proc = subprocess.run(common.encode_cmd(cmd), env=env,
                          stdout=subprocess.PIPE, stderr=log_file,
                          timeout=max(timeout, 1))
    if proc.returncode != 0:
        return proc.returncode, None
    try:
        entries = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError as e:
        log_file.write("# listing unreadable: %s\n" % e)
        return 1, None
    return 0, {e["Path"]: e.get("Size", -1) for e in entries
               if isinstance(e, dict) and "Path" in e}


def _transfer_once(pair, src, dst, base_args, filters_path, log_file,
                   deadline):
    """One-way copy that transfers every file only once (skip_transferred).

    The source side is listed and compared against the pair's transfer list
    (transfer_list): only files that are not in it, or whose size changed
    since, go to rclone - via --files-from-raw, so the destination is not
    searched for anything else. A file deleted on the destination therefore
    stays deleted. Source files that are gone drop out of the list, so a
    file that comes back later counts as new.

    Without a list (first run, reset) every source file is a candidate and
    rclone compares them against the destination as usual: what is there
    already is only recorded, what is missing is copied.

    The list is updated from rclone's --combined report, not from the exit
    code: a run that fails half way still records every file that made it,
    and a file that failed stays a candidate. Always --size-only - "path and
    size" is what the list stores, and comparing modtimes on top would copy
    a file again that the list says is done.

    Returns rclone's exit code.
    """
    pair_id = pair["id"]
    rc, source = _rclone_list(src, filters_path, log_file,
                              deadline - time.time())
    if rc != 0:
        # An incomplete listing must not thin out the list.
        return rc
    known = transfer_list.load(pair_id)
    first = known is None
    done = {path: size for path, size in (known or {}).items()
            if path in source}
    dropped = len(known or {}) - len(done)
    candidates = []
    for path, size in sorted(source.items()):
        if done.get(path) == size:
            continue
        if "\n" in path or "\r" in path:
            # One path per line in the file list - such a name cannot be
            # expressed there.
            log_file.write("# skipped, line break in name: %r\n" % path)
            continue
        candidates.append(path)
    log_file.write("# transfer list: %s, %d source file(s), %d already"
                   " transferred, %d to check, %d dropped\n"
                   % ("none yet - comparing with the destination" if first
                      else "%d entries" % len(known),
                      len(source), len(done), len(candidates), dropped))
    if not candidates:
        transfer_list.save(pair_id, done)
        log_file.write("# nothing new to transfer\n")
        return 0

    os.makedirs(transfer_list.tmp_dir(), exist_ok=True)
    files_from = os.path.join(transfer_list.tmp_dir(), "%s.files" % pair_id)
    report = os.path.join(transfer_list.tmp_dir(), "%s.report" % pair_id)
    with open(files_from, "w", encoding="utf-8") as f:
        f.write("".join("%s\n" % path for path in candidates))
    if os.path.exists(report):
        os.remove(report)
    args = ["copy", src, dst] + base_args + [
        "--size-only", "--files-from-raw", files_from, "--combined", report]
    if not first and len(candidates) <= NO_TRAVERSE_MAX:
        args.append("--no-traverse")
    log_file.write("# %s\n" % common.mask_secrets(" ".join(args)))
    log_file.flush()
    cmd, env = config_manager.build_rclone_command(args)
    rc = None
    try:
        rc = _run_rclone(common.encode_cmd(cmd), log_file, env,
                         max(deadline - time.time(), 1))
    finally:
        # Also after a timeout: what made it is recorded either way.
        recorded = _record_report(report, source, done)
        transfer_list.save(pair_id, done)
        log_file.write("# transfer list updated: %d file(s) recorded, %d"
                       " entries\n" % (recorded, len(done)))
        for path in (files_from, report):
            try:
                os.remove(path)
            except OSError:
                pass
    return rc


def _record_report(report, source, done):
    """Add every file rclone reports as present on the destination to done.

    Returns how many were added. A file reported as failed is left out even
    if it was also reported as copied.
    """
    ok, failed = set(), set()
    try:
        with open(report, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if len(line) < 3 or line[1] != " ":
                    continue
                sigil, path = line[0], line[2:]
                if sigil in _REPORT_DONE:
                    ok.add(path)
                elif sigil in _REPORT_FAILED:
                    failed.add(path)
    except FileNotFoundError:
        return 0
    added = 0
    for path in ok - failed:
        if path in source:
            done[path] = source[path]
            added += 1
    return added


def _run_rclone(cmd, log_file, env, timeout):
    """Run one rclone pass and return its exit code.

    Like subprocess.run(timeout=...), but a run that overstays is first asked
    to stop (SIGINT, see STOP_GRACE) and only killed when it does not.
    Raises subprocess.TimeoutExpired either way.
    """
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            env=env)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log("rclone pid %d over time - asking it to stop" % proc.pid)
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            log("rclone pid %d did not stop - killing it" % proc.pid)
            proc.kill()
            proc.wait()
        raise
    except BaseException:
        # The caller is going away: do not leave an orphaned rclone behind.
        proc.kill()
        proc.wait()
        raise


# rclone colours its output; the lock path sits between escape sequences.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_LOCK_FOUND = re.compile(r"prior lock file found:\s*(\S+?\.lck)")


def _lock_files_in_log(log_text):
    """The bisync lock files the pair's last run was blocked by.

    Taken from rclone's own message rather than rebuilt from the pair's
    paths: the session name rclone derives from both sides is its internal
    business (and differs for encrypted libraries). Only .lck files directly
    inside Ferry's own working directory qualify - nothing else is deleted.
    """
    workdir = os.path.realpath(_workdir())
    found = []
    for path in _LOCK_FOUND.findall(_ANSI.sub("", log_text)):
        real = os.path.realpath(path)
        if os.path.dirname(real) == workdir and real not in found:
            found.append(real)
    return found


def delete_lock_file(pair_id):
    """Remove the stale bisync lock that blocks a pair (context menu).

    Taken under the run lock: while Ferry itself runs a sync, the lock may
    well be a live one, and the user is told so instead.
    """
    if sync_pairs.get_pair(pair_id) is None:
        return {"ok": False, "message": "Pair not found"}
    removed = 0
    try:
        with _exclusive_run():
            try:
                with open(log_path(pair_id), encoding="utf-8",
                          errors="replace") as f:
                    paths = _lock_files_in_log(f.read())
            except OSError:
                paths = []
            for path in paths:
                try:
                    os.remove(path)
                    removed += 1
                    log("pair %s: deleted lock file %s" % (pair_id, path))
                except FileNotFoundError:
                    log("pair %s: lock file %s already gone" % (pair_id, path))
            sync_pairs.update_pair(pair_id, {
                "lock_blocked": False,
                "last_message": "Lock file deleted - sync again" if removed
                                else "No lock file left - sync again"})
    except _RunLockBusy:
        return {"ok": False, "busy": True, "removed": 0,
                "message": BUSY_REASON}
    return {"ok": True, "busy": False, "removed": removed,
            "message": "Lock file deleted" if removed else "No lock file found"}


def reset_transfer_list(pair_id):
    """Forget which files a one-way pair already transferred (context menu).

    The next run compares every file against the destination again and
    transfers what is missing there. Taken under the run lock: a run in
    progress would write its list back right after.
    """
    if sync_pairs.get_pair(pair_id) is None:
        return {"ok": False, "busy": False, "message": "Pair not found"}
    try:
        with _exclusive_run():
            transfer_list.delete(pair_id)
            sync_pairs.update_pair(pair_id, {
                "last_message": "Transfer list reset - missing files are"
                                " transferred at the next sync"})
    except _RunLockBusy:
        return {"ok": False, "busy": True, "message": BUSY_REASON}
    return {"ok": True, "busy": False, "message": "Transfer list reset"}


def _rclone_output(log_text):
    """The run log without the header lines this module writes itself.

    The header quotes the command, so every two-way run carries the
    literal "--max-delete" and every recovery run "--resync" - the very
    words the classification below looks for. Matching them against the
    header turned *every* failed bisync into a safety abort: a server
    that could not be reached paused the pair for "unusually many
    changes", and the pair then waited for a confirmation the user had no
    reason to give. Only rclone's own lines may say what a failure was.
    """
    return "\n".join(line for line in log_text.splitlines()
                      if not line.startswith("#"))


def _remote_listable(remote_target):
    """Quick check that the remote answers, run in front of a resync.

    A resync is a union copy: if the server drops out while it lists the
    remote side, every local file looks new and is uploaded again. A flat
    lsf of the target costs a second or two and keeps a resync from starting
    into a connection that is already down (router reconnect, no Wi-Fi yet).
    It cannot rule out a drop *during* the resync - --resilient/--recover
    keep that from ending in yet another resync.

    Returns (ok, output). Only an unreachable server or a probe that runs
    out of time counts as a failure; anything else (e.g. the remote folder
    does not exist yet on a first run) is left to bisync to report.
    """
    cmd, env = config_manager.build_rclone_command(
        ["lsf", remote_target, "--max-depth", "1", "--low-level-retries", "2"])
    try:
        proc = subprocess.run(common.encode_cmd(cmd), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        # lsf takes no lock, so killing it (what run() does) is harmless.
        return False, "listing probe timed out after %ds" % PROBE_TIMEOUT
    output = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0 and config_manager.looks_unreachable(output):
        return False, output
    return True, output


def _classify_failure(log_text, remote_path="", allow_resync=True):
    output = _rclone_output(log_text)
    if "prior lock file found" in output.lower():
        # A lock left by an earlier run that never finished. A resync does
        # not fix it and retrying will not either until the lock is gone.
        return "lock_blocked", ("Blocked by the lock file of an interrupted"
                                " run - long-press and delete the lock file")
    # A locked library is not a broken sync - the run never had a chance.
    # This also registers the library, so the next run is refused up front.
    library = enc_libraries.encrypted_library(output, remote_path)
    if library:
        return "locked", ("Library %r is encrypted - unlock it in the remote"
                          " browser, then sync again" % library)
    lowered = output.lower()
    # Only rclone's own verdict counts. A bare "--resync" also matches
    # "Error is retryable without --resync due to --resilient mode", i.e.
    # exactly the case where no resync is needed.
    if allow_resync and ("must run --resync" in lowered
                         or "run --resync to recover" in lowered
                         or "cannot find prior" in lowered
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
    if config_manager.looks_unreachable(lowered):
        # The run never got to the server, so no listing was compared and
        # nothing is out of step - there is nothing here for the user to
        # review or repair. Its own kind, so the pair keeps running and
        # the message can say what actually happens next.
        return "unreachable", ("Server not reachable - Ferry will try"
                               " again at the next sync")
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
    log("=== sync run start: %s %r (%s %s) %s %s %s force=%s ==="
        % (pair_id, sync_pairs.display_name(pair), mode, pair["type"],
           pair["local"],
           sync_pairs.mode_arrow(mode), pair["remote"], force))

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
        # bisync's safety checks are ratios over the files in scope, and a
        # single-file pair has exactly one: every edit is "all files were
        # changed", a delete is 100% deletes. Both would abort and pause
        # the pair on every ordinary change, so they are switched off for
        # file pairs - the include filter keeps --force to that one file.
        single_file = pair["type"] == "file"
        args = ["bisync", remote_target, local_dir,
                "--size-only", "-v", "--stats", "0",
                "--stats-log-level", "NOTICE",
                "--conflict-loser", "num",
                "--workdir", _workdir(),
                "--filters-file", filters_path,
                "--max-lock", MAX_LOCK,
                # A dropped connection (router reconnect, flaky Wi-Fi) must
                # not end in "Must run --resync": a resync is a union copy,
                # and if its listing of the remote fails mid-way every local
                # file looks new and is uploaded again. --resilient makes such
                # errors retryable on the next normal run, --recover lets an
                # interrupted run (timeout, SIGINT) continue without a resync.
                "--resilient", "--recover"]
        if not single_file:
            args += ["--max-delete",
                     str(int(settings_manager.get("max_delete")))]
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
        if single_file:
            args.append("--force")
            log("single-file pair - running with --force, no safety ratios")
        elif force:
            args.append("--force")
            log("running with --force (single run only)")
        os.makedirs(_workdir(), exist_ok=True)
        passes = [args]
    elif mode == sync_pairs.MODE_PULL:
        # One-way download - the mirror image of the upload below: copy
        # never deletes on the local side, there is no prior listing and no
        # conflict. A file that differs is overwritten with the remote
        # version, local edits included
        args = ["copy", remote_target, local_dir,
                "-v", "--stats", "0", "--stats-log-level", "NOTICE",
                "--filter-from", filters_path]
        passes = [_pull_pass(args)]
        once = (remote_target, local_dir)
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
        once = (local_dir, remote_target)
    if two_way or not pair.get("skip_transferred"):
        once = None
    else:
        # _transfer_once() builds its own rclone runs; the passes above are
        # only the plain copy it replaces.
        passes = []
        log("transfer list in use - files already transferred are skipped")

    os.makedirs(_logs_dir(), exist_ok=True)
    _rotate_log(pair_id)

    started = time.time()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        commands = [config_manager.build_rclone_command(a) for a in passes]
    except RuntimeError as e:
        return _finish_run(pair_id, now, False, str(e), {"needs_resync": True})

    if two_way and resync:
        try:
            reachable, probe_output = _remote_listable(remote_target)
        except Exception as e:
            reachable, probe_output = False, "listing probe failed: %s" % e
        if not reachable:
            # needs_resync stays set, so the next run probes and tries again.
            log("pair %s: resync not started - remote listing probe failed: %s"
                % (pair_id, probe_output[-400:].replace("\n", " | ")))
            with open(log_path(pair_id), "w", encoding="utf-8") as log_file:
                log_file.write("# ferry sync run %s\n" % now)
                log_file.write("# resync not started - remote listing probe"
                               " failed\n")
                log_file.write(common.mask_secrets(probe_output))
            return _finish_run(pair_id, now, False,
                               "Server not reachable - Ferry will try"
                               " again at the next sync",
                               # filters_hash is left alone on purpose: a
                               # filter change still forces the resync.
                               {"unreachable": True})

    rc = 0
    try:
        with open(log_path(pair_id), "w", encoding="utf-8") as log_file:
            log_file.write("# ferry sync run %s\n" % now)
            if once:
                # The filter is applied by the listing; the copy itself gets
                # an explicit file list and only the shared output flags.
                rc = _transfer_once(pair, once[0], once[1],
                                    ["-v", "--stats", "0",
                                     "--stats-log-level", "NOTICE"],
                                    filters_path, log_file,
                                    started + RUN_TIMEOUT)
            for pass_args, (cmd, env) in zip(passes, commands):
                # The remote target may carry the library key - never write it.
                log_file.write("# %s\n"
                               % common.mask_secrets(" ".join(pass_args)))
                log_file.flush()
                # RUN_TIMEOUT is the budget of the whole run: two passes must
                # not be able to hold the run lock for twice as long.
                left = RUN_TIMEOUT - (time.time() - started)
                rc = _run_rclone(common.encode_cmd(cmd), log_file, env,
                                 max(left, 1))
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
        elif mode == sync_pairs.MODE_PUSH:
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
    elif kind == "unreachable":
        # Not a state the pair has to be rescued from - only a
        # note that this run already reported the outage, so the
        # ones that follow it can stay quiet.
        extra["unreachable"] = True
    elif kind == "lock_blocked":
        # Shows the "Delete lock file" menu entry; announced once like an
        # outage, not on every timer tick.
        extra["lock_blocked"] = True
    return _finish_run(pair_id, now, False, message, extra)


def _finish_run(pair_id, timestamp, ok, message, extra_fields):
    previous = sync_pairs.get_pair(pair_id) or {}
    unreachable = bool(extra_fields.get("unreachable"))
    lock_blocked = bool(extra_fields.get("lock_blocked"))
    fields = {"last_run": timestamp, "last_ok": ok, "last_message": message,
              # Every other outcome clears it, so the next outage is
              # announced again rather than swallowed.
              "unreachable": unreachable,
              "lock_blocked": lock_blocked}
    if ok:
        fields["safety_abort"] = False
    fields.update(extra_fields)
    pair = sync_pairs.update_pair(pair_id, fields)
    log("=== sync run end: %s ok=%s message=%s ===" % (pair_id, ok, message))
    if not ok:
        # Notifications only on failure / action required.
        name = sync_pairs.display_name(pair) if pair else pair_id
        if extra_fields.get("safety_abort"):
            notify.send("Ferry: sync stopped", "%s: unusually many changes "
                        "- confirmation required" % name)
        elif unreachable:
            # A server that stays away is one event, not one per
            # scheduled run. The pair retries by itself, so
            # repeating this every few minutes would say nothing
            # new and only teach the user to swipe it away. The
            # first run that cannot reach the server reports it;
            # the rest wait for the state to change.
            if previous.get("unreachable"):
                log("pair %s still unreachable - already notified"
                    % pair_id)
            else:
                notify.send("Ferry: sync failed",
                            "%s: %s" % (name, message))
        elif lock_blocked and previous.get("lock_blocked"):
            log("pair %s still blocked by a lock file - already notified"
                % pair_id)
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

    # One entry per pair for the command line helper, which turns them into
    # its output and exit code. The UI ignores the return value.
    results = []
    try:
        # Held across all pairs so the other runner cannot slip between them
        # and rewrite the pair store while this run is still going.
        with _exclusive_run():
            pairs = sync_pairs.list_pairs()
            log("global sync run: %d pair(s)" % len(pairs))
            for pair in pairs:
                if pair.get("paused"):
                    log("skipping paused pair %s" % pair["id"])
                    results.append(pair_result(pair, {"paused": True}))
                    continue
                results.append(pair_result(
                    pair, run_pair(pair["id"], check_network=False)))
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
    return {"skipped": False, "results": results}


def pair_result(pair, outcome):
    """Condense what run_pair() returned into {id, name, status, message}.

    run_pair() answers with the updated pair after a run, but with a bare
    {"skipped"/"ok", "message"} dict when it never got to run - status
    tells the cases apart: "ok", "failed", "skipped" or "paused".
    """
    outcome = outcome or {}
    if outcome.get("paused"):
        status, message = "paused", "Paused"
    elif outcome.get("skipped"):
        status, message = "skipped", outcome.get("message", "")
    elif "last_ok" in outcome:
        status = "ok" if outcome.get("last_ok") else "failed"
        message = outcome.get("last_message", "")
    else:
        status = "ok" if outcome.get("ok") else "failed"
        message = outcome.get("message", "")
    return {"id": pair.get("id", ""), "name": sync_pairs.display_name(pair),
            "status": status, "message": message}


def run_all_async():
    threading.Thread(target=run_all_now, name="ferry-sync-all").start()
    return True
