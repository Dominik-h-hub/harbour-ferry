# Backend plugin package for Ferry.
# Each module in this package defines one rclone backend via a BACKEND dict
# and a build_rclone_config(values) function. Modules are discovered at
# runtime; adding a file here makes the backend appear in the UI.


# Shared account form field for the TLS backends (webdav/Nextcloud, Seafile,
# FTPS). Self-hosted servers often carry a certificate that no public
# authority signed - rclone then refuses to connect at all, which is what
# users run into. The switch turns rclone's certificate check off
# (RCLONE_NO_CHECK_CERTIFICATE / RCLONE_FTP_NO_CHECK_CERTIFICATE, set in
# config_manager._rclone_env for every rclone call the app makes).
#
# It is defined once and not per backend on purpose: the wording is a
# security warning, and three copies of it would drift apart. Backends take
# a copy - dict(INSECURE_TLS_FIELD) - so nothing can edit the shared one.
#
# "local": True keeps it out of the rclone remote: the value is stored in
# Ferry's settings (settings_manager, key "insecure_tls") because it has to
# reach every rclone process, including the background sync helper.
#
INSECURE_TLS_FIELD = {
    "key": "insecure_tls",
    "label": "Accept self-signed certificates",
    "description": "Only for a server whose certificate no public authority"
                   " signed. Ferry then accepts any certificate."
                   " Use it only on a server you know.",
    "type": "switch",
    "secret": False,
    "default": False,
    "local": True,
}
