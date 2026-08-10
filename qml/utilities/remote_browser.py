#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - remote browser backend (FR-05, FR-07, FR-08, FR-09).
# Lists remote directories via rclone lsjson, downloads to ~/Downloads with
# progress events, deletes remote entries and creates folders. Talks only
# to the generic remote name (AD-09d).
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import shutil
import subprocess
import threading

import common
import config_manager
import enc_libraries

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    HAVE_PYOTHERSIDE = False

log = common.make_logger("browser")

_transfers = {}          # transfer id -> current Popen
_cancelled = set()       # transfer ids cancelled by the user
_transfers_lock = threading.Lock()
_transfer_counter = [0]

_PERCENT_RE = re.compile(r"(\d{1,3})%")


def _new_transfer_id():
    with _transfers_lock:
        _transfer_counter[0] += 1
        return _transfer_counter[0]


def _is_cancelled(transfer_id):
    with _transfers_lock:
        return transfer_id in _cancelled


def _finish_transfer(transfer_id):
    """Remove bookkeeping; returns True if the transfer was cancelled."""
    with _transfers_lock:
        _transfers.pop(transfer_id, None)
        cancelled = transfer_id in _cancelled
        _cancelled.discard(transfer_id)
    return cancelled


def _stream_process(transfer_id, proc, info_prefix):
    """Read rclone stats output, emit progress events, return exit code."""
    try:
        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            match = _PERCENT_RE.search(line)
            if match:
                percent = min(100, int(match.group(1)))
                _send("transfer-progress",
                      {"id": transfer_id, "percent": percent,
                       "info": (info_prefix + " " + line)[:140]})
        return proc.wait()
    except Exception as e:
        log("transfer %d: reader error: %s" % (transfer_id, e))
        return -1


def _send(event, payload):
    if HAVE_PYOTHERSIDE:
        pyotherside.send(event, payload)
    else:
        log("(event) %s: %r" % (event, payload))


def _remote_target(path):
    # Routes paths inside known encrypted libraries via connection string
    # (FR-04); plain paths become "ferry:path".
    return enc_libraries.build_target(config_manager.REMOTE_NAME, path)


def _join(path, name):
    path = (path or "").strip("/")
    return "%s/%s" % (path, name) if path else name


def list_dir(path=""):
    """List one remote directory level. Returns {ok, entries | message}."""
    target = _remote_target(path)
    log("listing %s" % target)
    rc, out = config_manager.run_rclone(["lsjson", target], timeout=90,
                                        log_args=False)
    if rc != 0:
        lowered = out.lower()
        library = (path or "").strip("/").split("/")[0]
        if "encrypt" in lowered or "password" in lowered:
            # FR-04: encrypted library - the UI asks for the password.
            log("library %r appears to be encrypted (rc=%d): %s"
                % (library, rc, out[:300]))
            return {"ok": False, "encrypted": True, "library": library,
                    "message": "Library is encrypted - password required"}
        message = config_manager.friendly_error(out)
        log("listing failed (rc=%d): %s" % (rc, out[:400]))
        return {"ok": False, "message": message, "details": out[:400]}
    start, end = out.find("["), out.rfind("]")
    raw = []
    if 0 <= start < end:
        try:
            raw = json.loads(out[start:end + 1])
        except ValueError:
            log("could not parse lsjson output")
            return {"ok": False, "message": "Could not parse the server response",
                    "details": out[:400]}
    at_root = not (path or "").strip("/")
    entries = []
    for item in raw:
        name = item.get("Name", "?")
        entries.append({
            "name": name,
            "path": _join(path, name),
            "is_dir": bool(item.get("IsDir")),
            "size": item.get("Size", -1),
            "mtime": (item.get("ModTime") or "")[:16].replace("T", " "),
            # Lock marker for known encrypted libraries (section 5.1).
            "encrypted": at_root and enc_libraries.is_encrypted(name),
        })
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    log("listing OK: %d entries" % len(entries))
    return {"ok": True, "entries": entries}


def unlock_library(library, password):
    """Verify an encrypted library's password, then keep the (obscured)
    key in Sailfish Secrets (FR-04). Returns {ok, message}."""
    if not password:
        return {"ok": False, "message": "Password must not be empty"}
    rc, out = config_manager.run_rclone(["obscure", password], timeout=15,
                                        log_args=False)
    if rc != 0 or not out:
        return {"ok": False, "message": "Could not process the password"}
    obscured = out.strip().splitlines()[-1]
    probe = "%s,library=%s,library_key=%s:" % (
        config_manager.REMOTE_NAME,
        enc_libraries._quote(library), enc_libraries._quote(obscured))
    log("probing encrypted library %r" % library)
    rc, out = config_manager.run_rclone(["lsjson", probe], timeout=90,
                                        log_args=False)
    if rc != 0:
        log("library unlock failed (rc=%d): %s" % (rc, out[:300]))
        return {"ok": False,
                "message": "Could not open the library - wrong password?"}
    enc_libraries.store_key(library, obscured)
    return {"ok": True, "message": "Library unlocked"}


def make_dir(path, name):
    """Create a folder (a library when created at root)."""
    name = (name or "").strip().strip("/")
    if not name:
        return {"ok": False, "message": "Folder name must not be empty"}
    target = _remote_target(_join(path, name))
    log("mkdir %s" % target)
    rc, out = config_manager.run_rclone(["mkdir", target], timeout=60)
    if rc != 0:
        return {"ok": False, "message": config_manager.friendly_error(out),
                "details": out[:400]}
    return {"ok": True, "message": "Folder created"}


def delete_entry(path, is_dir):
    """Delete a remote file or folder (UI guards this with a remorse timer,
    FR-08)."""
    target = _remote_target(path)
    cmd = ["purge", target] if is_dir else ["deletefile", target]
    log("deleting %s (dir=%s)" % (target, is_dir))
    rc, out = config_manager.run_rclone(cmd, timeout=300)
    if rc != 0:
        return {"ok": False, "message": config_manager.friendly_error(out),
                "details": out[:400]}
    return {"ok": True, "message": "Deleted"}


def _downloads_dir():
    return os.path.join(common.home_dir(), "Downloads")


_STATS_ARGS = ["--stats", "1s", "--stats-one-line", "--log-level", "NOTICE"]


def download(path, name, is_dir):
    """Start an async download to ~/Downloads (FR-07). Progress and
    completion are reported via 'transfer-progress'/'transfer-finished'
    events; returns {ok, id}."""
    source = _remote_target(path)
    destination = os.path.join(_downloads_dir(), name)
    verb = "copy" if is_dir else "copyto"
    try:
        cmd, env = config_manager.build_rclone_command(
            [verb, source, destination] + _STATS_ARGS)
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}

    transfer_id = _new_transfer_id()
    log("transfer %d: starting download %s -> %s" % (transfer_id, source, destination))
    try:
        proc = subprocess.Popen(common.encode_cmd(cmd), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
    except Exception as e:
        log("transfer %d: start failed: %s" % (transfer_id, e))
        return {"ok": False, "message": "Could not start the download: %s" % e}

    with _transfers_lock:
        _transfers[transfer_id] = proc

    def worker():
        rc = _stream_process(transfer_id, proc, name)
        cancelled = _finish_transfer(transfer_id)
        if cancelled:
            message = "Download cancelled"
        elif rc == 0:
            message = "Saved to Downloads: %s" % name
        else:
            message = "Download failed: %s" % name
        log("transfer %d: finished rc=%s cancelled=%s" % (transfer_id, rc, cancelled))
        _send("transfer-finished",
              {"id": transfer_id, "ok": rc == 0 and not cancelled, "message": message})

    threading.Thread(target=worker, name="ferry-transfer-%d" % transfer_id).start()
    return {"ok": True, "id": transfer_id, "message": "Download started"}


def upload(local_paths, remote_dir):
    """Upload local files into the current remote directory (FR-06).

    Files are transferred sequentially in one background job; progress
    events carry 'name (i/n)' info. Returns {ok, id}.
    """
    paths = [p for p in (local_paths or []) if os.path.isfile(p)]
    if not paths:
        return {"ok": False, "message": "No files selected"}
    transfer_id = _new_transfer_id()
    with _transfers_lock:
        _transfers[transfer_id] = None
    log("transfer %d: starting upload of %d file(s) to %r"
        % (transfer_id, len(paths), remote_dir))

    def worker():
        failed = []
        total = len(paths)
        for index, local_path in enumerate(paths):
            if _is_cancelled(transfer_id):
                break
            name = os.path.basename(local_path)
            destination = _remote_target(_join(remote_dir, name))
            log("transfer %d: uploading %s -> %s" % (transfer_id, local_path, destination))
            try:
                cmd, env = config_manager.build_rclone_command(
                    ["copyto", local_path, destination] + _STATS_ARGS)
                proc = subprocess.Popen(common.encode_cmd(cmd), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env)
            except Exception as e:
                log("transfer %d: start failed for %s: %s" % (transfer_id, name, e))
                failed.append(name)
                continue
            with _transfers_lock:
                if transfer_id in _cancelled:
                    proc.terminate()
                    break
                _transfers[transfer_id] = proc
            _send("transfer-progress",
                  {"id": transfer_id, "percent": 0,
                   "info": "%s (%d/%d)" % (name, index + 1, total)})
            rc = _stream_process(transfer_id, proc,
                                 "%s (%d/%d)" % (name, index + 1, total))
            if rc != 0 and not _is_cancelled(transfer_id):
                failed.append(name)
        cancelled = _finish_transfer(transfer_id)
        if cancelled:
            message = "Upload cancelled"
            ok = False
        elif failed:
            message = "Upload failed for: %s" % ", ".join(failed[:5])
            ok = False
        else:
            message = "Uploaded %d file(s)" % total
            ok = True
        log("transfer %d: upload finished ok=%s failed=%s cancelled=%s"
            % (transfer_id, ok, failed, cancelled))
        _send("transfer-finished", {"id": transfer_id, "ok": ok, "message": message})

    threading.Thread(target=worker, name="ferry-upload-%d" % transfer_id).start()
    return {"ok": True, "id": transfer_id, "message": "Upload started"}


# --- text viewing/editing and image viewing (owner request, M3.1) --------

TEXT_MAX_BYTES = 512 * 1024


def _run_capture(args, timeout=180, input_bytes=None):
    """Run rclone with SEPARATE stdout/stderr capture (rclone cat content
    must not be polluted by NOTICE log lines). Returns (rc, stdout_bytes,
    stderr_text)."""
    try:
        cmd, env = config_manager.build_rclone_command(args)
    except RuntimeError as e:
        return -2, b"", str(e)
    log("exec (capture): %s" % " ".join(args[:2]))
    try:
        proc = subprocess.run(common.encode_cmd(cmd),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              input=input_bytes, timeout=timeout, env=env)
        return proc.returncode, proc.stdout or b"", \
            (proc.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, b"", "TIMEOUT after %ss" % timeout
    except Exception as e:
        return -3, b"", "ERROR: %s" % e


def read_text_file(path):
    """Fetch a text file's content for viewing/editing (size-limited)."""
    target = _remote_target(path)
    log("reading text file %s" % target)
    rc, data, err = _run_capture(["cat", "--count", str(TEXT_MAX_BYTES + 1), target])
    if rc != 0:
        log("read failed (rc=%d): %s" % (rc, err[:300]))
        return {"ok": False, "message": config_manager.friendly_error(err)}
    if len(data) > TEXT_MAX_BYTES:
        return {"ok": False,
                "message": "File is too large to open here (max 512 KB)"}
    log("read %d bytes" % len(data))
    return {"ok": True, "content": data.decode("utf-8", "replace")}


def save_text_file(path, content):
    """Write edited text back to the remote file (rclone rcat)."""
    target = _remote_target(path)
    data = content.encode("utf-8")
    log("saving text file %s (%d bytes)" % (target, len(data)))
    rc, _, err = _run_capture(["rcat", target], input_bytes=data)
    if rc != 0:
        log("save failed (rc=%d): %s" % (rc, err[:300]))
        return {"ok": False, "message": config_manager.friendly_error(err)}
    return {"ok": True, "message": "Saved"}


def _view_cache_dir():
    return os.path.join(common.data_dir(), "view-cache")


def fetch_image(path, name):
    """Download an image into the private view cache and return its local
    path for the QML image viewer. The cache holds one file at a time."""
    cache = _view_cache_dir()
    try:
        shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)
    except OSError as e:
        return {"ok": False, "message": "Cache not writable: %s" % e}
    local_path = os.path.join(cache, name)
    target = _remote_target(path)
    log("fetching image %s -> %s" % (target, local_path))
    rc, _, err = _run_capture(["copyto", target, local_path], timeout=300)
    if rc != 0 or not os.path.exists(local_path):
        log("image fetch failed (rc=%d): %s" % (rc, err[:300]))
        return {"ok": False, "message": config_manager.friendly_error(err)}
    return {"ok": True, "local_path": local_path}


def cancel_transfer(transfer_id):
    """Cancel a running transfer (FR-09)."""
    with _transfers_lock:
        known = transfer_id in _transfers
        proc = _transfers.get(transfer_id)
        if known:
            _cancelled.add(transfer_id)
    if not known:
        log("cancel: transfer %d not found" % transfer_id)
        return False
    log("cancel: terminating transfer %d" % transfer_id)
    try:
        if proc is not None:
            proc.terminate()
    except Exception as e:
        log("cancel failed: %s" % e)
    return True
