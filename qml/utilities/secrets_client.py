#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - Sailfish Secrets client (AD-04, AD-08c way 1).
# Talks to sailfishsecretsd over its peer-to-peer D-Bus socket. The method
# signatures below were taken from the daemon's introspection XML captured
# by the diagnostics on SFOS 5.0 (see diagnostics test "Secrets D-Bus
# introspection").
#
# SPDX-License-Identifier: Apache-2.0

import os

import common

log = common.make_logger("secrets")

OBJECT_PATH = "/Sailfish/Secrets"
INTERFACE = "org.sailfishos.secrets"
COLLECTION = "ferry"

DEFAULT_STORAGE_PLUGIN = "org.sailfishos.secrets.plugin.storage.sqlite"
DEFAULT_ENCRYPTION_PLUGIN = "org.sailfishos.secrets.plugin.encryption.openssl"

# Sailfish::Secrets enums (values from the sailfish-secrets sources).
DEVICE_LOCK_KEEP_UNLOCKED = 0   # DeviceLockUnlockSemantic
OWNER_ONLY_MODE = 0             # AccessControlMode
NO_ACCESS_CONTROL_MODE = 2      # AccessControlMode (identity-independent)
SYSTEM_INTERACTION = 1          # UserInteractionMode
RESULT_SUCCEEDED = 0            # Result::Code

# The daemon derives an application identity from the calling process;
# launching via the SDK debugger yields a different identity than the app
# grid, which locks OwnerOnlyMode collections (observed on SFOS 5.0).
OWNERSHIP_ERROR_FRAGMENT = "owned by a different application"


def is_ownership_error(error):
    return OWNERSHIP_ERROR_FRAGMENT in str(error).lower()

# InteractionParameters, all defaults: (ssss(i)sa{is}(i)(i))
_EMPTY_UI_PARAMS = ("", "", "", "", (0,), "", {}, (0,), (0,))

_connection = None
_plugin_cache = None


class SecretsError(Exception):
    pass


def _socket_path():
    uid = os.getuid() if hasattr(os, "getuid") else -1
    return "/run/user/%d/sailfishsecretsd/p2pSocket" % uid


def is_available():
    try:
        if not os.path.exists(_socket_path()):
            return False
        import dbus  # noqa: F401
        return True
    except Exception:
        return False


def _get_connection():
    global _connection
    if _connection is None:
        import dbus
        address = "unix:path=%s" % _socket_path()
        log("connecting to secrets daemon at %s" % address)
        _connection = dbus.connection.Connection(address)
    return _connection


def _reset_connection():
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None


def _call(method, signature, args, timeout=30):
    try:
        conn = _get_connection()
        return conn.call_blocking(None, OBJECT_PATH, INTERFACE, method,
                                  signature, args, timeout=timeout)
    except Exception as e:
        _reset_connection()
        raise SecretsError("%s failed: %s" % (method, e))


def _check_result(method, result, tolerate=()):
    """result is the (iis) Result struct: (code, errorCode, errorMessage)."""
    code = int(result[0])
    error_code = int(result[1])
    message = str(result[2])
    if code == RESULT_SUCCEEDED:
        return True
    lowered = message.lower()
    for fragment in tolerate:
        if fragment in lowered:
            log("%s: tolerated result (errorCode=%d): %s" % (method, error_code, message))
            return False
    raise SecretsError("%s failed: code=%d errorCode=%d message=%s"
                       % (method, code, error_code, message))


def _pick_plugin(plugin_structs, preferred_fragment, fallback):
    """PluginInfo is (ssii); the reverse-dns plugin name is one of the two
    string fields."""
    names = []
    for entry in plugin_structs:
        for field in (entry[0], entry[1]):
            text = str(field)
            if text.startswith("org."):
                names.append(text)
                break
    for name in names:
        if preferred_fragment in name:
            return name
    if names:
        return names[0]
    return fallback


def _plugins():
    """Return (storage_plugin, encryption_plugin), queried from the daemon."""
    global _plugin_cache
    if _plugin_cache:
        return _plugin_cache
    try:
        reply = _call("getPluginInfo", "", ())
        result, storage, encryption = reply[0], reply[1], reply[2]
        _check_result("getPluginInfo", result)
        storage_name = _pick_plugin(storage, "storage.sqlite", DEFAULT_STORAGE_PLUGIN)
        encryption_name = _pick_plugin(encryption, "encryption.openssl",
                                       DEFAULT_ENCRYPTION_PLUGIN)
    except SecretsError as e:
        log("getPluginInfo failed (%s) - using default plugin names" % e)
        storage_name, encryption_name = DEFAULT_STORAGE_PLUGIN, DEFAULT_ENCRYPTION_PLUGIN
    _plugin_cache = (storage_name, encryption_name)
    log("using plugins: storage=%s encryption=%s" % _plugin_cache)
    return _plugin_cache


def _create_collection(access_mode):
    storage, encryption = _plugins()
    result = _call("createCollection", "sss(i)(i)",
                   (COLLECTION, storage, encryption,
                    (DEVICE_LOCK_KEEP_UNLOCKED,), (access_mode,)))
    _check_result("createCollection", result, tolerate=("already exist",))


def ensure_collection():
    """Create the app's secrets collection if it does not exist yet.

    New collections use NoAccessControlMode so the launch method (app grid
    vs SDK debugger) does not lock us out of our own collection.
    """
    storage, _ = _plugins()
    try:
        reply = _call("collectionNames", "s", (storage,))
        result, names = reply[0], reply[1]
        _check_result("collectionNames", result)
        if COLLECTION in [str(n) for n in names.keys()]:
            return
    except SecretsError as e:
        log("collectionNames failed (%s) - trying createCollection anyway" % e)
    log("creating secrets collection %r" % COLLECTION)
    try:
        _create_collection(NO_ACCESS_CONTROL_MODE)
    except SecretsError as e:
        # Defensive: fall back if the daemon rejects the access mode value.
        log("createCollection with NoAccessControlMode failed (%s) - "
            "retrying with OwnerOnlyMode" % e)
        _create_collection(OWNER_ONLY_MODE)


def delete_collection():
    """Delete the app's collection including all secrets stored in it."""
    storage, _ = _plugins()
    result = _call("deleteCollection", "ss(i)s",
                   (COLLECTION, storage, (SYSTEM_INTERACTION,), ""))
    _check_result("deleteCollection", result,
                  tolerate=("not exist", "not found", "no such"))
    log("collection %r deleted (or did not exist)" % COLLECTION)


def recreate_collection_open():
    """Delete and recreate the collection with NoAccessControlMode.

    Used by the one-time migration away from OwnerOnlyMode; the caller must
    re-store all secrets afterwards.
    """
    delete_collection()
    ensure_collection()


def _identifier(name):
    storage, _ = _plugins()
    return (name, COLLECTION, storage)


def set_secret(name, value):
    """Store a UTF-8 string secret (value is never logged - DEV-02)."""
    import dbus
    ensure_collection()
    secret = (_identifier(name), dbus.ByteArray(value.encode("utf-8")), {})
    result = _call("setSecret",
                   "((sss)aya{sv})(ssss(i)sa{is}(i)(i))(i)s",
                   (secret, _EMPTY_UI_PARAMS, (SYSTEM_INTERACTION,), ""))
    _check_result("setSecret", result)
    log("secret %r stored" % name)


def get_secret(name):
    """Return the secret as a string, or None if it does not exist."""
    try:
        reply = _call("getSecret", "(sss)(i)s",
                      (_identifier(name), (SYSTEM_INTERACTION,), ""))
    except SecretsError:
        raise
    result, secret = reply[0], reply[1]
    code = int(result[0])
    if code != RESULT_SUCCEEDED:
        message = str(result[2]).lower()
        if "not exist" in message or "not found" in message or "no such" in message \
                or "invalid" in message:
            log("secret %r not found" % name)
            return None
        raise SecretsError("getSecret failed: code=%d errorCode=%d message=%s"
                           % (code, int(result[1]), str(result[2])))
    data = bytes(bytearray(secret[1]))
    log("secret %r loaded" % name)
    return data.decode("utf-8")


def delete_secret(name):
    result = _call("deleteSecret", "(sss)(i)s",
                   (_identifier(name), (SYSTEM_INTERACTION,), ""))
    _check_result("deleteSecret", result,
                  tolerate=("not exist", "not found", "no such", "invalid"))
    log("secret %r deleted (or did not exist)" % name)
