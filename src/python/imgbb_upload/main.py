#!/usr/bin/env python3
"""
KDE Purpose plugin that uploads one image to **imgbb.com** (permanent hosting)
and shares the link.

KDE Purpose starts it from a Share menu; it is not meant to be run by hand.

Upload settings:
- API key:
    ImgBB requires a free API key. Save your key in:
    ~/.config/kde-purpose-plugins/imgbb_api_key

purpose_common.py must sit next to this file (install.sh copies it).

Usage:
    main.py --server SOCKET [--pluginType Export] [--pluginPath PATH]

Args:
    --server SOCKET    Unix socket that KDE Purpose listens on
    --pluginType TYPE  Plugin type passed by Purpose (ignored)
    --pluginPath PATH  Path of this file passed by Purpose (ignored)

Examples:
    main.py --server /tmp/randomname-1286082386 --pluginType Export \
        --pluginPath ~/.local/share/kpackage/Purpose/imgbb_upload/contents/code/main.py

Author: midvok
Update: 2026-10-22
"""
import mimetypes
import os
from pathlib import Path
import sys

from purpose_common import (
    CONFIG_DIR,
    Plugin,
    PluginError,
    http_error,
    http_post,
    json_body,
    read_config,
    run,
)

UPLOAD_URL = "https://api.imgbb.com/1/upload"
CONFIG_FILE = "imgbb_api_key"
IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def is_image(path: str) -> bool:
    """Return True if the file has an image MIME type or image extension."""
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("image/") or Path(path).suffix.lower() in IMAGE_EXTENSIONS


def upload(plugin: Plugin, path: str) -> str:
    """
    Upload an image to imgbb.com.

    ImgBB requires an API key and responds with a JSON object. Updates
    plugin.note to indicate permanent hosting.

    Args:
        plugin: The ImgBB plugin instance.
        path:   Local path of the image to upload.
    Returns:
        The public direct link to the uploaded image.
    Raises:
        PluginError: If the API key is missing, the file is not an image,
                     or the upload fails.
    """
    api_key = read_config(CONFIG_FILE)
    if not api_key:
        raise PluginError(
            f"API key missing. Save your ImgBB API key in "
            f"~/.config/{CONFIG_DIR}/{CONFIG_FILE}."
        )

    if not is_image(path):
        raise PluginError(
            f"ImgBB only accepts image files (got {os.path.basename(path)})."
        )

    plugin.note = "Permanent upload."
    data = {"key": api_key}

    resp = http_post(UPLOAD_URL, path, "image", data=data)
    body = json_body(resp)

    if resp.status_code == 200 and isinstance(body.get("data"), dict):
        link = body["data"].get("url") or body["data"].get("display_url")
        if isinstance(link, str) and link.startswith("https://"):
            return link

    # Extract human-readable error description from ImgBB's error object if present
    error_obj = body.get("error") if isinstance(body, dict) else None
    if isinstance(error_obj, dict) and "message" in error_obj:
        detail: str | None = str(error_obj["message"])
    else:
        text = resp.text.strip()
        detail = None if not text or text.startswith("<") else text

    raise http_error(resp, detail)


def main() -> int:
    """
    Run the ImgBB plugin for one Purpose request.

    Returns:
        The process exit code: 0 on success, 1 on failure.
    """
    plugin = Plugin(name="ImgBB", upload=lambda path: upload(plugin, path))
    return run(plugin)


if __name__ == "__main__":
    sys.exit(main())
