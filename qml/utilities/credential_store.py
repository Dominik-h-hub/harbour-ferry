#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - credential store for the rclone config password.
#
# M2: primary storage is Sailfish Secrets via the daemon's P2P D-Bus socket
# (secrets_client). File store remains only as an emergency fallback when Secrets is unavailable.
#
# SPDX-License-Identifier: Apache-2.0

import os
import secrets
import threading

import common
import secrets_client

log = common.make_logger("credstore")

SECRET_NAME = "rclone-config-password"
_PASS_FILENAME = "config-pass"

# Process cache for the config password. Every rclone call needs it for
# RCLONE_CONFIG_PASS, and each lookup is a blocking D-Bus round trip to
# sailfishsecretsd - noticeable when browsing the remote, where one user
# action triggers several rclone processes. The value lives in this process
# anyway (it is passed to every child), so keeping it costs no extra exposure.
# Only a real password is cached, never a "not stored" result: the daemon may
# become reachable later in the session.
_CACHE_LOCK = threading.Lock()
_cached_password = None


def _remember(password):
    global _cached_password
    with _CACHE_LOCK:
        _cached_password = password
    return password


def invalidate_cache():
    """Forget the cached password (after delete/rotate)."""
    global _cached_password
    with _CACHE_LOCK:
        _cached_password = None


def _pass_path():
    return os.path.join(common.config_dir(), _PASS_FILENAME)


def _read_file():
    path = _pass_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return None


def _write_file(password):
    os.makedirs(common.config_dir(), exist_ok=True)
    fd = os.open(_pass_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(password)


def _delete_file():
    path = _pass_path()
    if os.path.exists(path):
        os.remove(path)
        log("legacy password file removed")


def _generate():
    return secrets.token_urlsafe(24)


def _open_collection_marker():
    return os.path.join(common.data_dir(), "secrets-collection-open")


def _ensure_open_collection(password):
    """One-time migration of the collection to NoAccessControlMode, so that a
    second application identity cannot lock us out (see
    secrets_client.ensure_collection for what that mode buys and costs).

    The migration IS destructive: an access mode cannot be changed after
    creation, so the collection has to be deleted and rebuilt. Everything in
    it is therefore read out beforehand and restored afterwards - the config
    password and the key of every encrypted library. Two safeguards for the
    window in between, in which the secrets only exist in this process:

    - Can the old collection not be read completely? Then nothing is deleted
      and the migration waits for the next start.
    - Does the rebuild fail half way? Then the config password goes into the
      file fallback so the rclone config stays decryptable; without it the
      account would have to be set up from scratch.
    """
    marker = _open_collection_marker()
    if os.path.exists(marker):
        return
    # Imported late: enc_libraries sits on top of this module, and only this
    # one migration needs it.
    import enc_libraries
    preserve = {SECRET_NAME: password}
    try:
        preserve.update(enc_libraries.stored_keys())
    except secrets_client.SecretsError as e:
        log("could not read the stored library keys (%s) - migration "
            "postponed, nothing deleted" % e)
        return
    try:
        log("migrating secrets collection to open access mode "
            "(%d secret(s) to preserve)" % len(preserve))
        secrets_client.recreate_collection_open(preserve)
    except secrets_client.SecretsError as e:
        log("open-collection migration failed (%s) - writing the config "
            "password to the file fallback so the rclone config stays "
            "readable" % e)
        try:
            _write_file(password)
        except OSError as file_error:
            log("WARNING: file fallback failed as well (%s) - the config "
                "password now only lives in this process" % file_error)
        return
    os.makedirs(common.data_dir(), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("migrated\n")
    log("secrets collection migrated to open access mode")


def _log_secrets_failure(error):
    if secrets_client.is_ownership_error(error):
        log("Sailfish Secrets collection is owned by another application "
            "identity - this happens when mixing SDK-debugger and app-grid "
            "launches. Stored secrets are NOT touched; launch the app "
            "normally from the launcher to access them. (%s)" % error)
    else:
        log("Sailfish Secrets unavailable (%s) - falling back to file store" % error)


def get_config_password(create=True):
    """Return the rclone config password, generating one on first use.

    Returns None if no password exists and create is False.
    """
    with _CACHE_LOCK:
        if _cached_password:
            return _cached_password
    if secrets_client.is_available():
        try:
            value = secrets_client.get_secret(SECRET_NAME)
            if value:
                log("config password loaded from Sailfish Secrets")
                _ensure_open_collection(value)
                # Clean up a leftover legacy file once Secrets works.
                _delete_file()
                return _remember(value)
            legacy = _read_file()
            if legacy:
                secrets_client.set_secret(SECRET_NAME, legacy)
                if secrets_client.get_secret(SECRET_NAME) == legacy:
                    _delete_file()
                    log("config password migrated from file store to Sailfish Secrets")
                    _ensure_open_collection(legacy)
                else:
                    log("WARNING: migration verification failed - keeping file store")
                return _remember(legacy)
            if not create:
                log("no config password stored")
                return None
            password = _generate()
            secrets_client.set_secret(SECRET_NAME, password)
            log("new config password generated and stored in Sailfish Secrets")
            _ensure_open_collection(password)
            return _remember(password)
        except secrets_client.SecretsError as e:
            _log_secrets_failure(e)
    else:
        log("Sailfish Secrets not available on this system - using file store")

    # Emergency fallback: M1-style 0600 file.
    legacy = _read_file()
    if legacy:
        log("config password loaded from file store (fallback)")
        return _remember(legacy)
    if not create:
        log("no config password stored")
        return None
    password = _generate()
    _write_file(password)
    log("new config password generated and stored in FILE fallback "
        "(Sailfish Secrets was not reachable)")
    return _remember(password)


def has_config_password():
    with _CACHE_LOCK:
        if _cached_password:
            return True
    if secrets_client.is_available():
        try:
            if secrets_client.get_secret(SECRET_NAME):
                return True
        except secrets_client.SecretsError:
            pass
    return os.path.exists(_pass_path())


def delete_config_password():
    if secrets_client.is_available():
        try:
            secrets_client.delete_secret(SECRET_NAME)
        except secrets_client.SecretsError as e:
            _log_secrets_failure(e)
    _delete_file()
    invalidate_cache()
    log("config password deleted from store")
