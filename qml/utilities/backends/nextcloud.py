# -*- coding: utf-8 -*-
#
# Ferry backend definition: Nextcloud (rclone webdav backend, vendor
# "nextcloud"). Nextcloud has no dedicated rclone backend - it is configured
# as WebDAV with the nextcloud vendor, which enables chunked uploads and the
# Nextcloud specific metadata handling.
#
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import ssl

from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import common
import version

from backends import INSECURE_TLS_FIELD

log = common.make_logger("nextcloud")

BACKEND = {
    "id": "nextcloud",
    "display_name": "Nextcloud (WebDAV)",
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
    # rclone's webdav backend sets modification times for the nextcloud
    # vendor (X-OC-Mtime header), so bisync can resolve a conflict by
    # picking the newer file.
    "supports_modtime": True,
    # Nextcloud has no library concept - the remote root holds plain folders.
    "terms": {"key": "folder", "one": "Folder", "many": "Folders"},
    # Shown by the connection test when the login worked but the WebDAV path
    # does not exist (404). Without it the user only sees "see details" and
    # cannot tell a wrong password from a wrong path - which is the single
    # most reported setup problem for this backend.
    "not_found_hint":
        "The login worked, but the WebDAV folder was not found. The name in"
        " the WebDAV path has to be the Nextcloud user ID, which is not"
        " always the name you log in with - an e-mail address or a display"
        " name is not. You can also enter the full WebDAV URL in the server"
        " field:\nhttps://cloud.example.com/remote.php/dav/files/USERID",
    # The account form in the UI is generated from these fields.
    # "key" doubles as the rclone option name where applicable; fields with
    # "local": True are UI-only (not passed to rclone as key=value).
    "config_fields": [
        {"key": "url", "text_id": "server_url_nextcloud",
         "label": "Server URL", "type": "url", "secret": False,
         # The placeholder is a single line inside the text field and cannot
         # wrap, so the long form goes into the description below the field
         # where it is readable in full - a WebDAV URL cut off after
         # ".../dav/fil" is worse than no example at all.
         "placeholder": "https://cloud.example.com",
         "description": "The server address, or the full WebDAV URL if you"
                        " know it - Ferry completes a plain server address"
                        " with the WebDAV path of your account.\n"
                        "Example: https://cloud.example.com"
                        "/remote.php/dav/files/USERID"},
        {"key": "user", "text_id": "username", "label": "Username",
         "type": "text", "secret": False},
        {"key": "pass", "text_id": "password_app",
         "label": "Password or app password (with 2FA)",
         "type": "password", "secret": True},
        dict(INSECURE_TLS_FIELD),
    ],
}

# The webdav backend asks no interactive authentication questions, so there
# is nothing to map here (see seafile.py for the 2FA case).
AUTH_ANSWERS = {}

# A URL already pointing at a DAV endpoint is taken as-is; anything else is
# completed to the per-user files endpoint below.
_DAV_MARKERS = ("/remote.php/dav", "/remote.php/webdav")
_FILES_PATH = "/remote.php/dav/files/%s"

# Nextcloud's OCS provisioning endpoint for "who am I". It answers for any
# account with the plain WebDAV credentials - no admin rights needed, and an
# app password works the same way. Both API versions are tried: v1 is the one
# every server still serves, v2 is what a hoster that switched off the old
# one leaves - and a server that blocks one of them answers the other.
_OCS_ENDPOINTS = ("/ocs/v1.php/cloud/user?format=json",
                  "/ocs/v2.php/cloud/user?format=json")
# Per attempt, and only the ones that got an answer are repeated (see
# resolve_user_id), so saving an account cannot stall on a dead server.
_OCS_TIMEOUT = 10

# Hosted Nextcloud instances sit behind a web application firewall, and a
# request identifying itself as "Python-urllib/3.x" - urllib's default - is
# what such a filter is built to reject (observed as a plain 403 from a
# server that answers the same call fine otherwise). Naming the app is both
# honest and what gets the request through.
_USER_AGENT = "Ferry/%s (Sailfish OS)" % version.APP_VERSION


def _normalized(raw_url):
    """The server URL from the form, with a scheme and no trailing slash."""
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return url


def has_dav_path(raw_url):
    """True when the value is a WebDAV URL rather than a server address."""
    lowered = _normalized(raw_url).lower()
    return any(marker in lowered for marker in _DAV_MARKERS)


def webdav_url(raw_url, user):
    """Turn the server URL from the form into the WebDAV endpoint.

    Users enter the address they use in the browser
    (https://cloud.example.com); rclone needs the per-user files endpoint
    (https://cloud.example.com/remote.php/dav/files/<user>). A URL that
    already contains a DAV path is left untouched, which also makes this
    idempotent when an existing account is edited and saved again.

    The name in the path is the Nextcloud user ID. It is usually the login
    name, but not for an account that logs in with an e-mail address or a
    display name - resolve_user_id() below asks the server for the real one.
    """
    url = _normalized(raw_url)
    if not url:
        return ""
    if has_dav_path(url):
        return url
    user = (user or "").strip()
    if not user:
        return url
    return url + _FILES_PATH % quote(user, safe="")


def _server_root(url):
    """The address without the WebDAV path - what the user typed as server."""
    url = (url or "").strip().rstrip("/")
    lowered = url.lower()
    for marker in _DAV_MARKERS:
        index = lowered.find(marker)
        if index >= 0:
            return url[:index]
    return url


def account_identity(url, user):
    """What decides whether a saved account is still the same one.

    Ferry drops the sync pairs when the server or the user changes, because
    the pairs describe the remote side of the previous account. The name in
    the WebDAV path must not take part in that decision: it is resolved from
    the server and its spelling can change (a login "max@x.de" whose user ID
    is "Max@x.de") while the account, its storage and the pairs stay exactly
    the same. Comparing the server and the login instead keeps re-saving an
    account from wiping the pairs over a rewritten path.
    """
    return (_server_root(url), (user or "").strip().lower())


def display_url(url):
    """The server address to print in an overview, without the WebDAV path.

    Purely cosmetic: the stored "<server>/remote.php/dav/files/<ID>" is too
    long for a settings line, and the ID in it says nothing a reader of that
    line needs. Nothing is ever saved back from this value - the account form
    uses form_url() below, which has to stay exact.
    """
    return _server_root(url)


def form_url(url, user=None):
    """The stored URL as the account form should show and return it.

    Whatever the form shows is what gets saved again, so this may only
    shorten when webdav_url() rebuilds the stored URL from it exactly. That
    holds while the path is just the login name; as soon as the ID differs -
    a WebDAV URL the user typed, or an ID resolved from the server - the
    short form would silently rewrite a working account into a broken one on
    the next save, so the full URL is shown and fed back unchanged.
    """
    url = (url or "").strip().rstrip("/")
    short = _server_root(url)
    if short == url:
        return url
    return short if webdav_url(short, user) == url else url


def _built_from_login(url, user):
    """True when the WebDAV path is just the login name, not a real user ID.

    That is what an account looks like before anyone asked the server: the
    one written by an older Ferry version, and the one a fresh setup starts
    from. Such a path is Ferry's own guess and may be corrected; a path that
    differs from the login was either typed by the user or resolved from the
    server, and belongs to neither of us to overwrite.
    """
    return webdav_url(_server_root(url), user) == _normalized(url)


def _ocs_user_id(url, user, password, ssl_context, timeout):
    """Ask one OCS endpoint who the credentials belong to."""
    request = Request(url)
    token = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8"))
    request.add_header("Authorization", "Basic " + token.decode("ascii"))
    # Without this header Nextcloud refuses the OCS call outright.
    request.add_header("OCS-APIRequest", "true")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", _USER_AGENT)
    with urlopen(request, timeout=timeout, context=ssl_context) as answer:
        data = json.loads(answer.read().decode("utf-8", "replace"))
    user_id = ((data.get("ocs") or {}).get("data") or {}).get("id") or ""
    return str(user_id).strip()


def resolve_user_id(server, user, password, insecure_tls=False,
                    timeout=_OCS_TIMEOUT):
    """The Nextcloud user ID behind a login name, as (user_id, reason).

    The WebDAV path needs the user ID. People log in with an e-mail address,
    a display name or an alias and the server accepts that - but
    /remote.php/dav/files/<login> then belongs to nobody, and every listing
    answers 404 while the login itself looks fine.

    Any failure returns ("", reason) so the caller falls back to the typed
    name: a lookup must never be what stops an account from being saved. The
    reason says what the server did, because a silently skipped lookup is
    indistinguishable from one that was never needed.
    """
    server = _normalized(server)
    user = (user or "").strip()
    if not server or not user or not password:
        return "", "nothing to ask with"
    if insecure_tls:
        # The "Accept self-signed certificates" switch of the account form.
        # Without honouring it here the lookup would fail on exactly the
        # self-hosted servers this backend is mostly used with, and the
        # silent fallback would make the whole lookup look like a no-op.
        ssl_context = ssl._create_unverified_context()
    else:
        ssl_context = ssl.create_default_context()
    reason = ""
    for endpoint in _OCS_ENDPOINTS:
        try:
            user_id = _ocs_user_id(server + endpoint, user, password,
                                   ssl_context, timeout)
        except HTTPError as e:
            # The server answered, just not with the data: an OCS version it
            # does not serve, or a filter in front of it. The other endpoint
            # is worth a try - a server that is simply unreachable is not,
            # which is why only this branch continues the loop.
            reason = "the server answered HTTP %s" % e.code
            log("user ID lookup on %s failed: %s" % (endpoint, e))
            continue
        except Exception as e:
            reason = "the server could not be asked"
            log("user ID lookup failed (%s) - using the name as typed" % e)
            return "", reason
        if not user_id:
            reason = "the server named no user ID"
            log("user ID lookup on %s returned no ID" % endpoint)
            continue
        if user_id != user:
            log("login %r is user ID %r on this server" % (user, user_id))
        return user_id, ""
    return "", (reason or "the server named no user ID")


def prepare_account(values, params, context):
    """Backend hook: put the real user ID into the WebDAV path.

    Runs after build_rclone_config() and before anything is written, so the
    account is created with a path that exists. Returns (params, step); step
    is a line for the result page, or None when there was nothing to do.
    """
    user = (values.get("user") or "").strip()
    if has_dav_path(values.get("url")) \
            and not _built_from_login(values.get("url"), user):
        # A path the user typed, or an ID this hook resolved earlier: it is
        # already the right one, and asking again would only cost a round
        # trip. A path that is merely the login name does get corrected -
        # that is how an account written by an older version is repaired
        # when it is saved the next time.
        return params, None
    password = values.get("pass") or ""
    if not user or not password:
        # An edit that keeps the stored password: nothing to authenticate the
        # lookup with, and the stored URL already works or already does not.
        return params, None
    server = _server_root(values.get("url"))
    user_id, reason = resolve_user_id(
        server, user, password,
        insecure_tls=bool(context.get("insecure_tls")))
    if not user_id:
        # Not an error: the account is saved with the name as typed, which is
        # the right path for most servers. Naming what the server said is
        # what makes the line worth reading when the test then fails.
        return params, {"title": "Look up user ID", "ok": True,
                        "detail": "%s - using the name as typed" % reason}
    params = dict(params)
    params["url"] = webdav_url(server, user_id)
    if user_id == user:
        return params, {"title": "Look up user ID", "ok": True,
                        "detail": user_id}
    return params, {"title": "Look up user ID", "ok": True,
                    "detail": "the user ID of this login is \"%s\" - the"
                              " WebDAV path uses the ID" % user_id}


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
