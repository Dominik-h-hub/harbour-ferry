# -*- coding: utf-8 -*-
#
# Ferry backend definition: SFTP (native rclone sftp backend - file transfer
# over SSH, unrelated to FTP/FTPS).
#
# rclone configures this backend with "host" and "port"; Ferry identifies an
# account by ("url", "user"), so the form keeps a single "url" field which is
# split via the hostport helper (see hostport.py for why that key is stored).
#
# Password authentication only: the account form makes the password field
# mandatory for a new account (AccountPage.qml, canAccept), so a key-file
# login could not be saved without changing the form itself.
#
# SPDX-License-Identifier: Apache-2.0

import hostport

BACKEND = {
    "id": "sftp",
    "display_name": "SFTP (SSH)",
    "rclone_type": "sftp",
    # SSH keyboard-interactive with a second factor is not something rclone
    # can answer non-interactively.
    "supports_2fa": False,
    # Server-side encrypted containers do not exist on SFTP.
    "supports_encrypted_libraries": False,
    # rclone sets modification times through the SFTP protocol itself
    # (setstat, option "set_modtime", on by default), with 1 second
    # precision and without needing shell access on the server. bisync
    # therefore gets --conflict-resolve newer.
    "supports_modtime": True,
    # An SFTP root holds plain folders, no library concept.
    "terms": {"key": "folder", "one": "Folder", "many": "Folders"},
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "label": "Server (host or host:port)", "type": "url",
         "secret": False, "placeholder": "sftp.example.com"},
        {"key": "user", "label": "Username", "type": "text", "secret": False},
        {"key": "pass", "label": "Password", "type": "password", "secret": True},
    ],
}

# The sftp backend asks no interactive questions in --non-interactive mode.
AUTH_ANSWERS = {}

_DEFAULT_PORT = 22

# SSH is always encrypted, so unlike FTP the scheme carries no setting. It
# is accepted on input (a pasted "sftp://host") and dropped for display.
_SCHEME = "sftp"


def display_url(url):
    """Reduce the stored URL to what the account form shows."""
    _scheme, host, port = hostport.split(url, _DEFAULT_PORT)
    return hostport.display(host, port, _DEFAULT_PORT)


def build_rclone_config(values):
    """Build the rclone option key=value dict from the account form values.

    Secret values are passed through; the config manager runs rclone with
    --obscure so passwords never land in the config in plain text.
    """
    _scheme, host, port = hostport.split(values.get("url"), _DEFAULT_PORT)
    config = {
        "host": host,
        "port": str(port),
        "user": (values.get("user") or "").strip(),
        # Ferry's account identity - ignored by rclone itself.
        "url": hostport.join(_SCHEME, host, port),
    }
    password = values.get("pass") or ""
    if password:
        # Omitted when empty so an update keeps the stored password.
        config["pass"] = password
    return config
