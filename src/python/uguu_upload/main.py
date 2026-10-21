#!/usr/bin/env python3
"""
KDE Purpose plugin that uploads one file to uguu.se (temporary hosting, no
account) and shares the link. KDE Purpose starts it from a Share menu; it is
not meant to be run by hand.

purpose_common.py must sit next to this file (install.sh copies it).

Usage:
    main.py --server SOCKET [--pluginType Export] [--pluginPath PATH]

Args:
    --server SOCKET    Unix socket that KDE Purpose listens on
    --pluginType TYPE  Plugin type passed by Purpose (ignored)
    --pluginPath PATH  Path of this file passed by Purpose (ignored)

Examples:
    main.py --server /tmp/randomname-1286082386 --pluginType Export \\
        --pluginPath ~/.local/share/kpackage/Purpose/uguu_upload/contents/code/main.py

Author: midvok
Update: 2026-10-21
"""
import sys

from purpose_common import Plugin, http_error, http_post, json_body, run

UPLOAD_URL = "https://uguu.se/upload"


def upload(path: str) -> str:
    """
    Upload a file to uguu.se.

    Uguu replies {"files": [{"url": ...}]} on success, or
    {"description": "<error>"} on failure.

    Args:
        path: Local path of the file to upload.
    Returns:
        The public link to the uploaded file.
    Raises:
        PluginError: If the upload fails or the reply has no link.
    """
    resp = http_post(UPLOAD_URL, path, "files[]")
    body = json_body(resp)
    try:
        return body["files"][0]["url"]
    except (KeyError, IndexError, TypeError):
        raise http_error(resp, body.get("description")) from None


def main() -> int:
    """
    Run the Uguu plugin for one Purpose request.

    Returns:
        The process exit code: 0 on success, 1 on failure.
    """
    return run(Plugin(name="Uguu", upload=upload, note="Expires in ~3 hours."))


if __name__ == "__main__":
    sys.exit(main())
