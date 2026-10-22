#!/usr/bin/env python3
"""
KDE Purpose plugin that uploads one file to **litterbox.catbox.moe** (temporary
hosting) and shares the link.

KDE Purpose starts it from a Share menu; it is not meant to be run by hand.

Upload settings:
- Retention duration:
    Allowed values are 1h, 12h, 24h, or 72h (defaults to 72h).
    Can be configured in:
    ~/.config/kde-purpose-plugins/litterbox_expiry

The desktop notification indicates the file expiration time.

purpose_common.py must sit next to this file (install.sh copies it).

Usage:
    main.py --server SOCKET [--pluginType Export] [--pluginPath PATH]

Args:
    --server SOCKET    Unix socket that KDE Purpose listens on
    --pluginType TYPE  Plugin type passed by Purpose (ignored)
    --pluginPath PATH  Path of this file passed by Purpose (ignored)

Examples:
    main.py --server /tmp/randomname-1286082386 --pluginType Export \
        --pluginPath \
        ~/.local/share/kpackage/Purpose/litterbox_upload/contents/code/main.py

Author: midvok
Update: 2026-10-22
"""
import sys

from purpose_common import Plugin, http_error, http_post, read_config, run

UPLOAD_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
CONFIG_FILE = "litterbox_expiry"
DEFAULT_EXPIRY = "72h"
VALID_EXPIRIES = {"1h", "12h", "24h", "72h"}


def upload(plugin: Plugin, path: str) -> str:
    """
    Upload a file to litterbox.catbox.moe.

    Litterbox replies with a plain text URL on success, or an error description
    as plain text. Updates plugin.note to reflect the file expiration time.

    Args:
        plugin: The Litterbox plugin instance (its note is updated dynamically).
        path:   Local path of the file to upload.
    Returns:
        The public link to the uploaded file.
    Raises:
        PluginError: If the upload fails or the reply is not a valid link.
    """
    expiry = read_config(CONFIG_FILE)
    if not expiry or expiry not in VALID_EXPIRIES:
        expiry = DEFAULT_EXPIRY

    plugin.note = f"Expires in {expiry}."
    data = {"reqtype": "fileupload", "time": expiry}

    resp = http_post(UPLOAD_URL, path, "fileToUpload", data=data)
    text = resp.text.strip()

    if resp.status_code == 200 and text.startswith("https://"):
        return text

    # Log raw server error to stderr so journalctl captures the exact body
    print(
        f"Litterbox error (HTTP {resp.status_code}): {text}",
        file=sys.stderr,
    )

    # Suppress raw HTML error bodies (e.g. Cloudflare 503) from error popups
    detail = None if not text or text.startswith("<") else text
    raise http_error(resp, detail)


def main() -> int:
    """
    Run the Litterbox plugin for one Purpose request.

    Returns:
        The process exit code: 0 on success, 1 on failure.
    """
    plugin = Plugin(name="Litterbox", upload=lambda path: upload(plugin, path))
    return run(plugin)


if __name__ == "__main__":
    sys.exit(main())
