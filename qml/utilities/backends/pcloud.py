# -*- coding: utf-8 -*-
#
# Ferry backend definition: pCloud (rclone webdav backend, vendor "other").
#
# rclone does have a native "pcloud" backend, but it authenticates with
# OAuth: the setup opens a browser, lets pCloud issue a token and stores that
# token as a JSON blob. Ferry drives rclone through its non-interactive
# config state machine (config_manager) and its account form only knows
# server, user and password - there is no place for that flow to happen. The
# username/password options of the native backend are no substitute: rclone
# only uses them for the "cleanup" command, the actual transfers still need
# the token.
#
# pCloud's own WebDAV gateway does take the plain account credentials, so
# that is the way in. It is a paid-plan feature; free accounts get a 401
# here, which the connection test reports as a failed login.
#
# Two things the WebDAV gateway cannot do, both on pCloud's side: the Crypto
# Folder stays invisible (its contents are only decryptable in pCloud's own
# clients), and with two-factor authentication switched on a login sends a
# confirmation mail instead of asking for a code - there is nothing for Ferry
# to prompt for, so both switches below are off.
#
# SPDX-License-Identifier: Apache-2.0

from backends import REMOTE_MARKER_KEY

# pCloud runs two independent regions; an account exists in exactly one of
# them and the other endpoint rejects its login. The form is prefilled with
# the US host and names the EU one in its label.
US_HOST = "webdav.pcloud.com"
EU_HOST = "ewebdav.pcloud.com"
US_URL = "https://" + US_HOST

BACKEND = {
    "id": "pcloud",
    "display_name": "pCloud (WebDAV)",
    "rclone_type": "webdav",
    # nextcloud.py also writes webdav remotes and is told apart by its own
    # vendor. "other" is rclone's catch-all vendor - plain WebDAV without any
    # server specific extension - and webdav.py writes exactly the same type
    # and vendor, so the marker below is what separates the two.
    "rclone_vendor": "other",
    # Written into the remote so a stored account finds its way back to this
    # module - see REMOTE_MARKER_KEY in backends/__init__.py. An account
    # saved before this key existed carries no marker and is matched by type
    # and vendor, which still lands here.
    "remote_marker": "pcloud",
    # A second factor is not part of the WebDAV login: pCloud confirms such a
    # login by mail, so there is no code to type into the account form.
    "supports_2fa": False,
    # The Crypto Folder is not reachable over WebDAV at all.
    "supports_encrypted_libraries": False,
    # Plain WebDAV carries no modification times - rclone only sets them for
    # the vendors that understand the X-OC-Mtime header (owncloud, nextcloud,
    # fastmail, rclone). A wrong "True" here would make rclone drop
    # --conflict-resolve and warn on every bisync run, so conflicts keep both
    # versions instead.
    "supports_modtime": False,
    # pCloud has no library concept - the WebDAV root holds plain folders.
    "terms": {"key": "folder", "one": "Folder", "many": "Folders"},
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    #
    # The region lives in the URL rather than in a switch of its own: the
    # account form only restores url, user and password from a stored
    # account, so a switch would silently fall back to its default whenever
    # an existing account is edited and saved again (same reasoning as the
    # FTPS scheme in ftp.py).
    "config_fields": [
        {"key": "url", "text_id": "server_pcloud",
         "label": "Server (EU region: %s)" % EU_HOST,
         # Handed to the translated label as %1 rather than standing inside
         # it: this is a server address, and a typo in any one translation
         # would point the account at nothing.
         "text_arg": EU_HOST,
         "type": "url", "secret": False, "default": US_URL,
         "placeholder": US_URL},
        {"key": "user", "text_id": "pcloud_email",
         "label": "pCloud email address", "type": "text",
         "secret": False},
        {"key": "pass", "text_id": "password", "label": "Password",
         "type": "password", "secret": True},
    ],
}

# The webdav backend asks no interactive authentication questions, so there
# is nothing to map here (see seafile.py for the 2FA case).
AUTH_ANSWERS = {}


def normalize_url(raw_url):
    """Turn the server value from the form into the WebDAV endpoint.

    The two endpoints differ only by the region an account was created in
    (US: webdav.pcloud.com, EU: ewebdav.pcloud.com); the wrong one answers
    with a failed login. Anything the user types is completed to https and
    stripped of a trailing slash, an empty value falls back to the US
    endpoint the form is prefilled with. The WebDAV root is "/", so no path
    is appended.
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return US_URL
    if "://" not in url:
        url = "https://" + url
    return url


def display_url(url):
    """The server URL as the account form shows it.

    Storing and displaying use the same shape here, so this is the exact
    inverse of normalize_url() and the value can be fed straight back into
    the form without the UI mistaking it for a changed account.
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
        # pCloud logs in with the account's email address.
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
