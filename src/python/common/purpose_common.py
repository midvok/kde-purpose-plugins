"""
Shared code for KDE Purpose plugins.

Everything a KDE Purpose "Export" plugin needs: the Purpose socket protocol,
CBOR decoding, input validation, HTTP upload, the Klipper clipboard and
Plasma notifications. A plugin only provides its upload function and calls
run(Plugin(...)).

Protocol (purpose/src/externalprocess/processjob.cpp):
    in   "<byte count>\\n" + CBOR map {"mimeType": ..., "urls": [...]}
    out  one JSON object per line: percent | output | error + errorText

Dependencies:
    requests  checked at runtime (package python3-requests)
    busctl    checked at runtime (part of systemd)

Author: midvok
Update: 2026-10-22
"""
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
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING, Any, BinaryIO
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    import requests

TIMEOUT = 60  # seconds, per upload
CONFIG_DIR = "kde-purpose-plugins"  # under $XDG_CONFIG_HOME


@dataclass
class Plugin:
    """
    Describes one upload plugin.

    Attributes:
        name:   Service name, shown in notifications (e.g. "Uguu").
        upload: upload(path) -> link; raises PluginError on failure.
        note:   Extra text for the success notification (optional).
    """
    name: str
    upload: Callable[[str], str]
    note: str = ""


class PluginError(Exception):
    """An error meant for the user: shown in the host app and as a notification."""


def require_command(name: str) -> None:
    """
    Check that a command is installed.

    Args:
        name: Command to look up on PATH.
    Raises:
        PluginError: If the command is not installed.
    """
    if shutil.which(name) is None:
        raise PluginError(f"'{name}' is required but not installed.")


def require_module(name: str, package: str) -> ModuleType:
    """
    Import a module on demand.

    Modules are imported here, not at the top: a failed import before
    connect() would crash the host app (see connect).

    Args:
        name: Module to import.
        package: Distribution package to suggest if the import fails.
    Returns:
        The imported module.
    Raises:
        PluginError: If the module cannot be imported.
    """
    try:
        return importlib.import_module(name)
    except ImportError:
        raise PluginError(
            f"Python module '{name}' is required: install {package}."
        ) from None


#------------------------------------------------------------------------------
# Step 1: Connect first.
#
# Must connect before any code or import can fail: if the plugin exits without
# connecting, ProcessJob dereferences a null socket and crashes the host app.
#
# Closing `sock` right away is safe: the connection stays open until the
# stream returned by makefile() is closed too.
#------------------------------------------------------------------------------
def connect(server: str) -> BinaryIO:
    """
    Connect to the Purpose socket.

    Args:
        server: Path of the Unix socket Purpose listens on.
    Returns:
        The connection as a binary read/write stream.
    Raises:
        OSError: If the socket cannot be connected.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(server)
    stream = sock.makefile("rwb")
    sock.close()
    return stream


#------------------------------------------------------------------------------
# Step 2: Read request before failing.
#
# The complete request payload must be read before validating dependencies
# or input files: closing a socket with unread data resets the connection
# and drops the error reply.
#------------------------------------------------------------------------------
def read_request(stream: BinaryIO) -> dict[str, Any]:
    """
    Read the request ("<length>\\n" + CBOR) from Purpose.

    Args:
        stream: The Purpose socket stream.
    Returns:
        The decoded request, e.g. {"mimeType": ..., "urls": [...]}.
    Raises:
        PluginError: If the payload is short or cannot be decoded.
    """
    length = int(stream.readline())
    data = stream.read(length)
    if len(data) != length:
        raise PluginError(f"Short read from Purpose ({len(data)}/{length} bytes).")
    try:
        return cbor_decode(data)
    except (ValueError, IndexError, struct.error):
        raise PluginError("Unexpected payload from Purpose.") from None


def reply(stream: BinaryIO, **fields: Any) -> None:
    """
    Send one JSON line to Purpose, e.g. reply(stream, percent=10).

    Args:
        stream:   The Purpose socket stream.
        **fields: Keys and values of the JSON object.
    """
    stream.write(json.dumps(fields).encode() + b"\n")
    stream.flush()


def cbor_decode(data: bytes) -> Any:
    """
    Decode CBOR bytes into Python values.

    Covers what QCborValue::fromJsonValue() emits: definite-length maps and
    arrays, text strings, integers, booleans, null and floats.

    Args:
        data: The complete CBOR payload.
    Returns:
        The decoded value.
    Raises:
        ValueError: If the data has trailing bytes or unsupported items.
        IndexError: If the data ends in the middle of an item.
    """
    value, end = cbor_item(data, 0)
    if end != len(data):
        raise ValueError("trailing bytes")
    return value


#------------------------------------------------------------------------------
# Header byte layout
#   - Top 3 bits: major type.
#   - Low 5 bits: "additional info".
#
# Additional info decoding
#   - 0..23:  the argument itself.
#   - 24..27: the argument follows in 1, 2, 4 or 8 bytes.
#   - Major 7 with a 2/4/8-byte argument is a half/single/double float, so
#     its raw bytes are kept for struct.unpack().
#------------------------------------------------------------------------------
def cbor_item(data: bytes, pos: int) -> tuple[Any, int]:
    """
    Decode one CBOR item.

    Args:
        data: The CBOR payload.
        pos:  Offset of the item's header byte.
    Returns:
        Tuple containing (value, offset after the item).
    Raises:
        ValueError: If the item has an indefinite length.
        IndexError: If the data ends in the middle of the item.
    """
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
                fmt = {25: ">e", 26: ">f", 27: ">d"}[info]
                return struct.unpack(fmt, raw)[0], pos
            return {20: False, 21: True, 22: None}.get(arg), pos


def notify(
    app: str, icon: str, summary: str, body: str, critical: bool = False
) -> None:
    """
    Show a Plasma notification via the freedesktop Notifications D-Bus API.
    Errors are ignored: a failed notification must not hide the result.

    Args:
        app:      Application name shown in the notification.
        icon:     Icon name, e.g. "cloud-upload".
        summary:  Title line.
        body:     Markup text; escape user text with html.escape(text, quote=False).
        critical: If True, show an urgent notification.
    """
    hints = ["1", "urgency", "y", "2"] if critical else ["0"]
    try:
        # Signature 'susssasa{sv}i' argument layout:
        # app(s), replaces_id(u), icon(s), summary(s), body(s), actions(as),
        # hints(a{sv}), timeout(i).
        # "--" stops busctl from parsing the -1 (default timeout) as an option.
        subprocess.run(
            [
                "busctl",
                "--user",
                "call",
                "--",
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                "susssasa{sv}i",
                app,
                "0",
                icon,
                summary,
                body,
                "0",
                *hints,
                "-1",
            ],
            stdout=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass


def copy_to_clipboard(text: str) -> bool:
    """
    Copy text to the clipboard via Plasma's Klipper service.

    Uses D-Bus to communicate with Klipper, which retains clipboard history
    after the plugin process exits.

    Args:
        text: Text to copy.
    Returns:
        True if copied, False if Klipper is not running.
    """
    result = subprocess.run(
        [
            "busctl",
            "--user",
            "call",
            "org.kde.klipper",
            "/klipper",
            "org.kde.klipper.klipper",
            "setClipboardContents",
            "s",
            text,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def parse_args(argv: list[str]) -> str:
    """
    Parse the command line Purpose starts the plugin with.
    Only --server is used; the other Purpose arguments are ignored.

    Args:
        argv: Command-line arguments, without the program name.
    Returns:
        The Purpose socket path.
    """
    parser = argparse.ArgumentParser(description="KDE Purpose upload plugin")
    parser.add_argument("--server", required=True, help="Purpose socket path")
    args, _ = parser.parse_known_args(argv)
    return args.server


def resolve_path(request: dict[str, Any]) -> str:
    """
    Validate the request's single file:// URL.

    Args:
        request: The decoded Purpose request.
    Returns:
        The local path of a readable regular file.
    Raises:
        PluginError: If there is not exactly one local, readable file.
    """
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


def read_config(name: str) -> str | None:
    """
    Read the first line of a config file.
    The file is $XDG_CONFIG_HOME/kde-purpose-plugins/<name>
    (~/.config/kde-purpose-plugins/<name> by default).

    Args:
        name: Config file name.
    Returns:
        The first line, stripped, or None if the file is missing or empty.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        with open(os.path.join(base, CONFIG_DIR, name)) as file:
            return file.readline().strip() or None
    except OSError:
        return None


def http_post(
    url: str, path: str, field: str, data: dict[str, str] | None = None
) -> "requests.Response":
    """
    Upload a file as a multipart form field.
    Network errors become short PluginErrors (requests' own are very long).

    Args:
        url:   Upload URL.
        path:  File to upload.
        field: Name of the form field that holds the file.
        data:  Extra form fields, e.g. {"reqtype": "fileupload"} (optional).
    Returns:
        The HTTP response, whatever its status code.
    Raises:
        PluginError: If requests is missing or the request fails.
    """
    requests = require_module("requests", "python3-requests")
    name = os.path.basename(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as file:
            return requests.post(
                url, data=data, files={field: (name, file, mime)}, timeout=TIMEOUT
            )
    except requests.Timeout:
        raise PluginError(f"Upload timed out after {TIMEOUT} s.") from None
    except requests.ConnectionError:
        raise PluginError(
            f"Upload failed: cannot connect to {urlparse(url).hostname}."
        ) from None
    except requests.RequestException as e:
        raise PluginError(f"Upload failed: {e}") from None


def json_body(resp: "requests.Response") -> dict[str, Any]:
    """
    Parse a response body as a JSON object.

    Args:
        resp: The HTTP response.
    Returns:
        The body as a dict, or {} if it is not a JSON object.
    """
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def http_error(resp: "requests.Response", detail: str | None = None) -> PluginError:
    """
    Build the error for a failed upload response.

    Args:
        resp:   The HTTP response.
        detail: The server's error message, if it gave one.
    Returns:
        A PluginError to raise.
    """
    return PluginError(
        f"Upload failed (HTTP {resp.status_code}): {detail or 'unexpected response'}"
    )


#------------------------------------------------------------------------------
# Step 5: Run slow desktop extras after closing the Purpose socket.
#
# Copies the link to Klipper and displays a desktop notification. Runs only
# after closing the Purpose socket so the host application finishes its task
# immediately without waiting for D-Bus calls.
#------------------------------------------------------------------------------
def report_success(plugin: Plugin, path: str, link: str) -> None:
    """
    Copy the link and show the success notification.

    Args:
        plugin: The plugin that uploaded the file.
        path:   Local path of the uploaded file.
        link:   Public link returned by the service.
    """
    filename = os.path.basename(path)

    if copy_to_clipboard(link):
        copied = "Link copied to clipboard."
    else:
        copied = "Klipper is not running, so the link was <b>not</b> copied."

    notify(
        plugin.name,
        "cloud-upload",
        f"Uploaded to {plugin.name}",
        f"{html.escape(filename, quote=False)}<br>"
        f'<a href="{html.escape(link)}">{html.escape(link)}</a><br>'
        f"{copied} {plugin.note}".rstrip(),
    )


#------------------------------------------------------------------------------
# Report error with code 100, never 1.
#
# Error code 100 (KJob::UserDefinedError) informs Purpose of failure. Spectacle
# treats error code 1 as "cancelled by user" and silently drops the message.
#
# Because host applications display error messages inconsistently (Krusader
# suppresses error dialogs), the plugin also triggers a desktop notification.
#------------------------------------------------------------------------------
def report_error(plugin: Plugin, stream: BinaryIO, message: str) -> None:
    """
    Report an error to the host app, close the socket, then notify.

    Args:
        plugin:  The plugin that failed.
        stream:  The Purpose socket stream; closed by this function.
        message: Error text for the user.
    """
    reply(stream, error=100, errorText=message)
    stream.close()
    notify(
        plugin.name,
        "dialog-error",
        f"{plugin.name} upload failed",
        html.escape(message, quote=False),
        critical=True,
    )


#------------------------------------------------------------------------------
# Protocol execution flow:
# Step 1. Connect socket first.
# Step 2. Read request payload before any failure checks.
# Step 3. Validate runtime dependencies and file path.
# Step 4. Upload file and report progress/result.
# Step 5. Close socket immediately, then handle clipboard and notification.
#
# Unexpected exceptions are caught and reported as error 100, with full
# tracebacks printed to stderr for debugging via journalctl / host logs.
#------------------------------------------------------------------------------
def run(plugin: Plugin) -> int:
    """
    Run a plugin for one Purpose request.

    Args:
        plugin: The plugin to run.
    Returns:
        The process exit code: 0 on success, 1 on failure.
    """
    # Step 1: Establish socket before anything can fail (prevents host app crash)
    stream = connect(parse_args(sys.argv[1:]))
    try:
        # Step 2: Read payload before checking dependencies (prevents socket reset)
        request = read_request(stream)

        # Step 3: Validate dependencies and input file
        require_command("busctl")
        require_module("requests", "python3-requests")
        path = resolve_path(request)

        # Step 4: Upload file and report progress
        reply(stream, percent=10)
        link = plugin.upload(path)
        reply(stream, percent=100, output={"url": link})

    except PluginError as e:
        report_error(plugin, stream, str(e))
        return 1

    except Exception as e:
        traceback.print_exc()
        report_error(plugin, stream, f"Internal error: {e}")
        return 1

    # Step 5: Close socket so host finishes immediately, then notify and copy
    stream.close()
    report_success(plugin, path, link)
    return 0
