#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - encrypted Seafile libraries.
# The library password is stored (rclone-obscured) in Sailfish Secrets; at
# runtime the remote is addressed via an rclone connection string
# ("remote,library=...,library_key=...:path") so no extra config section is
# needed. Known encrypted libraries are tracked in a small registry file so
# they can be marked with a lock icon.
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import threading

import common
import secrets_client

log = common.make_logger("enclibs")

_LOCK = threading.Lock()

# Process caches. Both the registry and the library keys are consulted on
# every listing (once per entry at the root) and before every rclone target
# is built, and each key lookup is a blocking D-Bus round trip to
# sailfishsecretsd. This module is the only writer, so invalidating in
# register/forget/store_key is enough. A "no key" result is cached as well -
# store_key() drops it again the moment one arrives.
_CACHE_LOCK = threading.Lock()
_names_cache = None
_key_cache = {}


def _invalidate(library=None):
    """Drop the cached registry, and one or all cached keys."""
    global _names_cache
    with _CACHE_LOCK:
        _names_cache = None
        if library is None:
            _key_cache.clear()
        else:
            _key_cache.pop(library, None)


def _registry_path():
    return os.path.join(common.data_dir(), "encrypted-libraries.json")


def _load_names():
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            return list(json.load(f).get("libraries", []))
    except (OSError, ValueError):
        return []


def known_libraries():
    global _names_cache
    with _CACHE_LOCK:
        if _names_cache is not None:
            return list(_names_cache)
    with _LOCK:
        names = _load_names()
    with _CACHE_LOCK:
        _names_cache = list(names)
    return names


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
            _invalidate()
            log("library %r registered as encrypted" % name)


def _secret_name(library):
    return "library-key-%s" % library


def stored_keys():
    """Return {secret name: key} for every registered encrypted library.

    Used by the secrets-collection migration, which has to write every secret
    back after the collection was recreated. Reads through the daemon on
    purpose - the process cache may not know all of them yet. A SecretsError
    propagates: an incomplete picture must not be treated as "nothing to
    preserve".
    """
    keys = {}
    for library in known_libraries():
        key = secrets_client.get_secret(_secret_name(library))
        if key:
            keys[_secret_name(library)] = key
    return keys


def store_key(library, obscured_key):
    """Store the rclone-obscured library password in Sailfish Secrets."""
    secrets_client.set_secret(_secret_name(library), obscured_key)
    register(library)
    with _CACHE_LOCK:
        _key_cache[library] = obscured_key
    log("key for library %r stored (value not logged)" % library)


def get_key(library):
    """Return the obscured library key, or None."""
    with _CACHE_LOCK:
        if library in _key_cache:
            return _key_cache[library]
    try:
        key = secrets_client.get_secret(_secret_name(library))
    except secrets_client.SecretsError as e:
        # Not cached: the daemon may become reachable again later.
        log("key lookup for %r failed: %s" % (library, e))
        return None
    with _CACHE_LOCK:
        _key_cache[library] = key
    return key


def forget(library):
    _invalidate(library)
    try:
        secrets_client.delete_secret(_secret_name(library))
    except secrets_client.SecretsError as e:
        log("key delete for %r failed: %s" % (library, e))
    with _LOCK:
        names = [n for n in _load_names() if n != library]
        os.makedirs(common.data_dir(), exist_ok=True)
        with open(_registry_path(), "w", encoding="utf-8") as f:
            json.dump({"libraries": names}, f, indent=2)
    _invalidate(library)
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
    _invalidate()
    log("encrypted library registry cleared (%d entries)" % len(names))
    return len(names)


# Phrases that identify a library which needs its password - from Seafile
# ("Repo is encrypted. Please provide password to view it.") and from rclone
# itself ("incorrect password"). Deliberately whole phrases: a bare
# "password" would also match a file called "Passwords.txt" in a sync log.
_LOCKED_MARKERS = (
    "repo is encrypted",
    "library is encrypted",
    "encrypted library",
    "incorrect password",
    "provide password",
    "password required",
    "password to view",
)


def locked_library(path):
    """The library in path that is known to be encrypted but has no key.

    Returns "" when the path is fine to work with - either because the
    library is not encrypted, or because its key is stored.
    """
    library = (path or "").strip("/").split("/")[0]
    if library and is_encrypted(library) and not get_key(library):
        return library
    return ""


def encrypted_library(output, path):
    """Return the library whose password rclone is missing, or "".

    Seafile encrypts only the *content* of an encrypted library: listing it,
    and even creating folders in it, works without the key, so the failure
    surfaces late - when a file is actually transferred or synced. Every
    caller that runs into it has to be able to hand the user over to the
    unlock dialog. A library that turns out to be encrypted is remembered
    right away, so the lock marker appears and the next run can refuse
    before doing any work.
    """
    library = (path or "").strip("/").split("/")[0]
    if not library:
        return ""
    lowered = (output or "").lower()
    if any(marker in lowered for marker in _LOCKED_MARKERS):
        if not is_encrypted(library):
            register(library)
        return library
    # No matching words, but the library is known to be encrypted and no key
    # is stored - then a failure is the missing password, whatever wording
    # the server used.
    if locked_library(path):
        log("library %r is encrypted and locked - treating the failure as a"
            " missing password" % library)
        return library
    return ""


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
