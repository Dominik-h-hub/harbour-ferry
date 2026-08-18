#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - rclone configuration manager.
# Creates/updates the account remote via rclone's non-interactive config
# state machine (handles the seafile 2FA question), keeps rclone.conf
# encrypted with the password from the credential store, and runs the
# connection test.
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import threading

import backend_manager
import common
import credential_store

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    HAVE_PYOTHERSIDE = False

log = common.make_logger("config")

# One server account: a single, fixed remote name.
REMOTE_NAME = "ferry"
_MAX_STATE_STEPS = 12


def _status(message):
    """Push a progress message to the UI and the log."""
    log("status: %s" % message)
    if HAVE_PYOTHERSIDE:
        pyotherside.send("account-status", message)


def rclone_conf_path():
    return os.path.join(common.config_dir(), "rclone.conf")


def is_config_encrypted():
    path = rclone_conf_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().startswith("# Encrypted rclone configuration File")
    except OSError:
        return False


def _rclone_env():
    env = {}
    password = credential_store.get_config_password(create=False)
    if password:
        env["RCLONE_CONFIG_PASS"] = password
    return env


def _run_rclone(args, timeout=60, input_text=None, log_args=True):
    rclone, origin = common.find_rclone()
    if not rclone:
        return -2, "rclone binary %s" % origin
    os.makedirs(common.config_dir(), exist_ok=True)
    # --ask-password=false: without it rclone would prompt interactively for
    # the config password when RCLONE_CONFIG_PASS is unavailable and block
    # until the timeout.
    cmd = [rclone, "--config", rclone_conf_path(), "--ask-password=false"] + args
    return common.run_cmd(cmd, timeout=timeout, input_text=input_text,
                          env_extra=_rclone_env(), logger=log, log_args=log_args)


def run_rclone(args, timeout=60, input_text=None, log_args=True):
    """Public wrapper for other modules (remote browser, sync engine)."""
    return _run_rclone(args, timeout=timeout, input_text=input_text,
                       log_args=log_args)


def build_rclone_command(args):
    """Return (cmd_list, env) for long-running rclone processes (Popen)."""
    rclone, origin = common.find_rclone()
    if not rclone:
        raise RuntimeError("rclone binary %s" % origin)
    env = os.environ.copy()
    env.update(_rclone_env())
    return [rclone, "--config", rclone_conf_path(), "--ask-password=false"] + args, env


def friendly_error(output):
    """Public alias for UI-facing error mapping."""
    return _friendly_error(output)


def _parse_json(output):
    """Extract the JSON object from rclone output (may be prefixed by
    NOTICE log lines)."""
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(output[start:end + 1])
    except ValueError:
        return None


def _format_answer(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _resolve_answer(option, values, backend):
    """Find the answer for an interactive rclone config question.

    Returns (answer, source) or (None, reason).
    """
    name = option.get("Name", "")
    # 1. Direct match: a form value with the same key as the question.
    if name in values and values[name] not in (None, ""):
        return _format_answer(values[name]), "form field %r" % name
    # 2. Backend-provided mapping (e.g. seafile 2FA question -> otp field).
    auth_map = getattr(backend, "AUTH_ANSWERS", {})
    if name in auth_map:
        key = auth_map[name]
        if values.get(key) not in (None, ""):
            return _format_answer(values[key]), "mapped field %r" % key
    # 3. Heuristic: anything that smells like a one-time code.
    lowered = name.lower()
    if any(hint in lowered for hint in ("2fa", "otp", "totp", "code")):
        if values.get("otp") not in (None, ""):
            return _format_answer(values["otp"]), "otp heuristic"
    # 4. Question default.
    if option.get("Default") is not None:
        return _format_answer(option["Default"]), "question default"
    return None, "no answer available for question %r (help: %s)" \
        % (name, (option.get("Help") or "")[:200])


def _run_config_state_machine(backend, params, values, update):
    """Drive 'rclone config create/update --non-interactive' to completion.

    Returns (ok, message).
    """
    base = ["config", "update", REMOTE_NAME] if update \
        else ["config", "create", REMOTE_NAME, backend.BACKEND["rclone_type"]]
    for key, value in params.items():
        base.append("%s=%s" % (key, value))
    base += ["--non-interactive"]
    if "pass" in params:
        base += ["--obscure"]

    log("config state machine start (update=%s, options: %s)"
        % (update, sorted(params.keys())))
    extra = []
    for step in range(_MAX_STATE_STEPS):
        rc, out = _run_rclone(base + extra, timeout=90, log_args=False)
        if rc != 0:
            return False, "rclone config failed (rc=%d): %s" % (rc, out[:400])
        response = _parse_json(out)
        if response is None:
            # No JSON usually means the command completed without questions.
            log("state machine step %d: no JSON in output, assuming done" % step)
            return True, "configuration written"
        error = response.get("Error") or ""
        if error:
            log("state machine reported error: %s" % error[:300])
        option = response.get("Option")
        state = response.get("State") or ""
        if not option:
            log("state machine finished after %d step(s)" % (step + 1))
            return True, "configuration written"
        answer, source = _resolve_answer(option, values, backend)
        secret_question = bool(option.get("IsPassword"))
        log("state machine question %r (state %r) -> %s"
            % (option.get("Name"), state[:60],
               ("answered from " + source) if answer is not None else source))
        if answer is None:
            return False, "configuration needs an answer for %r - %s" \
                % (option.get("Name"), source)
        extra = ["--continue", "--state", state, "--result", answer]
        if secret_question:
            extra += ["--obscure"] if "--obscure" not in base else []
    return False, "configuration did not finish within %d steps" % _MAX_STATE_STEPS


def _ensure_encrypted():
    """Encrypt rclone.conf with the stored config password."""
    if is_config_encrypted():
        log("rclone.conf already encrypted")
        return True, "already encrypted"
    password = credential_store.get_config_password(create=True)
    rc, out = _run_rclone(["config", "encryption", "set"], timeout=30,
                          input_text="%s\n%s\n" % (password, password))
    if rc != 0 or not is_config_encrypted():
        return False, "config encryption failed (rc=%d): %s" % (rc, out[:300])
    log("rclone.conf encrypted")
    return True, "encrypted"


def get_account_summary():
    """Return account info for the UI, or None if not configured."""
    if not os.path.exists(rclone_conf_path()):
        log("no rclone.conf - account not configured")
        return None
    rc, out = _run_rclone(["config", "dump"], timeout=30)
    if rc != 0:
        log("config dump failed (rc=%d): %s" % (rc, out[:200]))
        if is_config_encrypted() \
                and credential_store.get_config_password(create=False) is None:
            return {"error": "Configuration is encrypted but its password is not"
                             " accessible in this session (see log)."
                             " Start the app from the launcher, or remove and"
                             " re-add the account."}
        return {"error": "Stored configuration could not be read"
                         " - remove the account and set it up again."}
    dump = _parse_json(out) or {}
    remote = dump.get(REMOTE_NAME)
    if not remote:
        log("rclone.conf has no %r remote" % REMOTE_NAME)
        return None
    summary = {
        "backend": remote.get("type", ""),
        "vendor": remote.get("vendor", ""),
        "url": remote.get("url", ""),
        "user": remote.get("user", ""),
        "use_2fa": str(remote.get("2fa", "")).lower() == "true",
        "encrypted": is_config_encrypted(),
    }
    # Which backend module the remote belongs to (the account form preselects
    # it, so an existing account is not silently rewritten to another type).
    summary["backend_id"] = backend_manager.backend_id_for_remote(
        summary["backend"], summary["vendor"])
    log("account summary: backend=%s vendor=%s id=%s url=%s user=%s 2fa=%s"
        " encrypted=%s"
        % (summary["backend"], summary["vendor"], summary["backend_id"],
           summary["url"], summary["user"], summary["use_2fa"],
           summary["encrypted"]))
    return summary


def get_account_password():
    """Return {"password", "token_only"} for the settings form.

    With 2FA enabled rclone's seafile backend exchanges the password for an
    auth token at setup time and does NOT store the password - in
    that case token_only is True and there is nothing to reveal. The value
    is decoded with 'rclone reveal' and never written to the log."""
    result = {"password": "", "token_only": False}
    if not os.path.exists(rclone_conf_path()):
        return result
    rc, out = _run_rclone(["config", "dump"], timeout=30)
    if rc != 0:
        return result
    remote = (_parse_json(out) or {}).get(REMOTE_NAME) or {}
    obscured = remote.get("pass", "")
    if not obscured:
        result["token_only"] = bool(remote.get("auth_token"))
        log("no stored password in config (token_only=%s)" % result["token_only"])
        return result
    rc, out = _run_rclone(["reveal", obscured], timeout=15, log_args=False)
    if rc != 0 or not out:
        log("reveal failed (rc=%d)" % rc)
        return result
    log("account password revealed for the settings form (value not logged)")
    # Output may contain NOTICE lines merged in; the value is the last line.
    result["password"] = out.strip().splitlines()[-1]
    return result


def setup_and_test_background(backend_id, values):
    """Run setup_and_test detached; the result arrives via the
    'account-result' event (used by the account result page, which is shown
    while the work is still running)."""
    def worker():
        result = setup_and_test(backend_id, values)
        if HAVE_PYOTHERSIDE:
            pyotherside.send("account-result", result)

    threading.Thread(target=worker, name="ferry-account").start()
    return True


def test_connection_background():
    """Re-run only the connection test detached ('Test again' on the account
    result page); the result arrives via the 'account-result' event."""
    def worker():
        ok, message, details, libraries = test_connection()
        result = _result(ok, message, details,
                         [{"title": "Connection test", "ok": ok,
                           "detail": message}], libraries)
        if HAVE_PYOTHERSIDE:
            pyotherside.send("account-result", result)

    threading.Thread(target=worker, name="ferry-retest").start()
    return True


def test_connection():
    """List the remote root.

    Returns (ok, message, details, libraries) where libraries is the list of
    top-level directory names on the remote.
    """
    _status("Testing connection...")
    rc, out = _run_rclone(["lsjson", "%s:" % REMOTE_NAME], timeout=90)
    if rc == 0:
        # lsjson prints a JSON array, possibly preceded by NOTICE log lines.
        start, end = out.find("["), out.rfind("]")
        listing = []
        if 0 <= start < end:
            try:
                listing = json.loads(out[start:end + 1])
            except ValueError:
                log("could not parse lsjson output")
        names = [e.get("Name", "?") for e in listing if e.get("IsDir")]
        message = "Connection OK - %d libraries found" % len(names)
        details = ", ".join(names[:8]) + (" ..." if len(names) > 8 else "")
        log("connection test OK: %d libraries (%s)" % (len(names), details))
        return True, message, details, names
    friendly = _friendly_error(out)
    log("connection test FAILED (rc=%d): %s" % (rc, out[:500]))
    return False, friendly, out[:600], []


def _friendly_error(output):
    lowered = output.lower()
    if "no such host" in lowered or "name or service not known" in lowered:
        return "Server not found - please check the server URL."
    if "connection refused" in lowered:
        return "Connection refused - please check the URL and port."
    if "certificate" in lowered or "x509" in lowered:
        return "TLS certificate problem - please check the server certificate."
    if "timeout" in lowered or "deadline exceeded" in lowered:
        return "Connection timed out - please check your network and the URL."
    if "401" in lowered or "authentication" in lowered or "two-factor" in lowered \
            or "login" in lowered or "password" in lowered:
        return "Login failed - please check username, password and OTP."
    return "Connection test failed - see details."


def _account_info():
    """Account fields for the result page; never raises, empty when unknown."""
    try:
        summary = get_account_summary() or {}
    except Exception as e:
        log("account info for the result page failed: %s" % e)
        return {}
    return {} if summary.get("error") else summary


def _result(ok, message, details, steps, libraries=None):
    """The payload the account result page renders."""
    return {"ok": bool(ok), "message": message, "details": details,
            "steps": steps, "libraries": libraries or [],
            "account": _account_info()}


def setup_and_test(backend_id, values):
    """Save the account and run the connection test.

    Called from the AccountPage. Returns
    {ok, message, details, steps, libraries, account} - the extra fields feed
    the account result page.
    """
    steps = []
    try:
        backend = backend_manager.get_backend(backend_id)
        params = backend.build_rclone_config(values)

        # 'rclone config update' cannot change a remote's type, so switching
        # the account to another backend means recreating it from scratch.
        existing = get_account_summary() or {}
        stored_type = existing.get("backend") or ""
        switching = bool(stored_type) \
            and stored_type != backend.BACKEND["rclone_type"]
        update = bool(existing) and not switching

        missing = [k for k in ("url", "user") if not params.get(k)]
        if not update and not values.get("pass"):
            missing.append("pass")
        if missing:
            steps.append({"title": "Check input", "ok": False,
                          "detail": "missing: %s" % ", ".join(missing)})
            return _result(False, "Please fill in: %s" % ", ".join(missing),
                           "", steps)
        steps.append({"title": "Check input", "ok": True, "detail": "complete"})

        if switching:
            _status("Switching backend...")
            rc, out = _run_rclone(["config", "delete", REMOTE_NAME], timeout=30)
            steps.append({"title": "Switch backend", "ok": rc == 0,
                          "detail": ("replacing the previous %s remote"
                                     % stored_type) if rc == 0 else out[:300]})
            if rc != 0:
                return _result(False, "Switching the backend failed",
                               out[:300], steps)

        _status("Writing rclone configuration...")
        ok, message = _run_config_state_machine(backend, params, values, update)
        steps.append({"title": "Update account" if update else "Create account",
                      "ok": ok, "detail": message})
        if not ok:
            return _result(False, "Saving the account failed", message, steps)

        _status("Encrypting configuration...")
        ok, message = _ensure_encrypted()
        steps.append({"title": "Encrypt configuration", "ok": ok,
                      "detail": message})
        if not ok:
            return _result(False, "Encrypting the configuration failed",
                           message, steps)

        ok, message, details, libraries = test_connection()
        steps.append({"title": "Connection test", "ok": ok, "detail": message})
        return _result(ok, message, details, steps, libraries)
    except Exception as e:
        log("setup_and_test unexpected error: %s" % e)
        steps.append({"title": "Unexpected error", "ok": False,
                      "detail": str(e)})
        return _result(False, "Unexpected error", str(e), steps)


def delete_account():
    """Remove the account: config file and config password."""
    removed = []
    for path in (rclone_conf_path(),):
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    credential_store.delete_config_password()
    log("account deleted (removed: %s)" % (removed or "nothing"))
    return True
