#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry - SSH host key verification for the SFTP backend.
#
# rclone accepts *any* SSH host key unless it is given a known_hosts file, so
# an SFTP account without one hands the password and every transferred file
# to whoever answers on that address. Ferry therefore keeps its own
# known_hosts file next to rclone.conf and passes it to rclone (as a config
# option of the remote and as RCLONE_SFTP_KNOWN_HOSTS_FILE, so that accounts
# written by an older version are covered too). This module fills that file.
#
# The key is read here instead of through rclone because rclone cannot report
# a key it does not already trust - it only ever answers "knownhosts: key is
# unknown". So Ferry speaks just enough of the SSH transport protocol
# (RFC 4253) to read the host key out of the key exchange reply, the point at
# which the server presents it. The exchange is never completed: nothing is
# encrypted, no user name and no password are sent, and the socket is closed
# as soon as the key is on the table. Verifying the signature would prove
# nothing either - an attacker in the middle signs with his own key, and
# whether that key is the right one is exactly the question the user answers
# by comparing the fingerprint.
#
# First contact is trust on first use, as with ssh itself: the key is stored
# and its fingerprint shown, so it can be compared with the server's. Every
# later run is verified by rclone against the stored key.
#
# SPDX-License-Identifier: Apache-2.0

import base64
import hashlib
import os
import socket
import struct

import common

log = common.make_logger("hostkey")

DEFAULT_PORT = 22
TIMEOUT = 20

# Message numbers of the SSH transport layer (RFC 4253, section 12).
_MSG_DISCONNECT = 1
_MSG_IGNORE = 2
_MSG_UNIMPLEMENTED = 3
_MSG_DEBUG = 4
_MSG_EXT_INFO = 7
_MSG_KEXINIT = 20
_MSG_KEX_INIT = 30       # KEXDH_INIT / KEX_ECDH_INIT - same number
_MSG_KEX_REPLY = 31      # KEXDH_REPLY / KEX_ECDH_REPLY - same number

# Packets that may turn up at any time and carry nothing we need.
_SKIPPED = (_MSG_IGNORE, _MSG_UNIMPLEMENTED, _MSG_DEBUG, _MSG_EXT_INFO)

_CLIENT_VERSION = b"SSH-2.0-Ferry"

# Key exchange methods in our order of preference. Only these two shapes are
# implemented, which is enough for every server this app will meet: OpenSSH
# and Dropbear both offer curve25519, and group14 covers the rest. The reply
# looks the same for both (the host key is its first field), so all the code
# below needs from the choice is how to build the client's request.
_KEX_CURVE25519 = ("curve25519-sha256", "curve25519-sha256@libssh.org")
_KEX_GROUP14 = ("diffie-hellman-group14-sha256", "diffie-hellman-group14-sha1")
_KEX_PREFERENCE = _KEX_CURVE25519 + _KEX_GROUP14

# 2048 bit MODP group (RFC 3526, group 14) with generator 2.
_GROUP14_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)

# What we claim to support. The transport is never actually set up, but the
# server checks during the negotiation that something is common to both
# sides and hangs up otherwise, so the lists have to be honest-looking.
_HOST_KEY_ALGORITHMS = ("ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,"
                        "ecdsa-sha2-nistp521,rsa-sha2-512,rsa-sha2-256,"
                        "ssh-rsa,ssh-dss")
_CIPHERS = ("chacha20-poly1305@openssh.com,aes128-ctr,aes192-ctr,aes256-ctr,"
            "aes128-gcm@openssh.com,aes256-gcm@openssh.com")
_MACS = "hmac-sha2-256,hmac-sha2-512,hmac-sha1"


class HostKeyError(Exception):
    """The server's host key could not be read."""


# --- known_hosts file -------------------------------------------------------

def known_hosts_path():
    """The known_hosts file Ferry manages for rclone."""
    return os.path.join(common.config_dir(), "known_hosts")


def ensure_file():
    """Make sure the file exists, and return its path.

    rclone refuses to connect at all when the file it was pointed at cannot
    be opened, so an empty file is the difference between "this host is not
    trusted yet" and "SFTP is broken".
    """
    path = known_hosts_path()
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass
            os.chmod(path, 0o600)
        except OSError as e:
            log("could not create %s: %s" % (path, e))
    return path


def host_pattern(host, port):
    """The known_hosts name of a server, as OpenSSH and rclone write it."""
    host = (host or "").strip().lower()
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return host if port == DEFAULT_PORT else "[%s]:%d" % (host, port)


def _read_entries():
    """The stored file as a list of (pattern, key_type, key) tuples."""
    entries = []
    try:
        with open(known_hosts_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    entries.append((parts[0], parts[1], parts[2]))
    except OSError:
        pass
    return entries


def stored_key(host, port):
    """The trusted (key_type, key) of a server, or None."""
    pattern = host_pattern(host, port)
    for entry_pattern, key_type, key in _read_entries():
        if entry_pattern == pattern:
            return key_type, key
    return None


def trust(host, port, key_type, key):
    """Store a host key as the trusted one, replacing any earlier entry."""
    pattern = host_pattern(host, port)
    kept = [e for e in _read_entries() if e[0] != pattern]
    kept.append((pattern, key_type, key))
    path = ensure_file()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("# Ferry - SSH host keys trusted for the SFTP account.\n")
            for entry in kept:
                f.write("%s %s %s\n" % entry)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError as e:
        log("could not store the host key for %s: %s" % (pattern, e))
        return False
    log("host key trusted for %s (%s %s)"
        % (pattern, key_type, fingerprint(key)))
    return True


def forget_all():
    """Drop every trusted key - the account they belonged to is gone."""
    try:
        os.remove(known_hosts_path())
        log("trusted host keys removed")
        return True
    except OSError:
        return False


def fingerprint(key):
    """The SHA256 fingerprint of a base64 key, in the format ssh prints."""
    try:
        digest = hashlib.sha256(base64.b64decode(key)).digest()
    except Exception:
        return "unknown"
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


# --- the check the account setup runs --------------------------------------

def check_host(host, port, timeout=TIMEOUT):
    """Compare the server's host key with the one Ferry trusts.

    Returns a dict with "state" - one of:
      "trusted"     the server presents the key that is already stored
      "new"         no key stored yet; call trust() to accept this one
      "changed"     another key than the stored one - do not connect
      "unreachable" the key could not be read ("error" says why)
    plus the key itself and the fingerprints for both sides.
    """
    known = stored_key(host, port)
    try:
        key_type, key = fetch_host_key(host, port, timeout)
    except HostKeyError as e:
        log("host key of %s unreadable: %s" % (host_pattern(host, port), e))
        return {"state": "unreachable", "error": str(e),
                "key_type": "", "key": "", "fingerprint": "",
                "stored_fingerprint": fingerprint(known[1]) if known else ""}
    result = {"key_type": key_type, "key": key, "error": "",
              "fingerprint": fingerprint(key),
              "stored_fingerprint": fingerprint(known[1]) if known else ""}
    if known is None:
        result["state"] = "new"
    elif known[1] == key:
        result["state"] = "trusted"
    else:
        result["state"] = "changed"
    log("host key check for %s: %s"
        % (host_pattern(host, port), result["state"]))
    return result


# --- reading the key off the wire ------------------------------------------

def fetch_host_key(host, port, timeout=TIMEOUT):
    """Return (key_type, key) of the server's SSH host key, base64 encoded."""
    if not host:
        raise HostKeyError("no server address")
    try:
        port = int(port or DEFAULT_PORT)
    except (TypeError, ValueError):
        raise HostKeyError("invalid port")
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError as e:
        raise HostKeyError("%s" % e)
    try:
        sock.settimeout(timeout)
        connection = _Connection(sock)
        connection.exchange_versions()
        connection.send(_client_kexinit())
        kex = _pick_kex(_server_kex_algorithms(connection.read(_MSG_KEXINIT)))
        connection.send(_kex_request(kex))
        reply = connection.read(_MSG_KEX_REPLY)
        # Both reply shapes start with the host key, which is all we want.
        blob = _read_string(reply, 1)[0]
        key_type = _read_string(blob, 0)[0]
        return (key_type.decode("ascii", "replace"),
                base64.b64encode(blob).decode("ascii"))
    except HostKeyError:
        raise
    except (OSError, struct.error, IndexError, ValueError) as e:
        raise HostKeyError("unexpected answer from the SSH server (%s)" % e)
    finally:
        try:
            sock.close()
        except OSError:
            pass


class _Connection(object):
    """The unencrypted part of an SSH connection: banners and packets.

    Reads are buffered because the version banner and the first packets can
    arrive in the same TCP segment.
    """

    def __init__(self, sock):
        self._sock = sock
        self._buffer = b""

    def exchange_versions(self):
        """Swap version banners. The server may send extra lines first."""
        self._sock.sendall(_CLIENT_VERSION + b"\r\n")
        while True:
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                if line.startswith(b"SSH-"):
                    if not line.startswith(b"SSH-2.0-"):
                        raise HostKeyError(
                            "the server speaks %s, not SSH 2.0"
                            % line.strip().decode("ascii", "replace"))
                    return
            if len(self._buffer) > 64 * 1024:
                raise HostKeyError("the server does not answer with an SSH"
                                   " banner")
            self._fill()

    def send(self, payload):
        """Wrap a payload in an unencrypted binary packet and send it."""
        # The packet (length field included) has to be a multiple of 8 bytes
        # and carry at least 4 bytes of padding.
        padding = 8 - ((len(payload) + 5) % 8)
        if padding < 4:
            padding += 8
        self._sock.sendall(struct.pack(">IB", len(payload) + padding + 1,
                                       padding)
                           + payload + os.urandom(padding))

    def read(self, expected):
        """The next packet of the expected type; noise in between is skipped."""
        while True:
            payload = self._read_packet()
            if not payload:
                continue
            message = payload[0]
            if message == expected:
                return payload
            if message in _SKIPPED:
                continue
            if message == _MSG_DISCONNECT:
                raise HostKeyError("the server refused the connection: %s"
                                   % _disconnect_reason(payload))
            raise HostKeyError("unexpected SSH message %d" % message)

    def _read_packet(self):
        """One unencrypted binary packet (RFC 4253, section 6), payload only."""
        length = struct.unpack(">I", self._take(4))[0]
        if not 8 <= length <= 65536:
            raise HostKeyError("the server sent a packet of %d bytes" % length)
        body = self._take(length)
        padding = body[0]
        if padding + 1 > len(body):
            raise HostKeyError("the server sent a malformed packet")
        return body[1:len(body) - padding]

    def _take(self, count):
        while len(self._buffer) < count:
            self._fill()
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _fill(self):
        chunk = self._sock.recv(8192)
        if not chunk:
            raise HostKeyError("the server closed the connection")
        self._buffer += chunk


def _disconnect_reason(payload):
    try:
        return _read_string(payload, 5)[0].decode("utf-8", "replace").strip()
    except (struct.error, IndexError, ValueError):
        return "no reason given"


def _string(value):
    if not isinstance(value, bytes):
        value = value.encode("ascii")
    return struct.pack(">I", len(value)) + value


def _read_string(data, offset):
    """The SSH string at offset; returns (value, offset after it)."""
    length = struct.unpack(">I", data[offset:offset + 4])[0]
    start = offset + 4
    end = start + length
    if end > len(data):
        raise ValueError("truncated string")
    return data[start:end], end


def _mpint(number):
    """An SSH multiple precision integer (RFC 4251, section 5)."""
    size = (number.bit_length() + 8) // 8
    raw = number.to_bytes(size, "big")
    return struct.pack(">I", len(raw)) + raw


def _client_kexinit():
    """Our side of the algorithm negotiation."""
    lists = [",".join(_KEX_PREFERENCE),   # key exchange
             _HOST_KEY_ALGORITHMS,        # host key
             _CIPHERS, _CIPHERS,          # encryption, both directions
             _MACS, _MACS,                # MAC, both directions
             "none", "none",              # compression
             "", ""]                      # languages
    payload = struct.pack(">B", _MSG_KEXINIT) + os.urandom(16)
    for name_list in lists:
        payload += _string(name_list)
    # No guessed packet follows, and the reserved field is zero.
    return payload + struct.pack(">BI", 0, 0)


def _server_kex_algorithms(payload):
    """The key exchange methods offered by the server."""
    # 1 message byte + 16 bytes cookie, then the first name-list.
    names, _offset = _read_string(payload, 17)
    return names.decode("ascii", "replace").split(",")


def _pick_kex(offered):
    """The method both sides support, in our order of preference."""
    for method in _KEX_PREFERENCE:
        if method in offered:
            return method
    raise HostKeyError("the server offers no key exchange method Ferry can"
                       " use to read its host key (%s)"
                       % ", ".join(offered[:6]))


def _kex_request(kex):
    """The client's key exchange packet for the chosen method.

    The value it carries never protects anything - the exchange is abandoned
    right after the server's answer - but it has to be a well formed one, or
    the server hangs up before sending its host key.
    """
    if kex in _KEX_CURVE25519:
        # Any 32 bytes are a valid X25519 public key.
        return struct.pack(">B", _MSG_KEX_INIT) + _string(os.urandom(32))
    secret = int.from_bytes(os.urandom(32), "big") + 2
    public = pow(2, secret, _GROUP14_PRIME)
    return struct.pack(">B", _MSG_KEX_INIT) + _mpint(public)
