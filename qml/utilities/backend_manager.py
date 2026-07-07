#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Sailfile - backend plugin discovery (AD-09c).
# Backends are Python modules in qml/utilities/backends/; adding a file
# there makes the backend appear in the UI without further changes.
#
# SPDX-License-Identifier: Apache-2.0

import importlib
import os

import common

log = common.make_logger("backends")

_BACKENDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backends")


def _module_names():
    return sorted(f[:-3] for f in os.listdir(_BACKENDS_DIR)
                  if f.endswith(".py") and f != "__init__.py")


def get_backend(backend_id):
    """Import and return the backend module for backend_id."""
    module = importlib.import_module("backends.%s" % backend_id)
    if not hasattr(module, "BACKEND"):
        raise ValueError("backend module %r exports no BACKEND definition" % backend_id)
    return module


def list_backends():
    """Return [{id, display_name}] for the backend dropdown (FR-01a)."""
    result = []
    for name in _module_names():
        try:
            module = get_backend(name)
            info = module.BACKEND
            result.append({"id": info["id"], "display_name": info["display_name"]})
        except Exception as e:
            log("skipping backend %r: %s" % (name, e))
    log("discovered backends: %s" % [b["id"] for b in result])
    return result


def get_fields(backend_id):
    """Return the config field definitions for the account form (AD-09b)."""
    fields = get_backend(backend_id).BACKEND["config_fields"]
    log("fields for %r: %s" % (backend_id, [f["key"] for f in fields]))
    return fields
