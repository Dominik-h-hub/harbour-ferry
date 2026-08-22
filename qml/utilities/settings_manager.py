#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - app settings (FR-15, FR-18, FR-19).
# Stored as JSON in the app config directory.
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import threading

import common

try:
    import pyotherside
    HAVE_PYOTHERSIDE = True
except ImportError:
    HAVE_PYOTHERSIDE = False

log = common.make_logger("settings")

_LOCK = threading.Lock()

DEFAULTS = {
    "interval": "manual",        # FR-18: manual/15min/30min/1h/6h/12h
    "network_rule": "wifi",      # FR-19: "wifi" or "any"
    "excludes": [".thumbnails/**", "*.tmp", "*~", ".~lock*", ".cache/**"],
    "max_delete": 50,            # FR-14: percent
}


def _store_path():
    return os.path.join(common.config_dir(), "settings.json")


def get_settings():
    with _LOCK:
        merged = dict(DEFAULTS)
        try:
            with open(_store_path(), encoding="utf-8") as f:
                merged.update(json.load(f))
        except (OSError, ValueError):
            pass
        return merged


def get(key):
    return get_settings().get(key, DEFAULTS.get(key))


def set_setting(key, value):
    if key == "max_delete":
        # QML sliders deliver floats; rclone --max-delete needs an integer.
        value = int(round(float(value)))
    with _LOCK:
        data = dict(DEFAULTS)
        try:
            with open(_store_path(), encoding="utf-8") as f:
                data.update(json.load(f))
        except (OSError, ValueError):
            pass
        data[key] = value
        os.makedirs(common.config_dir(), exist_ok=True)
        with open(_store_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    log("setting saved: %s = %r" % (key, value))
    return True


def set_excludes_text(text):
    """Excludes as edited in the UI: one pattern per line (FR-15)."""
    patterns = [line.strip() for line in text.splitlines() if line.strip()]
    return set_setting("excludes", patterns)


def apply_all_background(new_settings):
    """Save all settings from the settings dialog and (re)apply the timer;
    the result arrives via the 'settings-applied' event. Runs detached so
    the dialog can close immediately."""
    def worker():
        import timer_manager
        if "network_rule" in new_settings:
            set_setting("network_rule", new_settings["network_rule"])
        if "max_delete" in new_settings:
            set_setting("max_delete", new_settings["max_delete"])
        if "excludes_text" in new_settings:
            set_excludes_text(new_settings["excludes_text"])
        interval = new_settings.get("interval", get("interval"))
        set_setting("interval", interval)
        result = timer_manager.apply_interval(interval)
        log("settings applied: %s" % result["message"])
        if HAVE_PYOTHERSIDE:
            pyotherside.send("settings-applied", result)

    threading.Thread(target=worker, name="ferry-settings").start()
    return True
