# -*- coding: utf-8 -*-
#
# Ferry backend definition: Seafile (native rclone seafile backend).
#
# SPDX-License-Identifier: Apache-2.0

BACKEND = {
    "id": "seafile",
    "display_name": "Seafile",
    "rclone_type": "seafile",
    "supports_2fa": True,
    "supports_encrypted_libraries": True,
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "label": "Server URL", "type": "url", "secret": False,
         "placeholder": "https://seafile.example.com"},
        {"key": "user", "label": "Username", "type": "text", "secret": False},
        {"key": "pass", "label": "Password", "type": "password", "secret": True},
        {"key": "use_2fa", "label": "Two-factor authentication (2FA)",
         "type": "switch", "secret": False, "default": False, "local": True},
        {"key": "otp", "label": "One-time code (OTP)", "type": "otp",
         "secret": True, "local": True, "visible_if": "use_2fa"},
    ],
}

# Maps rclone's interactive question names (config state machine) to UI
# field keys. Extended once the real question names show up in the logs.
AUTH_ANSWERS = {
    "2fa": "otp",
    "2fa_code": "otp",
    "config_2fa": "otp",
}


def build_rclone_config(values):
    """Build the rclone option key=value dict from the account form values.

    Secret values are passed through; the config manager runs rclone with
    --obscure so passwords never land in the config in plain text.
    """
    config = {
        "url": (values.get("url") or "").strip().rstrip("/"),
        "user": (values.get("user") or "").strip(),
        "2fa": "true" if values.get("use_2fa") else "false",
    }
    password = values.get("pass") or ""
    if password:
        # Omitted when empty so an update keeps the stored password.
        config["pass"] = password
    return config
