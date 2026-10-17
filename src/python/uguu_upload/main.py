#!/usr/bin/env python3
#==============================================================================
# uguu_upload - KDE Purpose plugin: share a file to uguu.se
#==============================================================================
# Usage: main.py --server SOCKET --pluginType Export --pluginPath PATH
#        (started by KDE Purpose from a Share menu, not by hand)
#
# Description:
#   Uploads one file to uguu.se (temporary hosting, no account), copies the
#   link to the clipboard via Klipper and shows a Plasma notification.
#   Works from any Share > Export menu: Spectacle, Dolphin, Krusader.
#
# Protocol (purpose/src/externalprocess/processjob.cpp):
#   in  : "<byte count>\n" + CBOR map {"mimeType": ..., "urls": [...]}
#   out : one JSON object per line: percent | output | error + errorText
#
# Dependencies:
#   python3  : gated by the exec: constraint in metadata.json
#   requests : checked at runtime (package python3-requests)
#   busctl   : checked at runtime (part of systemd)
#
# Install: ~/.local/share/kpackage/Purpose/uguu_upload/contents/code/main.py
#------------------------------------------------------------------------------
# Update: 2026-10-02
#==============================================================================

import argparse
import html
import importlib
import json
import mimetypes
import os
import shutil
import socket
import struct
import subprocess
import sys
import traceback
from urllib.parse import unquote, urlparse

UPLOAD_URL = "https://uguu.se/upload"
TIMEOUT = 60  # seconds
APP_NAME = "Uguu"


#------------------------------------------------------------------------------
# ERRORS
#------------------------------------------------------------------------------
# An error meant for the user: shown in the host app and as a notification.
#------------------------------------------------------------------------------
class PluginError(Exception):
    pass


#------------------------------------------------------------------------------
# Raises PluginError unless command `name` is installed.
#------------------------------------------------------------------------------
def require_command(name):
    if shutil.which(name) is None:
        raise PluginError(f"'{name}' is required but not installed.")


#------------------------------------------------------------------------------
# Imports and returns module `name`, or raises PluginError naming `package`.
#
# - Imported here, not at the top: a failed import before connect() would
#     crash the host app (see connect).
#------------------------------------------------------------------------------
def require_module(name, package):
    try:
        return importlib.import_module(name)
    except ImportError:
        raise PluginError(f"Python module '{name}' is required: install {package}.") from None


#------------------------------------------------------------------------------
# SOCKET
#------------------------------------------------------------------------------
# Connects to the Purpose socket and returns it as a binary stream.
#
# - Must run before anything can fail: if we exit without connecting,
#     ProcessJob dereferences a null socket and crashes the host app.
# - sock.close() right away is safe: the connection stays open until the
#     stream returned by makefile() is closed too.
#------------------------------------------------------------------------------
def connect(server):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(server)
    stream = sock.makefile("rwb")
    sock.close()
    return stream


#------------------------------------------------------------------------------
# Reads the request ("<length>\n" + CBOR) and returns it as a dict.
#------------------------------------------------------------------------------
def read_request(stream):
    length = int(stream.readline())
    data = stream.read(length)
    if len(data) != length:
        raise PluginError(f"Short read from Purpose ({len(data)}/{length} bytes).")
    try:
        return cbor_decode(data)
    except (ValueError, IndexError, struct.error):
        raise PluginError("Unexpected payload from Purpose.") from None


#------------------------------------------------------------------------------
# Sends one JSON line to Purpose, e.g. reply(stream, percent=10).
#------------------------------------------------------------------------------
def reply(stream, **fields):
    stream.write(json.dumps(fields).encode() + b"\n")
    stream.flush()


#------------------------------------------------------------------------------
# CBOR
#------------------------------------------------------------------------------
# Decodes CBOR bytes into Python values.
# Covers what QCborValue::fromJsonValue() emits: definite-length maps and
# arrays, text strings, integers, booleans, null and floats.
#------------------------------------------------------------------------------
def cbor_decode(data):
    value, end = cbor_item(data, 0)
    if end != len(data):
        raise ValueError("trailing bytes")
    return value


#------------------------------------------------------------------------------
# Decodes one item at `pos` and returns (value, position after it).
#
# - Header byte: top 3 bits = major type, low 5 bits = "additional info".
#     Info 24..27 means the argument follows in 1, 2, 4 or 8 bytes.
# - Major 7 with a 2/4/8-byte argument is a half/single/double float.
#------------------------------------------------------------------------------
def cbor_item(data, pos):
    major, info = data[pos] >> 5, data[pos] & 0x1F
    pos += 1
    raw = b""
    if info < 24:
        arg = info
    elif info <= 27:
        size = 1 << (info - 24)
        raw = data[pos:pos + size]
        arg = int.from_bytes(raw, "big")
        pos += size
    else:
        raise ValueError("indefinite length")  # never emitted by Qt here

    match major:
        case 0:  # unsigned integer
            return arg, pos
        case 1:  # negative integer
            return -1 - arg, pos
        case 2:  # byte string
            return bytes(data[pos:pos + arg]), pos + arg
        case 3:  # text string
            return data[pos:pos + arg].decode(), pos + arg
        case 4:  # array
            items = []
            for _ in range(arg):
                item, pos = cbor_item(data, pos)
                items.append(item)
            return items, pos
        case 5:  # map
            result = {}
            for _ in range(arg):
                key, pos = cbor_item(data, pos)
                result[key], pos = cbor_item(data, pos)
            return result, pos
        case 6:  # tag: ignore it, keep the tagged value
            return cbor_item(data, pos)
        case _:  # 7: simple values and floats
            if info in (25, 26, 27):
                return struct.unpack({25: ">e", 26: ">f", 27: ">d"}[info], raw)[0], pos
            return {20: False, 21: True, 22: None}.get(arg), pos


#------------------------------------------------------------------------------
# DESKTOP
#------------------------------------------------------------------------------
# Shows a Plasma notification via the freedesktop Notifications D-Bus API.
# `body` is markup: escape user text with html.escape(text, quote=False).
#
# - "--" stops busctl from parsing the -1 (default timeout) as an option.
# - Errors are ignored: a failed notification must not hide the result.
#------------------------------------------------------------------------------
def notify(icon, summary, body, critical=False):
    hints = ["1", "urgency", "y", "2"] if critical else ["0"]
    try:
        subprocess.run(
            ["busctl", "--user", "call", "--",
             "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
             "org.freedesktop.Notifications", "Notify", "susssasa{sv}i",
             APP_NAME, "0", icon, summary, body, "0", *hints, "-1"],
            stdout=subprocess.DEVNULL, check=False)
    except OSError:
        pass


#------------------------------------------------------------------------------
# Copies `text` to the clipboard via Klipper, which keeps it after we exit.
# Returns False if Klipper is not running.
#------------------------------------------------------------------------------
def copy_to_clipboard(text):
    result = subprocess.run(
        ["busctl", "--user", "call", "org.kde.klipper", "/klipper",
         "org.kde.klipper.klipper", "setClipboardContents", "s", text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


#------------------------------------------------------------------------------
# INPUT
#------------------------------------------------------------------------------
# Returns the --server socket path; other Purpose arguments are ignored.
#------------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(description="KDE Purpose plugin: upload to uguu.se")
    parser.add_argument("--server", required=True, help="Purpose socket path")
    args, _ = parser.parse_known_args(argv)
    return args.server


#------------------------------------------------------------------------------
# Validates the request's single file:// URL and returns its local path.
#------------------------------------------------------------------------------
def resolve_path(request):
    urls = request.get("urls", [])
    if len(urls) != 1:
        raise PluginError(f"Select exactly one file (got {len(urls)}).")
    url = urlparse(urls[0])
    if url.scheme != "file":
        raise PluginError("Only local files are supported.")

    path = unquote(url.path)
    if not os.path.exists(path):
        raise PluginError(f"File not found: {path}")
    if not os.path.isfile(path):
        raise PluginError(f"Not a regular file: {path}")
    if not os.access(path, os.R_OK):
        raise PluginError(f"Cannot read {path}")
    return path


#------------------------------------------------------------------------------
# UPLOAD
#------------------------------------------------------------------------------
# Uploads `path` to uguu.se and returns the link.
#
# - Uguu replies {"files": [{"url": ...}]} or {"description": "<error>"}.
#------------------------------------------------------------------------------
def upload(requests, path):
    name = os.path.basename(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as file:
            resp = requests.post(UPLOAD_URL, files={"files[]": (name, file, mime)}, timeout=TIMEOUT)
    except requests.Timeout:
        raise PluginError(f"Upload timed out after {TIMEOUT} s.") from None
    except requests.ConnectionError:
        raise PluginError("Upload failed: cannot connect to uguu.se.") from None
    except requests.RequestException as e:
        raise PluginError(f"Upload failed: {e}") from None

    try:
        body = resp.json()
    except ValueError:
        body = {}
    try:
        return body["files"][0]["url"]
    except (KeyError, IndexError, TypeError):
        error = body.get("description") if isinstance(body, dict) else None
        raise PluginError(f"Uguu upload failed (HTTP {resp.status_code}): "
                          f"{error or 'unexpected response'}") from None


#------------------------------------------------------------------------------
# REPORTING
#------------------------------------------------------------------------------
# Copies the link and shows the success notification.
# Runs after the socket is closed, so the host app never waits for it.
#------------------------------------------------------------------------------
def report_success(path, link):
    home = os.path.expanduser("~")
    shown = "~" + path[len(home):] if path.startswith(home + "/") else path
    if copy_to_clipboard(link):
        copied = "Link copied to clipboard."
    else:
        copied = "Klipper is not running, so the link was <b>not</b> copied."
    notify("cloud-upload", "[PY] Uploaded to Uguu",
           f"<b>{html.escape(shown, quote=False)}</b><br>"
           f'<a href="{html.escape(link)}">{html.escape(link)}</a><br>'
           f"{copied} Expires in ~3 hours.")


#------------------------------------------------------------------------------
# Reports `message` to the host app, closes the socket, then notifies.
#
# - Error code 100 (KJob::UserDefinedError): Spectacle treats 1 as
#     "cancelled" and silently drops the message.
# - Hosts show errors inconsistently (Krusader not at all), hence the
#     extra notification.
#------------------------------------------------------------------------------
def report_error(stream, message):
    reply(stream, error=100, errorText=message)
    stream.close()
    notify("dialog-error", "Uguu upload failed", html.escape(message, quote=False), critical=True)


#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
# - Order matters: connect first (see connect), then read the request before
#     anything else can fail. Closing a socket with unread data resets it,
#     and the host app loses our error reply.
# - Unexpected exceptions are reported like PluginError, with the traceback
#     on stderr (the host app's log / journal).
#------------------------------------------------------------------------------
def main():
    stream = connect(parse_args(sys.argv[1:]))
    try:
        request = read_request(stream)
        require_command("busctl")
        requests = require_module("requests", "python3-requests")
        path = resolve_path(request)
        reply(stream, percent=10)
        link = upload(requests, path)
        reply(stream, percent=100, output={"url": link})
    except PluginError as e:
        report_error(stream, str(e))
        return 1
    except Exception as e:
        traceback.print_exc()
        report_error(stream, f"Internal error: {e}")
        return 1

    stream.close()
    report_success(path, link)
    return 0


if __name__ == "__main__":
    sys.exit(main())
