#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - backend plugin discovery.
# Backends are Python modules in qml/utilities/backends/; adding a file
# there makes the backend appear in the UI without further changes.
#
# SPDX-License-Identifier: Apache-2.0

import importlib
import os

import common

log = common.make_logger("backends")

_BACKENDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backends")

# Wording for the top-level containers of a remote, used when a backend does
# not declare its own (see BACKEND["terms"] in backends/*.py).
DEFAULT_TERMS = {"key": "folder", "one": "Folder", "many": "Folders"}


def _module_names():
    return sorted(f[:-3] for f in os.listdir(_BACKENDS_DIR)
                  if f.endswith(".py") and f != "__init__.py")


def get_backend(backend_id):
    """Import and return the backend module for backend_id."""
    module = importlib.import_module("backends.%s" % backend_id)
    if not hasattr(module, "BACKEND"):
        raise ValueError("backend module %r exports no BACKEND definition" % backend_id)
    return module


def _terms_of(info):
    """Merge a backend's wording over the defaults."""
    merged = dict(DEFAULT_TERMS)
    merged.update(info.get("terms") or {})
    return merged


def get_terms(backend_id):
    """Return the container wording of a backend ("library" vs "folder").

    The UI translates by terms["key"] and uses the English "one"/"many"
    strings as the fallback for a key it does not know.
    """
    try:
        return _terms_of(get_backend(backend_id).BACKEND)
    except Exception as e:
        log("terms for %r unavailable (%s) - using the default" % (backend_id, e))
        return dict(DEFAULT_TERMS)


def list_backends():
    """Return [{id, display_name, terms}] for the backend dropdown."""
    result = []
    for name in _module_names():
        try:
            module = get_backend(name)
            info = module.BACKEND
            result.append({"id": info["id"], "display_name": info["display_name"],
                           "terms": _terms_of(info)})
        except Exception as e:
            log("skipping backend %r: %s" % (name, e))
    log("discovered backends: %s" % [b["id"] for b in result])
    return result


def backend_id_for_remote(rclone_type, vendor=""):
    """Map a stored rclone remote back to the backend id that created it.

    Several backends can share an rclone type (webdav serves Nextcloud and
    others), so a backend may pin itself down further via "rclone_vendor".
    Returns "" when no backend matches.
    """
    for name in _module_names():
        try:
            info = get_backend(name).BACKEND
        except Exception as e:
            log("skipping backend %r: %s" % (name, e))
            continue
        if info.get("rclone_type") != rclone_type:
            continue
        expected_vendor = info.get("rclone_vendor", "")
        if expected_vendor and expected_vendor != vendor:
            continue
        return info["id"]
    log("no backend matches remote type=%r vendor=%r" % (rclone_type, vendor))
    return ""


def display_url(backend_id, url):
    """The server URL to show in the UI, in the same shape for every backend.

    Backends that store a technical URL (Nextcloud keeps the full WebDAV
    path) shorten it back via a display_url() function in their module;
    everything else is shown as stored.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    try:
        shorten = getattr(get_backend(backend_id), "display_url", None)
    except Exception as e:
        log("display URL for %r unavailable: %s" % (backend_id, e))
        return url
    if shorten is None:
        return url
    try:
        return shorten(url) or url
    except Exception as e:
        log("display_url() of backend %r failed: %s" % (backend_id, e))
        return url


def get_fields(backend_id):
    """Return the config field definitions for the account form."""
    fields = get_backend(backend_id).BACKEND["config_fields"]
    log("fields for %r: %s" % (backend_id, [f["key"] for f in fields]))
    return fields
