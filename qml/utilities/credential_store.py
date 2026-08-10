#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - credential store for the rclone config password (AD-04).
#
# M2: primary storage is Sailfish Secrets via the daemon's P2P D-Bus socket
# (secrets_client). A password stored by the M1 interim file store is
# migrated into Secrets automatically on first access. The file store
# remains only as an emergency fallback when Secrets is unavailable.
#
# SPDX-License-Identifier: Apache-2.0

import os
import secrets

import common
import secrets_client

log = common.make_logger("credstore")

SECRET_NAME = "rclone-config-password"
_PASS_FILENAME = "config-pass"


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
    """One-time migration: recreate the collection with NoAccessControlMode
    so the SDK-debugger identity cannot lock us out again. Only runs while
    we have working access and the password in hand - never destructive."""
    marker = _open_collection_marker()
    if os.path.exists(marker):
        return
    try:
        log("migrating secrets collection to open access mode")
        secrets_client.recreate_collection_open()
        secrets_client.set_secret(SECRET_NAME, password)
        if secrets_client.get_secret(SECRET_NAME) == password:
            os.makedirs(common.data_dir(), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                f.write("migrated\n")
            log("secrets collection migrated to open access mode")
        else:
            log("WARNING: open-collection migration verification failed")
    except secrets_client.SecretsError as e:
        log("open-collection migration failed (%s) - will retry next start" % e)


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
    if secrets_client.is_available():
        try:
            value = secrets_client.get_secret(SECRET_NAME)
            if value:
                log("config password loaded from Sailfish Secrets")
                _ensure_open_collection(value)
                # Clean up a leftover legacy file once Secrets works.
                _delete_file()
                return value
            legacy = _read_file()
            if legacy:
                secrets_client.set_secret(SECRET_NAME, legacy)
                if secrets_client.get_secret(SECRET_NAME) == legacy:
                    _delete_file()
                    log("config password migrated from file store to Sailfish Secrets")
                    _ensure_open_collection(legacy)
                else:
                    log("WARNING: migration verification failed - keeping file store")
                return legacy
            if not create:
                log("no config password stored")
                return None
            password = _generate()
            secrets_client.set_secret(SECRET_NAME, password)
            log("new config password generated and stored in Sailfish Secrets")
            _ensure_open_collection(password)
            return password
        except secrets_client.SecretsError as e:
            _log_secrets_failure(e)
    else:
        log("Sailfish Secrets not available on this system - using file store")

    # Emergency fallback: M1-style 0600 file.
    legacy = _read_file()
    if legacy:
        log("config password loaded from file store (fallback)")
        return legacy
    if not create:
        log("no config password stored")
        return None
    password = _generate()
    _write_file(password)
    log("new config password generated and stored in FILE fallback "
        "(Sailfish Secrets was not reachable)")
    return password


def has_config_password():
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
    log("config password deleted from store")
