# -*- coding: utf-8 -*-
#
# Ferry backend definition: plain WebDAV (rclone webdav backend, vendor
# "other").
#
# SPDX-License-Identifier: Apache-2.0

from backends import INSECURE_TLS_FIELD, REMOTE_MARKER_KEY

BACKEND = {
    "id": "webdav",
    "display_name": "WebDAV",
    "rclone_type": "webdav",
    # rclone's catch-all vendor: plain WebDAV without any server specific
    # extension. pcloud.py writes the same type and vendor, which is why
    # this backend also carries the marker below.
    "rclone_vendor": "other",
    # Written into the remote so a stored account finds its way back to this
    # module - see REMOTE_MARKER_KEY in backends/__init__.py.
    "remote_marker": "webdav",
    # HTTP Basic authentication is user and password, and a second factor is
    # not part of it. A server behind one needs an app password, exactly as
    # with Nextcloud.
    "supports_2fa": False,
    # Server-side encrypted containers are a Seafile concept; WebDAV has
    # nothing of the sort.
    "supports_encrypted_libraries": False,
    # Plain WebDAV carries no modification times: rclone only sets them for
    # the vendors that understand the X-OC-Mtime header (owncloud,
    # nextcloud, fastmail, rclone). A wrong "True" here would make rclone
    # drop --conflict-resolve and warn on every bisync run, so conflicts
    # keep both versions instead.
    "supports_modtime": False,
    # A WebDAV root holds plain folders, no library concept.
    "terms": {"key": "folder", "one": "Folder", "many": "Folders"},
    # Shown by the connection test when the login worked but the address
    # points at nothing (404). For this backend that is nearly always the
    # path: the server answers on "/" with the login accepted while the
    # share itself sits somewhere below it.
    "not_found_hint":
        "The login worked, but the address points at no folder. A WebDAV"
        " share is usually not at the root of the server - add the path it"
        " is served under to the server URL:\n"
        "https://dav.example.com/dav",
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "text_id": "server_url_webdav",
         "label": "Server URL", "type": "url", "secret": False,
         # The placeholder is one line inside the text field and cannot
         # wrap; everything that needs more room goes into the description
         # below the field (same reasoning as in nextcloud.py).
         "placeholder": "https://dav.example.com/dav",
         "description": "The full address of the WebDAV share, including the"
                        " path it is served under. A port only where it is"
                        " not the default of the scheme"
                        " (https://dav.example.com:8443/dav).\n"
                        "Without a scheme Ferry uses https - put http:// in"
                        " front of the address for a server without TLS,"
                        " which sends the password in the clear."},
        {"key": "user", "text_id": "username", "label": "Username",
         "type": "text", "secret": False},
        {"key": "pass", "text_id": "password", "label": "Password",
         "type": "password", "secret": True},
        dict(INSECURE_TLS_FIELD),
    ],
}

# The webdav backend asks no interactive authentication questions, so there
# is nothing to map here (see seafile.py for the 2FA case).
AUTH_ANSWERS = {}

# What a server address is completed to when the user typed no scheme.
# https, because the alternative sends the password in the clear and a
# server that only speaks http is the rarer case worth typing out.
_DEFAULT_SCHEME = "https"


def normalize_url(raw_url):
    """The server URL from the form as it is stored for rclone.

    Only two things are decided here: a missing scheme becomes https, and a
    trailing slash goes away so that saving the same account twice produces
    the same string - a difference there reads as "another server" and wipes
    the sync pairs (config_manager._account_identity_changed). The path is
    whatever the user typed; only the server knows where its share lives.
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "%s://%s" % (_DEFAULT_SCHEME, url)
    return url


def display_url(url):
    """The stored URL as the account form shows and hands back.

    Storing and displaying are the same shape here, so this is the exact
    inverse of normalize_url() and the value can go straight back into the
    form without the UI reading it as a changed account.
    """
    return normalize_url(url)


def build_rclone_config(values):
    """Build the rclone option key=value dict from the account form values.

    Secret values are passed through; the config manager runs rclone with
    --obscure so passwords never land in the config in plain text.
    """
    config = {
        "url": normalize_url(values.get("url")),
        "vendor": BACKEND["rclone_vendor"],
        "user": (values.get("user") or "").strip(),
        # Ferry's own key - ignored by rclone, read by
        # backend_manager.backend_id_for_remote.
        REMOTE_MARKER_KEY: BACKEND["remote_marker"],
    }
    password = values.get("pass") or ""
    if password:
        # Omitted when empty so an update keeps the stored password.
        config["pass"] = password
    return config
