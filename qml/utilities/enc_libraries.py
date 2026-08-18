#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - encrypted Seafile libraries (FR-04).
# The library password is stored (rclone-obscured) in Sailfish Secrets; at
# runtime the remote is addressed via an rclone connection string
# ("remote,library=...,library_key=...:path") so no extra config section is
# needed. Known encrypted libraries are tracked in a small registry file so
# they can be marked with a lock icon (section 5.1).
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import threading

import common
import secrets_client

log = common.make_logger("enclibs")

_LOCK = threading.Lock()


def _registry_path():
    return os.path.join(common.data_dir(), "encrypted-libraries.json")


def _load_names():
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            return list(json.load(f).get("libraries", []))
    except (OSError, ValueError):
        return []


def known_libraries():
    with _LOCK:
        return _load_names()


def is_encrypted(name):
    return name in known_libraries()


def register(name):
    with _LOCK:
        names = _load_names()
        if name not in names:
            names.append(name)
            os.makedirs(common.data_dir(), exist_ok=True)
            with open(_registry_path(), "w", encoding="utf-8") as f:
                json.dump({"libraries": names}, f, indent=2)
            log("library %r registered as encrypted" % name)


def _secret_name(library):
    return "library-key-%s" % library


def store_key(library, obscured_key):
    """Store the rclone-obscured library password in Sailfish Secrets."""
    secrets_client.set_secret(_secret_name(library), obscured_key)
    register(library)
    log("key for library %r stored (value not logged)" % library)


def get_key(library):
    """Return the obscured library key, or None."""
    try:
        return secrets_client.get_secret(_secret_name(library))
    except secrets_client.SecretsError as e:
        log("key lookup for %r failed: %s" % (library, e))
        return None


def forget(library):
    try:
        secrets_client.delete_secret(_secret_name(library))
    except secrets_client.SecretsError as e:
        log("key delete for %r failed: %s" % (library, e))
    with _LOCK:
        names = [n for n in _load_names() if n != library]
        os.makedirs(common.data_dir(), exist_ok=True)
        with open(_registry_path(), "w", encoding="utf-8") as f:
            json.dump({"libraries": names}, f, indent=2)
    log("library %r removed from encrypted registry" % library)


def forget_all():
    """Drop every registered encrypted library and its stored key.

    The registry belongs to one account - keeping it across an account change
    would mark same-named folders of the new account as encrypted and route
    them through a connection string with a key that does not fit.
    """
    names = known_libraries()
    for name in names:
        forget(name)
    with _LOCK:
        try:
            os.remove(_registry_path())
        except OSError:
            pass
    log("encrypted library registry cleared (%d entries)" % len(names))
    return len(names)


def _quote(value):
    """Quote a connection string value if it contains special characters."""
    if any(c in value for c in ',:"= '):
        return '"%s"' % value.replace('"', '""')
    return value


def build_target(remote_name, path):
    """Build the rclone target for a remote path, transparently routing
    paths inside known encrypted libraries through a connection string."""
    path = (path or "").strip("/")
    if path:
        first, _, rest = path.partition("/")
        if is_encrypted(first):
            key = get_key(first)
            if key:
                return "%s,library=%s,library_key=%s:%s" % (
                    remote_name, _quote(first), _quote(key), rest)
            log("library %r is encrypted but no key is stored" % first)
    return "%s:%s" % (remote_name, path)
