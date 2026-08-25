#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - host/port helper for the backends that rclone configures with
# "host" and "port" instead of a URL (FTP, SFTP).
#
# Ferry identifies an account by ("url", "user"): the account form, the
# summary and the "server changed" detection all read "url" (see
# config_manager._account_identity). Those backends therefore keep a single
# "url" field in the UI, split it here into host/port for rclone and store
# the normalised URL alongside - rclone ignores the extra key, so no backend
# needs a special case in config_manager or the QML form.
#
# This module lives next to the other utilities, not in backends/: every .py
# in that directory is treated as a backend module by backend_manager and a
# helper without a BACKEND definition would be logged as "skipping backend"
# on every account lookup.
#
# SPDX-License-Identifier: Apache-2.0

from urllib.parse import urlsplit


def _bracket(host):
    """Wrap a bare IPv6 address for use in a URL ("::1" -> "[::1]")."""
    if ":" in host and not host.startswith("["):
        return "[%s]" % host
    return host


def _split_host_port(text, default_port):
    """Split "host", "host:port", "[::1]" or "[::1]:port"."""
    if text.startswith("["):
        host, _, rest = text.partition("]")
        host = host[1:]
        port_text = rest[1:] if rest.startswith(":") else ""
    elif ":" in text:
        host, _, port_text = text.rpartition(":")
    else:
        host, port_text = text, ""
    if not port_text:
        return host, default_port
    try:
        return host, int(port_text)
    except ValueError:
        # Not a port at all (a bare IPv6 address without brackets, a typo):
        # keep the whole thing as the host and let rclone report the error.
        return text, default_port


def split(raw_url, default_port):
    """Return (scheme, host, port) for whatever the user typed.

    Accepts "host", "host:port", "scheme://host:port", a pasted
    "scheme://user@host/path" and bracketed IPv6 literals. The scheme is
    lower-cased and empty when none was given; user and path are dropped -
    the username has its own field and the remote path is chosen later in
    the sync pair editor.
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return "", "", default_port
    scheme = ""
    if "://" in url:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        url = parts.netloc or parts.path
    # "user@host" from a pasted URL - the username field wins.
    if "@" in url:
        url = url.rsplit("@", 1)[1]
    # Anything behind the host is a remote path, not part of the address.
    url = url.split("/", 1)[0]
    host, port = _split_host_port(url.strip(), default_port)
    return scheme, host.strip(), port


def join(scheme, host, port):
    """Build the canonical URL stored as Ferry's account identity.

    Always carries scheme and port so that saving the same account twice
    produces the same string - a difference here is read as "another
    server" and wipes the local sync state (config_manager._is_account_change).
    """
    if not host:
        return ""
    return "%s://%s:%d" % (scheme, _bracket(host), port)


def display(host, port, default_port, scheme=""):
    """Shorten the stored URL back to what the account form shows.

    Inverse of join(): the result must split() back into the same values,
    otherwise editing an account looks like a server change. The default
    port is left out, and the scheme only where it carries information
    (FTP vs. FTPS - SFTP has nothing to choose).
    """
    if not host:
        return ""
    text = _bracket(host)
    if port != default_port:
        text = "%s:%d" % (text, port)
    return "%s://%s" % (scheme, text) if scheme else text
