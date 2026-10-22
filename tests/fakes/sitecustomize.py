"""
Test-only fake for requests.post (loaded automatically via PYTHONPATH).

Python automatically imports `sitecustomize` on startup if it is present on
`PYTHONPATH`. During tests, this module replaces `requests.post` with an
in-memory fake so uploads never touch the network.

Environment variables:
    FAKE:     Response mode to simulate: "ok", "big", "html", "down", or "slow".
    FAKE_LOG: Path to the file where uploaded file metadata is logged as JSON.
"""
import json
import os
from typing import Any
from urllib.parse import urlparse

# Host -> mode -> (HTTP status code, response body). Dict bodies are sent as JSON.
RESPONSES: dict[str, dict[str, tuple[int, Any]]] = {
    "uguu.se": {
        "ok": (
            200,
            {
                "success": True,
                "files": [{
                    "url": "https://h.uguu.se/AbCdEf.png"
                }]
            },
        ),
        "big": (
            413,
            {
                "success": False,
                "errorcode": 413,
                "description": "File too big"
            },
        ),
        "html": (503, "<html>over capacity</html>"),
    },
    "catbox.moe": {
        "ok": (200, "https://files.catbox.moe/AbCdEf.png"),
        "big": (413, "File too big"),
        "html": (503, "<html>over capacity</html>"),
    },
    "litterbox.catbox.moe": {
        "ok": (200, "https://litter.catbox.moe/AbCdEf.png"),
        "big": (413, "File too big"),
        "html": (503, "<html>over capacity</html>"),
    },
    "api.imgbb.com": {
        "ok": (
            200,
            {
                "data": {
                    "url": "https://i.ibb.co/AbCdEf/image.png",
                    "display_url": "https://i.ibb.co/AbCdEf/image.png",
                    "delete_url": "https://ibb.co/AbCdEf/delete123",
                },
                "success": True,
                "status": 200,
            },
        ),
        "big": (
            413,
            {
                "status_code": 413,
                "error": {
                    "message": "File too big",
                    "code": 100,
                },
                "status_txt": "Payload Too Large",
            },
        ),
        "html": (503, "<html>over capacity</html>"),
    },
}

if os.environ.get("FAKE"):
    import requests

    def fake_post(
        url: str,
        data: Any = None,
        files: Any = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Record the upload metadata and return a canned HTTP response."""
        ((field, (name, file_obj, mime)), ) = files.items()
        size = len(file_obj.read())
        with open(os.environ["FAKE_LOG"], "a") as log:
            record = {
                "url": url,
                "field": field,
                "name": name,
                "mime": mime,
                "size": size,
                "data": data,
                "timeout": timeout,
            }
            log.write(json.dumps(record) + "\n")

        mode = os.environ["FAKE"]
        if mode == "down":
            raise requests.ConnectionError("Failed to resolve host")
        if mode == "slow":
            raise requests.Timeout("Read timed out")

        host = urlparse(url).hostname or ""
        status, body = RESPONSES[host][mode]
        resp = requests.Response()
        resp.status_code = status
        resp._content = (json.dumps(body) if isinstance(body, dict) else body).encode()
        return resp

    requests.post = fake_post
