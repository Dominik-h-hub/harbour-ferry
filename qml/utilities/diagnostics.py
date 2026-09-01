#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - M0 walking skeleton diagnostics (TS-00).
# Runs the validation spike tests in the real target environment and reports
# PASS/FAIL/SKIP per test to the QML DiagnosticsPage (FR-21).
#
# Copyright (C) 2026 Ferry contributors
# SPDX-License-Identifier: Apache-2.0

import datetime
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    # Allows running this module standalone on a desktop for a smoke test.
    HAVE_PYOTHERSIDE = False

APP_NAME = "harbour-ferry"

try:
    import backend_manager
except ImportError:
    # A standalone run outside the app tree: the account test reports the
    # missing module and every other test stays usable.
    backend_manager = None

try:
    # The version lives in rpm/harbour-ferry.spec and reaches Python through
    # the build (see version.py) - a leaf module with no further imports, so
    # this stays usable even when the rest of the app is unavailable.
    from version import APP_VERSION
except ImportError:
    APP_VERSION = "0.1"

LOG_PREFIX = "[ferry]"
RCLONE_MIN_VERSION = (1, 66)
NETWORK_PROBE_URLS = [
    "https://downloads.rclone.org/version.txt",
    "https://api.github.com",
]
# Standard user folders that the Sailjail permission set should expose (AD-07c).
USER_FOLDERS = ["Documents", "Downloads", "Pictures", "Videos", "Music"]
MARKER_FILENAME = "sync-marker.json"
UI_DETAIL_LIMIT = 1400  # characters shown per test in the UI; full text goes to the report

_report_lines = []
_run_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Logging / event helpers
# ---------------------------------------------------------------------------

def log(msg):
    """Print a debug log line (DEV-02) and keep it for the report file."""
    line = "%s %s" % (LOG_PREFIX, msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # stdout may be ASCII-only (C locale, e.g. under the SDK debugger).
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    _report_lines.append(line)


def send(event, *args):
    if HAVE_PYOTHERSIDE:
        pyotherside.send(event, *args)
    else:
        line = "%s (event) %s: %r" % (LOG_PREFIX, event, args)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def home_dir():
    return os.path.expanduser("~")


def data_dir():
    base = os.environ.get("XDG_DATA_HOME", os.path.join(home_dir(), ".local", "share"))
    return os.path.join(base, APP_NAME, APP_NAME)


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME", os.path.join(home_dir(), ".config"))
    return os.path.join(base, APP_NAME, APP_NAME)


def app_install_dir():
    # __file__ is .../qml/utilities/diagnostics.py -> install root is two levels up.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def marker_candidate_paths():
    """All locations where the sync helper may have written its marker file."""
    home = home_dir()
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
    dirs = [
        os.path.join(xdg_data, APP_NAME, APP_NAME),
        os.path.join(xdg_data, APP_NAME),
        os.path.join(xdg_config, APP_NAME, APP_NAME),
        "/tmp",
    ]
    return [os.path.join(d, MARKER_FILENAME) for d in dirs]


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout=15, input_text=None, env_extra=None):
    """Run a command, capture output, never raise. Returns (rc, output)."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log("exec: %s" % " ".join(cmd))
    try:
        # Capture bytes and decode manually: text mode would use the locale
        # encoding, which may be ASCII and choke on tool output (systemctl
        # uses typographic characters).
        proc = subprocess.run(
            cmd,
            input=input_text.encode("utf-8") if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        log("exec done: rc=%d, %d bytes output" % (proc.returncode, len(out)))
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        log("exec timeout after %ss" % timeout)
        return -1, "TIMEOUT after %ss" % timeout
    except FileNotFoundError as e:
        log("exec failed: %s" % e)
        return -2, "NOT FOUND: %s" % e
    except Exception as e:
        log("exec failed: %s" % e)
        return -3, "ERROR: %s" % e


def find_processes(needle):
    """Scan /proc for processes whose cmdline contains needle.

    Replacement for pgrep: sailjail blocks executing system binaries.
    """
    found = []
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
                if needle in cmd:
                    found.append("%s: %s" % (pid, cmd[:120]))
            except Exception:
                continue
    except Exception as e:
        found.append("proc scan failed: %s" % e)
    return found


def dbus_session_names(filter_str):
    """List session bus names containing filter_str. Returns (names, error)."""
    try:
        import dbus
        bus = dbus.SessionBus()
        proxy = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        names = proxy.ListNames(dbus_interface="org.freedesktop.DBus")
        return [str(n) for n in names if filter_str in str(n).lower()], None
    except Exception as e:
        return None, str(e)


def dbus_start_unit(unit):
    """Start a systemd user unit via the D-Bus API (systemctl exec may be
    blocked inside the sandbox). Returns (ok, info)."""
    try:
        import dbus
        bus = dbus.SessionBus()
        proxy = bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
        mgr = dbus.Interface(proxy, "org.freedesktop.systemd1.Manager")
        job = mgr.StartUnit(unit, "replace")
        return True, "job: %s" % job
    except Exception as e:
        return False, str(e)


def find_rclone():
    """Locate the rclone binary: bundled first, then PATH (AD-07a)."""
    machine = platform.machine()
    candidates = [
        os.path.join(app_install_dir(), "bin", "rclone"),
        os.path.join(app_install_dir(), "bin", "rclone-%s" % machine),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c, "bundled"
    path_rclone = shutil.which("rclone")
    if path_rclone:
        return path_rclone, "system PATH"
    return None, "not found (checked: %s, PATH)" % ", ".join(candidates)


# ---------------------------------------------------------------------------
# Tests (each returns (status, details))
# ---------------------------------------------------------------------------

def test_pyotherside():
    d = []
    d.append("python: %s" % sys.version.replace("\n", " "))
    # NOTE: platform.platform() is avoided on purpose - under the app booster
    # sys.executable points at a directory and libc_ver() crashes on it.
    d.append("machine: %s" % platform.machine())
    try:
        d.append("uname: %s" % " ".join(os.uname()))
    except Exception as e:
        d.append("uname failed: %s" % e)
    d.append("sys.executable: %r" % sys.executable)
    # Sandbox indicators: inside sailjail/firejail PID 1 of the namespace is
    # the jail process, and system binary execution may be blocked.
    try:
        with open("/proc/1/comm", encoding="utf-8", errors="replace") as f:
            d.append("/proc/1/comm: %s" % f.read().strip())
    except Exception as e:
        d.append("/proc/1/comm unreadable: %s" % e)
    d.append("/run/firejail exists: %s" % os.path.exists("/run/firejail"))
    if HAVE_PYOTHERSIDE:
        try:
            ver = pyotherside.version
            if callable(ver):
                ver = ver()
            d.append("pyotherside version: %s" % ver)
        except Exception as e:
            d.append("pyotherside version lookup failed: %s" % e)
    else:
        d.append("pyotherside module NOT importable (standalone run?)")
    d.append("app install dir: %s" % app_install_dir())
    d.append("HOME: %s" % home_dir())
    d.append("data dir: %s" % data_dir())
    d.append("config dir: %s" % config_dir())
    sailjail_env = {k: v for k, v in os.environ.items()
                    if "SAILJAIL" in k or "FIREJAIL" in k or k.startswith("XDG")}
    d.append("sandbox-related env: %s" % json.dumps(sailjail_env))
    status = "PASS" if HAVE_PYOTHERSIDE else "FAIL"
    return status, "\n".join(d)


def test_rclone():
    rclone, origin = find_rclone()
    if not rclone:
        return "FAIL", "rclone binary %s" % origin
    d = ["rclone binary: %s (%s)" % (rclone, origin)]
    if not os.access(rclone, os.X_OK):
        d.append("WARNING: binary is not executable (check chmod in spec)")
    rc, out = run_cmd([rclone, "version"], timeout=20)
    d.append("rclone version output (rc=%d):\n%s" % (rc, out))
    if rc != 0:
        return "FAIL", "\n".join(d)
    m = re.search(r"rclone v(\d+)\.(\d+)", out)
    if not m:
        d.append("could not parse version string")
        return "FAIL", "\n".join(d)
    version = (int(m.group(1)), int(m.group(2)))
    d.append("parsed version: %d.%d (required: >= %d.%d)"
             % (version + RCLONE_MIN_VERSION))
    if version < RCLONE_MIN_VERSION:
        return "FAIL", "\n".join(d)
    return "PASS", "\n".join(d)


def test_network():
    import urllib.request
    d = []
    ok = False
    for url in NETWORK_PROBE_URLS:
        try:
            log("network probe: %s" % url)
            req = urllib.request.Request(url, headers={"User-Agent": "ferry-m0-diagnostics"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read(200)
                d.append("GET %s -> HTTP %s, first bytes: %r" % (url, resp.status, body[:80]))
                ok = True
                break
        except Exception as e:
            d.append("GET %s FAILED: %s" % (url, e))
    return ("PASS" if ok else "FAIL"), "\n".join(d)


def test_rclone_config():
    rclone, origin = find_rclone()
    d = []
    cfg_dir = config_dir()
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        probe = os.path.join(cfg_dir, "write-probe.txt")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        with open(probe, encoding="utf-8") as f:
            f.read()
        os.remove(probe)
        d.append("config dir read/write/delete OK: %s" % cfg_dir)
    except Exception as e:
        d.append("config dir access FAILED: %s (%s)" % (e, cfg_dir))
        return "FAIL", "\n".join(d)

    if not rclone:
        d.append("SKIP rclone config probe: rclone %s" % origin)
        return "FAIL", "\n".join(d)

    cfg = os.path.join(cfg_dir, "rclone-probe.conf")
    try:
        # 1. Create a config entry non-interactively.
        rc, out = run_cmd([rclone, "--config", cfg, "config", "create",
                           "ferry-probe", "local"], timeout=20)
        d.append("config create (rc=%d): %s" % (rc, out[:300]))
        create_ok = (rc == 0)

        # 2. Read it back.
        rc, out = run_cmd([rclone, "--config", cfg, "config", "dump"], timeout=20)
        d.append("config dump (rc=%d): %s" % (rc, out[:300]))
        dump_ok = (rc == 0)

        # 3. Encryption probe: encrypt the config, then read it back with
        #    RCLONE_CONFIG_PASS (AD-04). Password fed via stdin (no tty).
        enc_pass = "ferry-probe-pass"
        rc, out = run_cmd([rclone, "--config", cfg, "config", "encryption", "set"],
                          timeout=20, input_text="%s\n%s\n" % (enc_pass, enc_pass))
        d.append("config encryption set (rc=%d): %s" % (rc, out[:400]))
        enc_ok = False
        if rc == 0:
            with open(cfg, encoding="utf-8", errors="replace") as f:
                head = f.readline().strip()
            d.append("encrypted config first line: %r" % head)
            rc2, out2 = run_cmd([rclone, "--config", cfg, "config", "dump"],
                                timeout=20, env_extra={"RCLONE_CONFIG_PASS": enc_pass})
            d.append("encrypted dump with RCLONE_CONFIG_PASS (rc=%d): %s" % (rc2, out2[:200]))
            enc_ok = (rc2 == 0)
        d.append("summary: create=%s dump=%s encryption=%s"
                 % (create_ok, dump_ok, enc_ok))
        # Encryption result is spike information; plain config handling decides PASS.
        status = "PASS" if (create_ok and dump_ok) else "FAIL"
        return status, "\n".join(d)
    finally:
        try:
            if os.path.exists(cfg):
                os.remove(cfg)
                log("removed probe config %s" % cfg)
        except Exception as e:
            log("could not remove probe config: %s" % e)


def test_secrets():
    """Probe the Sailfish Secrets access paths in the order given by AD-08c.

    Sandbox-aware: no external binaries needed (sailjail blocks executing
    system binaries like pgrep).
    """
    d = []
    score = {"daemon": False, "socket": False, "dbus": False, "tool": False}

    # Daemon visible? (/proc scan instead of pgrep)
    procs = find_processes("sailfishsecretsd")
    score["daemon"] = bool(procs)
    d.append("sailfishsecretsd processes (/proc scan): %s"
             % (procs if procs else "none visible"))

    # Full runtime dir listing: shows what the sandbox exposes.
    run_dir = "/run/user/%d" % os.getuid()
    try:
        d.append("%s entries: %s" % (run_dir, sorted(os.listdir(run_dir))))
    except Exception as e:
        d.append("%s not listable: %s" % (run_dir, e))

    # Way 1a: the daemon's peer-to-peer socket.
    sock_path = os.path.join(run_dir, "sailfishsecretsd", "p2pSocket")
    d.append("p2pSocket path exists: %s (%s)" % (os.path.exists(sock_path), sock_path))
    if os.path.exists(sock_path):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(sock_path)
            s.close()
            score["socket"] = True
            d.append("p2pSocket connect: OK")
        except Exception as e:
            d.append("p2pSocket connect FAILED: %s" % e)

    # Way 1b: D-Bus from Python + session bus visibility.
    try:
        import dbus  # noqa: F401
        score["dbus"] = True
        d.append("python3 'dbus' module: importable")
    except ImportError as e:
        d.append("python3 'dbus' module: NOT importable (%s)" % e)
    names, err = dbus_session_names("secret")
    if names is not None:
        d.append("session bus names containing 'secret': %s" % (names if names else "none"))
    else:
        d.append("session bus ListNames FAILED: %s" % err)

    # Way 2: secrets-tool CLI (exec may be blocked in the sandbox).
    tool = shutil.which("secrets-tool")
    if tool:
        d.append("secrets-tool found: %s" % tool)
        rc, out = run_cmd([tool, "--help"], timeout=10)
        score["tool"] = (rc >= 0)
        d.append("secrets-tool --help (rc=%d):\n%s" % (rc, out[:1500]))
    else:
        d.append("secrets-tool: NOT found in PATH")

    # Way 3 is probed from QML (module import results are passed into the
    # report by the DiagnosticsPage).
    status = "PASS" if score["socket"] and score["dbus"] else "FAIL"
    d.append("summary: %s" % json.dumps(score))
    return status, "\n".join(d)


def test_secrets_introspection():
    """Walk and dump the Secrets daemon's P2P D-Bus introspection XML.

    The exact method signatures are needed to implement the real secrets
    manager (AD-08c way 1) - the full XML lands in the report file.
    """
    d = []
    try:
        import dbus
    except ImportError as e:
        return "FAIL", "dbus module not importable: %s" % e

    address = "unix:path=/run/user/%d/sailfishsecretsd/p2pSocket" % os.getuid()
    d.append("connecting to %s" % address)
    try:
        conn = dbus.connection.Connection(address)
    except Exception as e:
        return "FAIL", "\n".join(d + ["peer connection FAILED: %s" % e])

    def introspect(path):
        return str(conn.call_blocking(None, path,
                                      "org.freedesktop.DBus.Introspectable",
                                      "Introspect", "", (), timeout=10))

    interesting = []
    to_visit = ["/", "/Sailfish/Secrets"]
    visited = set()
    try:
        while to_visit and len(visited) < 25:
            path = to_visit.pop(0)
            if path in visited:
                continue
            visited.add(path)
            try:
                xml = introspect(path)
            except Exception as e:
                d.append("introspect %s FAILED: %s" % (path, e))
                continue
            children = re.findall(r'<node name="([^"]+)"', xml)
            method_count = xml.count("<method")
            d.append("node %s: children=%s, methods=%d" % (path, children, method_count))
            if method_count > 0 and ("sailfish" in xml.lower()
                                     or "secret" in xml.lower()
                                     or method_count > 3):
                interesting.append("=== %s ===\n%s" % (path, xml))
            for child in children:
                to_visit.append(path.rstrip("/") + "/" + child)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if interesting:
        d.append("\n".join(interesting))
        return "PASS", "\n".join(d)
    d.append("no service interfaces found in %d visited node(s)" % len(visited))
    return "FAIL", "\n".join(d)


def test_secrets_roundtrip():
    """Store/read/delete a probe secret via the secrets_client module
    (validates the real AD-04 storage path)."""
    d = []
    try:
        import secrets_client
    except ImportError as e:
        return "FAIL", "secrets_client not importable: %s" % e
    if not secrets_client.is_available():
        return "FAIL", "Sailfish Secrets not available (socket or dbus missing)"
    probe_name = "diagnostics-probe"
    probe_value = "ferry-probe-%d" % os.getpid()
    try:
        secrets_client.ensure_collection()
        d.append("collection ensured: %r" % secrets_client.COLLECTION)
        secrets_client.set_secret(probe_name, probe_value)
        d.append("set_secret OK")
        read_back = secrets_client.get_secret(probe_name)
        d.append("get_secret returned %s value"
                 % ("the matching" if read_back == probe_value else "a DIFFERENT"))
        secrets_client.delete_secret(probe_name)
        d.append("delete_secret OK")
        after = secrets_client.get_secret(probe_name)
        d.append("secret gone after delete: %s" % (after is None))
        ok = (read_back == probe_value) and (after is None)
        return ("PASS" if ok else "FAIL"), "\n".join(d)
    except secrets_client.SecretsError as e:
        d.append("SecretsError: %s" % e)
        return "FAIL", "\n".join(d)


def test_user_folders():
    d = []
    all_ok = True
    home = home_dir()
    for name in USER_FOLDERS:
        folder = os.path.join(home, name)
        probe = os.path.join(folder, ".ferry-m0-probe.txt")
        try:
            if not os.path.isdir(folder):
                d.append("%s: MISSING (%s)" % (name, folder))
                all_ok = False
                continue
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ferry m0 probe")
            with open(probe, encoding="utf-8") as f:
                f.read()
            os.remove(probe)
            listing_count = len(os.listdir(folder))
            d.append("%s: write/read/delete OK, %d entries visible" % (name, listing_count))
        except Exception as e:
            d.append("%s: FAILED (%s)" % (name, e))
            all_ok = False

    # RemovableMedia (SD card): informational.
    for media_root in ["/run/media/defaultuser", "/run/media/nemo"]:
        try:
            if os.path.isdir(media_root):
                d.append("removable media %s: %s" % (media_root, os.listdir(media_root)))
        except Exception as e:
            d.append("removable media %s: not listable (%s)" % (media_root, e))

    # Negative probes: paths that should be blocked when Sailjail is effective.
    for label, action in [
        ("~/.ssh listing", lambda: os.listdir(os.path.join(home, ".ssh"))),
        ("~/.bash_history read", lambda: open(os.path.join(home, ".bash_history")).close()),
    ]:
        try:
            action()
            d.append("negative probe %s: ALLOWED (sandbox may be inactive or path is empty view)" % label)
        except Exception as e:
            d.append("negative probe %s: blocked/failed as expected (%s)" % (label, e))

    neg_probe = os.path.join(home, ".ferry-negative-probe.txt")
    try:
        with open(neg_probe, "w", encoding="utf-8") as f:
            f.write("x")
        d.append("negative probe write to $HOME root: ALLOWED (note: sailjail keeps a private home view)")
        os.remove(neg_probe)
    except Exception as e:
        d.append("negative probe write to $HOME root: blocked (%s)" % e)

    return ("PASS" if all_ok else "FAIL"), "\n".join(d)


def test_backends():
    d = []
    try:
        backends_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backends")
        modules = sorted(f[:-3] for f in os.listdir(backends_dir)
                         if f.endswith(".py") and f != "__init__.py")
        d.append("backend modules found: %s" % modules)
        loaded = []
        for name in modules:
            mod = importlib.import_module("backends.%s" % name)
            info = getattr(mod, "BACKEND", None)
            if info:
                loaded.append(info.get("id", name))
                d.append("%s: display_name=%r rclone_type=%r fields=%d 2fa=%s enc_libs=%s"
                         % (name, info.get("display_name"), info.get("rclone_type"),
                            len(info.get("config_fields", [])),
                            info.get("supports_2fa"), info.get("supports_encrypted_libraries")))
            else:
                d.append("%s: no BACKEND definition exported" % name)
        status = "PASS" if "seafile" in loaded else "FAIL"
        return status, "\n".join(d)
    except Exception as e:
        d.append("backend discovery failed: %s" % e)
        return "FAIL", "\n".join(d)


# ---------------------------------------------------------------------------
# Configured account
# ---------------------------------------------------------------------------

# This report is written to ~/Downloads for users to send in, so the account
# section has to describe the account without carrying it: no server, no
# login, no folder names. What a support case needs is the *shape* of the
# account - is the URL a DAV path, is the name in that path the login name,
# does the root listing work - and every line below answers one of those
# from a redacted value. Equal fingerprints mean equal values, which is all
# the comparisons here need; differing ones are what tell a resolved
# Nextcloud user ID apart from a login name that was never corrected.

# Path segments that are backend structure rather than user data, and worth
# reading in full: which of these the path is made of is exactly what
# separates a working Nextcloud account from a broken one.
_STRUCTURAL_PATH_SEGMENTS = frozenset([
    "remote.php", "dav", "files", "webdav", "ocs", "v1.php", "v2.php",
    "seafdav", "seafhttp",
])


def fingerprint(value):
    """A short, stable, non-reversible stand-in for a value."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def redacted(value, label):
    """What a report may say about a value it must not contain."""
    if not value:
        return "<no %s>" % label
    return "<%s: %d chars, fp %s>" % (label, len(value), fingerprint(value))


def compare_values(left, right):
    """How two redacted values relate - the comparison, without the values."""
    if left == right:
        return "identical"
    if left.lower() == right.lower():
        return "same text, different capitalisation"
    return "different"


def scrubbed(text, secrets, limit=600):
    """Program output with the account values taken out of it.

    rclone quotes the full URL in most of its errors, and an error is the
    most useful thing this section can carry - so it is scrubbed rather than
    dropped. The longest values go first (the URL contains the host, the
    host contains nothing else), and whatever still looks like a URL
    afterwards is removed wholesale: a redirect target or a server message
    may name an address this function was never told about.
    """
    for value, placeholder in sorted(secrets, key=lambda pair: -len(pair[0])):
        if value:
            text = text.replace(value, placeholder)
    text = re.sub(r"https?://[^\s\"'<>]+", "<url redacted>", text)
    return text[:limit]


def describe_url(url):
    """The account URL as lines a report may contain."""
    parts = urllib.parse.urlsplit(url if "://" in url else "//" + url)
    d = []
    host = parts.hostname or ""
    is_ip = bool(re.match(r"^[0-9]+(\.[0-9]+){3}$", host)) or ":" in host
    d.append("URL scheme: %s%s"
             % (parts.scheme or "none",
                # sftp and ftp are encrypted or not by their own settings;
                # only a WebDAV account spelled http:// is plainly wrong.
                "  <-- plain HTTP, the password travels unencrypted"
                if parts.scheme == "http" else ""))
    if is_ip:
        # An address has no name for a certificate to match and nothing for
        # the resolver probe below to look up, which a reader would
        # otherwise take for failures of the account.
        d.append("URL host: %s (a literal IP address, port: %s)"
                 % (redacted(host, "host"),
                    parts.port if parts.port else "default"))
    else:
        labels = [label for label in host.split(".") if label]
        d.append("URL host: %s (%d label(s), tld %r, port: %s)"
                 % (redacted(host, "host"), len(labels),
                    labels[-1] if len(labels) > 1 else "",
                    parts.port if parts.port else "default"))
    segments = [segment for segment in (parts.path or "").split("/") if segment]
    if segments:
        shown = []
        for index, segment in enumerate(segments):
            if segment.lower() in _STRUCTURAL_PATH_SEGMENTS:
                shown.append(segment)
            else:
                shown.append(redacted(urllib.parse.unquote(segment),
                                      "segment %d" % (index + 1)))
        d.append("URL path: /%s" % "/".join(shown))
    else:
        d.append("URL path: none (the URL is a bare server address)")
    return d, host, segments


def account_shape(summary):
    """The redacted description of the stored account, and its host."""
    d = []
    url = summary.get("url") or ""
    user = summary.get("user") or ""
    backend_id = summary.get("backend_id") or ""
    d.append("backend: %s (rclone type=%r vendor=%r)"
             % (backend_id or "unknown", summary.get("backend", ""),
                summary.get("vendor", "")))
    url_lines, host, segments = describe_url(url)
    d.extend(url_lines)
    d.append("login: %s (contains '@': %s)"
             % (redacted(user, "login"), "@" in user))
    d.append("rclone.conf encrypted: %s" % summary.get("encrypted"))
    d.append("accept self-signed certificates: %s" % summary.get("insecure_tls"))

    # The Nextcloud question. Its WebDAV path ends in the user ID; Ferry
    # guesses that from the login name and corrects the guess by asking the
    # server (backends/nextcloud.resolve_user_id). A path that is still the
    # guess while the real ID differs is the one account shape that
    # authenticates fine and lists nothing - and it is invisible in a log,
    # because both values are names the report may not print.
    if segments:
        d.append("last URL path segment vs. login: %s"
                 % compare_values(urllib.parse.unquote(segments[-1]), user))
    if backend_manager is None:
        return d, host
    try:
        # Only a backend that carries a name in its URL brings a form_url()
        # of its own (see backend_manager.form_url) - and only there is
        # there a guess that could be wrong. Asking the module rather than
        # the backend id keeps this true for the next such backend.
        module = backend_manager.get_backend(backend_id)
    except Exception as e:
        d.append("backend module %r not loadable: %s" % (backend_id, e))
        return d, host
    if hasattr(module, "form_url"):
        # form_url() shortens exactly when the stored URL is what
        # webdav_url(server, login) rebuilds - that is, when the name in the
        # path is Ferry's own guess from the login and was never replaced by
        # an ID resolved from the server.
        display = backend_manager.display_url(backend_id, url)
        form = backend_manager.form_url(backend_id, url, user)
        d.append("name in the URL is Ferry's guess from the login (no user"
                 " ID lookup ever corrected it): %s" % (form == display != url))
    return d, host


def resolver_probe(host):
    """Whether this device can resolve the account host from Python.

    rclone is a static Go binary with a resolver of its own and keeps
    working where glibc's does not; the Nextcloud user ID lookup goes
    through urllib and does not. When the two disagree, that lookup falls
    back to the login name without an error, which then looks like a broken
    account rather than broken name resolution - so the report asks both.
    Returns (line for the report, resolved).
    """
    if not host:
        return "host name resolution: no host to resolve", True
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return "host name resolution (Python/glibc): OK", True
    except Exception as e:
        return "host name resolution (Python/glibc) FAILED: %s" % e, False


def test_account():
    """The configured account, and what the server answers for it.

    The one test that looks at the user's own account instead of the
    device: the support cases this report exists for ("it connects but I
    see no folders") are decided by the account's shape and by what
    "rclone lsjson ferry:" answers, and neither is visible anywhere else.
    Everything it prints is redacted - see the section comment above.
    """
    d = []
    try:
        import config_manager
    except Exception as e:
        d.append("config_manager not importable: %s" % e)
        return "FAIL", "\n".join(d)

    conf = config_manager.rclone_conf_path()
    if not os.path.exists(conf):
        d.append("no rclone.conf at %s" % conf)
        d.append("no account is configured on this device - nothing to probe")
        return "SKIP", "\n".join(d)
    try:
        summary = config_manager.get_account_summary()
    except Exception as e:
        d.append("reading the account failed: %s" % e)
        return "FAIL", "\n".join(d)
    if not summary:
        d.append("rclone.conf exists but holds no %r remote"
                 % config_manager.REMOTE_NAME)
        return "SKIP", "\n".join(d)
    if summary.get("error"):
        # An encrypted config whose password is unreachable in this session
        # lands here, and so does one rclone cannot parse.
        d.append("the stored configuration could not be read: %s"
                 % summary["error"])
        return "FAIL", "\n".join(d)

    shape, host = account_shape(summary)
    d.extend(shape)
    resolver_line, resolver_ok = resolver_probe(host)
    d.append(resolver_line)

    # Everything above describes what is stored. The probe below is what the
    # app itself runs when it opens the main page or the remote browser, and
    # it is the answer to the report the user actually sends: "it connects
    # but I see no folders".
    url = summary.get("url") or ""
    user = summary.get("user") or ""
    secrets = [(url, "<url redacted>"), (host, "<host redacted>"),
               (user, "<login redacted>")]
    remote = "%s:" % config_manager.REMOTE_NAME
    rc, out = config_manager.run_rclone(["lsjson", remote], timeout=90,
                                        log_args=False)
    d.append("rclone lsjson %s -> rc=%d" % (remote, rc))
    if rc != 0:
        d.append("listing output: %s" % scrubbed(out, secrets))
        if config_manager.looks_like_missing_path(out):
            d.append("the server answered, but the path in the URL does not"
                     " exist on it: the login works and the address does not"
                     " (Nextcloud: the name in /remote.php/dav/files/<name>"
                     " is not this account's user ID)")
        return "FAIL", "\n".join(d)

    start, end = out.find("["), out.rfind("]")
    listing = []
    if 0 <= start < end:
        try:
            listing = json.loads(out[start:end + 1])
        except ValueError:
            d.append("listing output could not be parsed as JSON: %s"
                     % scrubbed(out, secrets, limit=300))
            return "FAIL", "\n".join(d)
    if not resolver_ok:
        # rclone got through and Python did not: everything the app does
        # over urllib is broken on this device while every rclone call
        # works. For a Nextcloud account that is not cosmetic - the user ID
        # lookup is one of those urllib calls, and its failure is a silent
        # fallback to the login name (see backends/nextcloud.resolve_user_id).
        d.append("NOTE: rclone reached the server but Python cannot resolve"
                 " its name - the Nextcloud user ID lookup runs through"
                 " Python and cannot work on this device, so the WebDAV path"
                 " stays whatever was typed or guessed")

    folders = [item for item in listing if item.get("IsDir")]
    d.append("remote root: %d folder(s), %d file(s)"
             % (len(folders), len(listing) - len(folders)))
    if not folders:
        # rc=0 means the server listed this path, so the path exists - which
        # is the whole difference between "there is nothing to show" and the
        # missing-path case above. The main page lists folders only, so on
        # screen the two are indistinguishable and only this line separates
        # them.
        d.append("NOTE: the listing succeeded, so the URL points at a folder"
                 " that exists - an empty result here means the remote root"
                 " really holds no folders, not that the account is broken")

    # A second angle on the same question: rclone asks the server for its
    # quota, which a path that merely exists (a shared parent folder, an
    # error page) does not answer. Informational - not every backend
    # implements it.
    rc_about, out_about = config_manager.run_rclone(
        ["about", remote, "--json"], timeout=60, log_args=False)
    if rc_about == 0:
        d.append("rclone about %s -> rc=0: the server reported quota data,"
                 " so the URL belongs to a real account" % remote)
    else:
        d.append("rclone about %s -> rc=%d: %s"
                 % (remote, rc_about, scrubbed(out_about, secrets, limit=300)))

    try:
        import sync_pairs
        pairs = sync_pairs.list_pairs()
        d.append("sync pairs configured: %d" % len(pairs))
        for index, pair in enumerate(pairs):
            d.append("  pair %d: mode=%s remote=%s local=%s"
                     % (index + 1, sync_pairs.pair_mode(pair),
                        redacted(pair.get("remote_path") or "", "remote path"),
                        redacted(pair.get("local_path") or "", "local path")))
    except Exception as e:
        d.append("sync pairs not readable: %s" % e)
    return "PASS", "\n".join(d)

def test_systemd_timer():
    d = []
    unit = "%s-sync.service" % APP_NAME
    timer = "%s-sync.timer" % APP_NAME

    # Unit files installed? (file check - systemctl exec may be blocked)
    for u in (unit, timer):
        path = "/usr/lib/systemd/user/%s" % u
        d.append("unit file %s exists: %s" % (path, os.path.exists(path)))
    if not os.path.exists("/usr/lib/systemd/user/%s" % unit):
        d.append("service unit file missing; aborting timer test")
        return "FAIL", "\n".join(d)

    # Exec capability probes: which system binaries can the sandbox execute?
    for binary in ["/usr/bin/systemctl", "/usr/bin/pgrep",
                   "/bin/sh", "/usr/bin/python3"]:
        d.append("exec probe %s: exists=%s access_x=%s"
                 % (binary, os.path.exists(binary), os.access(binary, os.X_OK)))

    helper_path = os.path.join(app_install_dir(), "helper", "sync_helper.py")

    # Sub-test A: run the helper directly to prove the script works.
    # (No sailjail wrapper anymore: sandboxing disabled per AD-07d.)
    rc, out = run_cmd(["/usr/bin/python3", helper_path], timeout=30)
    d.append("A) direct helper run (rc=%d):\n%s" % (rc, out[:800]))

    # Remove stale markers so we only accept a fresh one.
    for p in marker_candidate_paths():
        try:
            if os.path.exists(p):
                os.remove(p)
                d.append("removed stale marker: %s" % p)
        except Exception as e:
            d.append("could not remove stale marker %s: %s" % (p, e))

    started = time.time()
    # Reload user systemd first: after an RPM upgrade the old unit definition
    # stays loaded until daemon-reload (observed in TS-00 round 4).
    rc, out = run_cmd(["systemctl", "--user", "daemon-reload"], timeout=20)
    d.append("systemctl --user daemon-reload (rc=%d): %s" % (rc, out[:200]))

    # C1: start via systemctl binary; C2: fall back to the systemd D-Bus API
    # when binary execution is blocked inside the sandbox.
    start_rc, out = run_cmd(["systemctl", "--user", "start", unit], timeout=30)
    d.append("C1) systemctl --user start %s (rc=%d): %s" % (unit, start_rc, out[:300]))
    systemctl_usable = (start_rc >= 0)
    if start_rc != 0:
        ok, info = dbus_start_unit(unit)
        d.append("C2) D-Bus StartUnit(%s): %s (%s)" % (unit, "OK" if ok else "FAILED", info[:400]))

    # Poll for the marker file the helper writes.
    marker_found = None
    deadline = time.time() + 20
    while time.time() < deadline and marker_found is None:
        for p in marker_candidate_paths():
            if os.path.exists(p) and os.path.getmtime(p) >= started - 1:
                marker_found = p
                break
        if marker_found is None:
            time.sleep(1)

    if marker_found:
        try:
            with open(marker_found, encoding="utf-8", errors="replace") as f:
                content = f.read()
            d.append("marker found at %s:\n%s" % (marker_found, content[:1200]))
        except Exception as e:
            d.append("marker found at %s but unreadable: %s" % (marker_found, e))
    else:
        d.append("NO marker file appeared within 20s (checked: %s)"
                 % ", ".join(marker_candidate_paths()))
        d.append("note: helper may have run in a separate sandbox with a private "
                 "data dir/tmp - check journal output below")

    if systemctl_usable:
        rc, out = run_cmd(["systemctl", "--user", "status", unit, "--no-pager", "-l"], timeout=15)
        d.append("service status (rc=%d):\n%s" % (rc, out[:1200]))
        rc, out = run_cmd(["journalctl", "--user", "-q", "-u", unit, "-n", "25", "--no-pager"], timeout=15)
        d.append("journal tail (rc=%d):\n%s" % (rc, out[:1200]))
    else:
        d.append("skipping status/journal capture: systemctl not executable in sandbox")
    d.append("timer unit installed as %s (not enabled by default in M0)" % timer)

    return ("PASS" if marker_found else "FAIL"), "\n".join(d)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("PyOtherSide / environment", test_pyotherside),
    ("rclone binary + version", test_rclone),
    ("Network access", test_network),
    ("rclone.conf access + encryption", test_rclone_config),
    ("Sailfish Secrets access", test_secrets),
    ("Secrets D-Bus introspection", test_secrets_introspection),
    ("Secrets store/get roundtrip", test_secrets_roundtrip),
    ("User folder access (Sailjail)", test_user_folders),
    ("Backend plugin discovery", test_backends),
    ("Configured account + remote listing", test_account),
    ("systemd timer + sync helper", test_systemd_timer),
]


def _summary_line(passed, failed, skipped, prefix="", suffix=""):
    """The counts headline - skipped tests only appear when there are any."""
    counts = "%d passed, %d failed" % (passed, failed)
    if skipped:
        counts += ", %d skipped" % skipped
    return "%s%s of %d tests%s" % (prefix, counts, len(TESTS), suffix)


def _write_report(summary):
    # Write to several locations: the app data dir may be a sandbox-private
    # view, so also drop a copy into ~/Downloads (whitelisted, easy to find).
    targets = [
        os.path.join(data_dir(), "diagnostics-report.txt"),
        os.path.join(home_dir(), "Downloads", "ferry-diagnostics-report.txt"),
    ]
    content = "Ferry %s M0 diagnostics report - %s\n%s\n\n%s\n" % (
        APP_VERSION, datetime.datetime.now().isoformat(), summary,
        "\n".join(_report_lines))
    written = []
    for path in targets:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            log("report written to %s" % path)
            written.append(path)
        except Exception as e:
            log("could not write report to %s: %s" % (path, e))
    return ", ".join(written)


def _run_all_impl(qml_probe_info=None):
    del _report_lines[:]
    log("=== Ferry %s M0 diagnostics started (TS-00) ===" % APP_VERSION)
    if qml_probe_info:
        # Import results of QML modules (Sailfish.Secrets etc.), probed by
        # the DiagnosticsPage and passed in for the report (AD-08c way 3).
        log("QML module probes (from DiagnosticsPage):")
        for line in str(qml_probe_info).splitlines():
            log("    %s" % line)
    passed = failed = skipped = 0
    for name, func in TESTS:
        log("--- test started: %s ---" % name)
        send("test-started", {"name": name})
        try:
            status, details = func()
        except Exception:
            status = "FAIL"
            details = "unexpected exception:\n%s" % traceback.format_exc()
        log("--- test result: %s -> %s ---" % (name, status))
        for line in details.splitlines():
            log("    %s" % line)
        if status == "PASS":
            passed += 1
        elif status == "SKIP":
            # Nothing to test rather than something that went wrong: a
            # device without a configured account must not send in a report
            # that says "1 failed" and points the reader at the wrong line.
            skipped += 1
        else:
            failed += 1
        ui_details = details if len(details) <= UI_DETAIL_LIMIT \
            else details[:UI_DETAIL_LIMIT] + "\n[... truncated, see report file]"
        send("test-result", {"name": name, "status": status, "details": ui_details})
        # Persist partial results after every test so an aborted run still
        # leaves a usable report on disk.
        _write_report(_summary_line(passed, failed, skipped, "IN PROGRESS: ",
                                    " so far"))
    summary = _summary_line(passed, failed, skipped)
    log("=== diagnostics finished: %s ===" % summary)
    report_path = _write_report(summary)
    send("finished", summary, report_path)


def run_all(qml_probe_info=None):
    """Entry point called from QML; runs all tests in a background thread."""
    if not _run_lock.acquire(blocking=False):
        log("diagnostics already running, ignoring request")
        return

    def worker():
        try:
            _run_all_impl(qml_probe_info)
        finally:
            _run_lock.release()

    threading.Thread(target=worker, name="ferry-diagnostics").start()


if __name__ == "__main__":
    # Standalone smoke test (desktop / device shell).
    _run_all_impl()
