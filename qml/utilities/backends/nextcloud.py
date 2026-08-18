# -*- coding: utf-8 -*-
#
# Ferry backend definition: Nextcloud (rclone webdav backend, vendor
# "nextcloud"). Nextcloud has no dedicated rclone backend - it is configured
# as WebDAV with the nextcloud vendor, which enables chunked uploads and the
# Nextcloud specific metadata handling.
#
# SPDX-License-Identifier: Apache-2.0

try:
    from urllib.parse import quote
except ImportError:  # pragma: no cover - Python 2 is not a target
    from urllib import quote

BACKEND = {
    "id": "nextcloud",
    "display_name": "Nextcloud",
    "rclone_type": "webdav",
    # rclone's webdav backend serves several services; the vendor pins it to
    # Nextcloud and is also how a stored remote is recognised again.
    "rclone_vendor": "nextcloud",
    # Nextcloud's 2FA is not part of the WebDAV login: an account with 2FA
    # needs an app password instead of the account password, so there is no
    # OTP question to answer during setup.
    "supports_2fa": False,
    # Server-side end-to-end encryption is not supported by rclone's webdav
    # backend - encrypted folders are not usable through this backend.
    "supports_encrypted_libraries": False,
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "label": "Server URL", "type": "url", "secret": False,
         "placeholder": "https://cloud.example.com"},
        {"key": "user", "label": "Username", "type": "text", "secret": False},
        {"key": "pass", "label": "Password or app password (with 2FA)",
         "type": "password", "secret": True},
    ],
}

# The webdav backend asks no interactive authentication questions, so there
# is nothing to map here (see seafile.py for the 2FA case).
AUTH_ANSWERS = {}

# A URL already pointing at a DAV endpoint is taken as-is; anything else is
# completed to the per-user files endpoint below.
_DAV_MARKERS = ("/remote.php/dav", "/remote.php/webdav")
_FILES_PATH = "/remote.php/dav/files/%s"


def webdav_url(raw_url, user):
    """Turn the server URL from the form into the WebDAV endpoint.

    Users enter the address they use in the browser
    (https://cloud.example.com); rclone needs the per-user files endpoint
    (https://cloud.example.com/remote.php/dav/files/<user>). A URL that
    already contains a DAV path is left untouched, which also makes this
    idempotent when an existing account is edited and saved again.
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    if any(marker in url.lower() for marker in _DAV_MARKERS):
        return url
    user = (user or "").strip()
    if not user:
        return url
    return url + _FILES_PATH % quote(user, safe="")


def build_rclone_config(values):
    """Build the rclone option key=value dict from the account form values.

    Secret values are passed through; the config manager runs rclone with
    --obscure so passwords never land in the config in plain text.
    """
    user = (values.get("user") or "").strip()
    config = {
        "url": webdav_url(values.get("url"), user),
        "vendor": BACKEND["rclone_vendor"],
        "user": user,
    }
    password = values.get("pass") or ""
    if password:
        # Omitted when empty so an update keeps the stored password.
        config["pass"] = password
    return config
