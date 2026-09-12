# -*- coding: utf-8 -*-
#
# Ferry backend definition: FTP / FTPS (native rclone ftp backend).
#
# rclone configures this backend with "host" and "port"; Ferry identifies an
# account by ("url", "user"), so the form keeps a single "url" field which is
# split via the hostport helper (see hostport.py for why that key is stored).
#
# There is no separate TLS switch: the scheme in that field decides.
# "ftp://" means plain FTP, everything else (including no scheme at all)
# means explicit FTPS. That keeps the setting where the user can see it -
# a UI-only switch would fall back to its default whenever an existing
# account is edited, because the account form only restores url, user,
# password and 2FA from the stored config.
#
# SPDX-License-Identifier: Apache-2.0

import hostport

from backends import INSECURE_TLS_FIELD

BACKEND = {
    "id": "ftp",
    "display_name": "FTP / FTPS",
    "rclone_type": "ftp",
    # FTP knows no second factor - user and password are all there is.
    "supports_2fa": False,
    # Server-side encrypted containers do not exist on FTP.
    "supports_encrypted_libraries": False,
    # Modification times depend on the server (MLSD/MDTM/MFMT): ProFTPd,
    # PureFTPd, VsFTPd and FileZilla Server manage 1 second precision,
    # anything else reports the upload time. A wrong "True" would make
    # rclone warn on every bisync run, so this stays False and conflicts
    # keep both versions.
    "supports_modtime": False,
    # An FTP root holds plain folders, no library concept.
    "terms": {"key": "folder", "one": "Folder", "many": "Folders"},
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "text_id": "server_ftp",
         "label": "Server (ftps:// - ftp:// is unencrypted)",
         "type": "url", "secret": False,
         "placeholder": "ftps://ftp.example.com"},
        {"key": "user", "text_id": "username", "label": "Username",
         "type": "text", "secret": False},
        {"key": "pass", "text_id": "password", "label": "Password",
         "type": "password", "secret": True},
        dict(INSECURE_TLS_FIELD),
    ],
}

# The ftp backend asks no interactive questions in --non-interactive mode.
AUTH_ANSWERS = {}

# Explicit FTPS (AUTH TLS) runs on the plain FTP port; implicit FTPS on 990
# would be rclone's "tls" option and is not offered here.
_DEFAULT_PORT = 21

# Only this scheme turns encryption off; an empty one defaults to FTPS,
# because plain FTP sends the password in the clear.
_PLAIN_SCHEME = "ftp"


def _scheme_of(raw_url):
    """(scheme to store, host, port, tls) for a value from the form."""
    scheme, host, port = hostport.split(raw_url, _DEFAULT_PORT)
    use_tls = scheme != _PLAIN_SCHEME
    return ("ftps" if use_tls else "ftp"), host, port, use_tls


def display_url(url):
    """Reduce the stored URL to what the account form shows.

    Keeps the scheme: it is the only place the FTPS setting is visible, and
    feeding the result back into the form has to produce the same config.
    """
    scheme, host, port, _tls = _scheme_of(url)
    return hostport.display(host, port, _DEFAULT_PORT, scheme)


def build_rclone_config(values):
    """Build the rclone option key=value dict from the account form values.

    Secret values are passed through; the config manager runs rclone with
    --obscure so passwords never land in the config in plain text.
    """
    scheme, host, port, use_tls = _scheme_of(values.get("url"))
    config = {
        "host": host,
        "port": str(port),
        "user": (values.get("user") or "").strip(),
        # Explicit FTPS (AUTH TLS on the normal port) is what nearly every
        # server offers. rclone only speaks passive mode.
        "explicit_tls": "true" if use_tls else "false",
        # Ferry's account identity - ignored by rclone itself.
        "url": hostport.join(scheme, host, port),
    }
    password = values.get("pass") or ""
    if password:
        # Omitted when empty so an update keeps the stored password.
        config["pass"] = password
    return config
