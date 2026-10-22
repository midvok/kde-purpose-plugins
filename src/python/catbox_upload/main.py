#!/usr/bin/env python3
"""
KDE Purpose plugin that uploads one file to **catbox.moe** (permanent hosting)
and shares the link.

KDE Purpose starts it from a Share menu; it is not meant to be run by hand.

Uplaod modes:
- User account upload:
    Tied to a registered account if userhash is configured in:
    ~/.config/kde-purpose-plugins/catbox_userhash
- Anonymous upload:
    Used automatically if the config file does not exist or is empty.

purpose_common.py must sit next to this file (install.sh copies it).

Usage:
    main.py --server SOCKET [--pluginType Export] [--pluginPath PATH]

Args:
    --server SOCKET    Unix socket that KDE Purpose listens on
    --pluginType TYPE  Plugin type passed by Purpose (ignored)
    --pluginPath PATH  Path of this file passed by Purpose (ignored)

Examples:
    main.py --server /tmp/randomname-1286082386 --pluginType Export \
        --pluginPath ~/.local/share/kpackage/Purpose/catbox_upload/contents/code/main.py

Author: midvok
Update: 2026-10-22
"""
import sys

from purpose_common import Plugin, http_error, http_post, read_config, run

UPLOAD_URL = "https://catbox.moe/user/api.php"
CONFIG_FILE = "catbox_userhash"


def upload(plugin: Plugin, path: str) -> str:
    """
    Upload a file to catbox.moe.

    Catbox replies with a plain text URL on success, or an error description
    as plain text. Updates plugin.note to reflect account or anonymous status.

    Args:
        plugin: The Catbox plugin instance (its note is updated dynamically).
        path:   Local path of the file to upload.
    Returns:
        The public link to the uploaded file.
    Raises:
        PluginError: If the upload fails or the reply is not a valid link.
    """
    data: dict[str, str] = {"reqtype": "fileupload"}
    userhash = read_config(CONFIG_FILE)

    if userhash:
        data["userhash"] = userhash
        plugin.note = "Uploaded to your account."
    else:
        plugin.note = "Uploaded anonymously."

    resp = http_post(UPLOAD_URL, path, "fileToUpload", data=data)
    text = resp.text.strip()

    if resp.status_code == 200 and text.startswith("https://"):
        return text

    # Suppress raw HTML error bodies (e.g. Cloudflare 503) from error popups
    detail = None if not text or text.startswith("<") else text
    raise http_error(resp, detail)


def main() -> int:
    """
    Run the Catbox plugin for one Purpose request.

    Returns:
        The process exit code: 0 on success, 1 on failure.
    """
    plugin = Plugin(name="Catbox", upload=lambda path: upload(plugin, path))
    return run(plugin)


if __name__ == "__main__":
    sys.exit(main())
