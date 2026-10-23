# Supported Upload Services

Export plugins for **KDE Purpose** delegate file hosting to external cloud upload services. Each service offers distinct retention policies, file size limits, authentication mechanisms, and response payloads.

## Service comparison

| Service                     | Hosting type | Retention         | File types  | Max size | Authentication & requirements            | Response format |
| :-------------------------- | :----------- | :---------------- | :---------- | :------- | :--------------------------------------- | :-------------- |
| [**Uguu**](#uguu)           | Temporary    | ~3 hours          | Any file    | 100 MB   | Anonymous (no account needed)            | JSON            |
| [**Catbox**](#catbox)       | Permanent    | Indefinite        | Any file    | 200 MB   | Anonymous, optional user account         | Plain text      |
| [**Litterbox**](#litterbox) | Temporary    | 1h, 12h, 24h, 72h | Any file    | 1 GB     | Anonymous, selectable retention duration | Plain text      |
| [**ImgBB**](#imgbb)         | Permanent    | Indefinite        | Images only | 32 MB    | Requires free API key                    | JSON            |

## Uguu

**Uguu** (https://uguu.se) is a lightweight temporary hosting service designed for fast, frictionless file sharing without registration.

- **Retention**: Uploaded files remain accessible for approximately 3 hours before automatic deletion.
- **Access mode**: Fully anonymous; no account credentials, user tokens, or registration required.
- **Constraints**: Accepts arbitrary file types up to 100 MB.

### Request specifications

- **Endpoint**: `POST https://uguu.se/upload`
- **Content type**: `multipart/form-data`
- **Form fields**:

| Field name | Type | Content                                               |
| :--------- | :--- | :---------------------------------------------------- |
| `files[]`  | File | Binary file stream, filename, and detected MIME type. |

### Response format

Uguu returns a **JSON** object on both success and failure:

```json
{
  "success": true,
  "files": [
    {
      "hash": "83936c3ac5ee07fc",
      "name": "Screenshot.png",
      "url": "https://h.uguu.se/YoTANMRs.png",
      "size": 201020
    }
  ]
}
```

The plugin extracts `files[0].url`. If the upload fails (e.g. file size exceeds 100 MB), the server responds with HTTP 413 and an error description:

```json
{
  "success": false,
  "errorcode": 413,
  "description": "File too big"
}
```

### Command-line examples

Using **curl**:

```bash
curl -sS \
    -A "kde-purpose-plugins/1.0 (KDE Plasma)" \
    -F "files[]=@Screenshot.png" \
    https://uguu.se/upload
```

Using Python **requests**:

```python
import mimetypes
import requests

url = "https://uguu.se/upload"
file_path = "Screenshot.png"
mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

with open(file_path, "rb") as file_handle:
    response = requests.post(
        url,
        files={"files[]": (file_path, file_handle, mime)},
        headers={"User-Agent": "kde-purpose-plugins/1.0 (KDE Plasma)"},
        timeout=60,
    )

data = response.json()
print("Uploaded link:", data["files"][0]["url"])
```

## Catbox

**Catbox** (https://catbox.moe) provides permanent, indefinite file hosting for arbitrary files up to 200 MB.

- **Retention**: Files remain online permanently until deleted by the account owner or removed for terms of service violations.
- **Account support**: Supports both anonymous uploads and account-linked uploads for managing or deleting files via a `userhash` token.
- **Anti-bot protection**: The service runs behind Cloudflare mitigation. The default Python **requests** identifier (`python-requests/...`) is blocked with HTTP 403 or HTML challenge pages; setting a custom **User-Agent** is **_mandatory_**.

### Request specifications

- **Endpoint**: `POST https://catbox.moe/user/api.php`
- **Content type**: `multipart/form-data`
- **Form fields**:

| Field name     | Type | Required | Description                                           |
| :------------- | :--- | :------- | :---------------------------------------------------- |
| `reqtype`      | Text | Yes      | Must be `"fileupload"`.                               |
| `fileToUpload` | File | Yes      | Binary file stream, filename, and detected MIME type. |
| `userhash`     | Text | Optional | Registered account token (`userhash`).                |

### Response format

Catbox returns the direct link as raw **plain text** on HTTP 200:

```
https://files.catbox.moe/AbCdEf.png
```

On failure, Catbox returns a plain text error message (e.g. `File too big`), or an HTML error page during Cloudflare capacity issues (HTTP 503). The plugin inspects the response to ensure raw HTML tags are never shown in desktop notifications.

### Command-line examples

Using **curl**:

```bash
# Anonymous upload:
curl -sS \
    -A "kde-purpose-plugins/1.0 (KDE Plasma)" \
    -F "reqtype=fileupload" \
    -F "fileToUpload=@Screenshot.png" \
    https://catbox.moe/user/api.php

# Account-linked upload:
curl -sS \
    -A "kde-purpose-plugins/1.0 (KDE Plasma)" \
    -F "reqtype=fileupload" \
    -F "userhash=YOUR_USERHASH" \
    -F "fileToUpload=@Screenshot.png" \
    https://catbox.moe/user/api.php
```

Using Python **requests**:

```python
import mimetypes
import requests

url = "https://catbox.moe/user/api.php"
file_path = "Screenshot.png"
mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

payload = {"reqtype": "fileupload"}
# Optional account token: payload["userhash"] = "YOUR_USERHASH"

with open(file_path, "rb") as file_handle:
    response = requests.post(
        url,
        data=payload,
        files={"fileToUpload": (file_path, file_handle, mime)},
        headers={"User-Agent": "kde-purpose-plugins/1.0 (KDE Plasma)"},
        timeout=60,
    )

print("Uploaded link:", response.text.strip())
```

## Litterbox

**Litterbox** (https://litterbox.catbox.moe) is the temporary hosting counterpart to Catbox, designed for transferring large files (up to 1 GB) with automated expiration.

- **Retention**: Selectable expiration duration: 1 hour, 12 hours, 24 hours, or 72 hours (passed via the `time` parameter).
- **Access mode**: Fully anonymous; no user accounts or registration.
- **Constraints**: Accepts arbitrary file types up to 1 GB.

### Request specifications

- **Endpoint**: `POST https://litterbox.catbox.moe/resources/internals/api.php`
- **Content type**: `multipart/form-data`
- **Form fields**:

| Field name     | Type | Required | Description                                                |
| :------------- | :--- | :------- | :--------------------------------------------------------- |
| `reqtype`      | Text | Yes      | Must be `"fileupload"`.                                    |
| `time`         | Text | Yes      | Expiration duration: `"1h"`, `"12h"`, `"24h"`, or `"72h"`. |
| `fileToUpload` | File | Yes      | Binary file stream, filename, and detected MIME type.      |

### Response format

Litterbox returns the direct public link as raw **plain text** on HTTP 200:

```
https://litter.catbox.moe/AbCdEf.png
```

Similar to Catbox, errors are returned as plain text descriptions or HTML error pages.

### Command-line examples

Using **curl**:

```bash
curl -sS \
    -A "kde-purpose-plugins/1.0 (KDE Plasma)" \
    -F "reqtype=fileupload" \
    -F "time=72h" \
    -F "fileToUpload=@Screenshot.png" \
    https://litterbox.catbox.moe/resources/internals/api.php
```

Using Python **requests**:

```python
import mimetypes
import requests

url = "https://litterbox.catbox.moe/resources/internals/api.php"
file_path = "Screenshot.png"
mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

payload = {"reqtype": "fileupload", "time": "72h"}

with open(file_path, "rb") as file_handle:
    response = requests.post(
        url,
        data=payload,
        files={"fileToUpload": (file_path, file_handle, mime)},
        headers={"User-Agent": "kde-purpose-plugins/1.0 (KDE Plasma)"},
        timeout=60,
    )

print("Uploaded link:", response.text.strip())
```

## ImgBB

**ImgBB** (https://imgbb.com) is a dedicated image hosting service providing permanent storage for images up to 32 MB.

- **Retention**: Permanent storage; direct image URLs do not expire.
- **Format constraint**: Accepts **_only image files_** (`image/*`). Non-image files are rejected locally before upload.
- **Authentication**: A free API key from [api.imgbb.com](https://api.imgbb.com/) is **_strictly required_** (passed via the `key` parameter).

### Request specifications

- **Endpoint**: `POST https://api.imgbb.com/1/upload`
- **Content type**: `multipart/form-data`
- **Form fields**:

| Field name | Type | Required | Description                                            |
| :--------- | :--- | :------- | :----------------------------------------------------- |
| `key`      | Text | Yes      | Your ImgBB API key string.                             |
| `image`    | File | Yes      | Binary image stream, filename, and detected MIME type. |

### Response format

ImgBB returns a structured **JSON** response object:

```json
{
  "data": {
    "id": "2ndCYJK",
    "title": "Screenshot",
    "url_viewer": "https://ibb.co/2ndCYJK",
    "url": "https://i.ibb.co/AbCdEf/image.png",
    "display_url": "https://i.ibb.co/AbCdEf/image.png",
    "delete_url": "https://ibb.co/2ndCYJK/delete123"
  },
  "success": true,
  "status": 200
}
```

The plugin extracts `data.url` (falling back to `data.display_url`). On error, ImgBB responds with a descriptive JSON error payload:

```json
{
  "status_code": 400,
  "error": {
    "message": "Invalid API key",
    "code": 100
  },
  "status_txt": "Bad Request"
}
```

### Command-line examples

Using **curl**:

```bash
curl -sS \
    -A "kde-purpose-plugins/1.0 (KDE Plasma)" \
    -F "key=YOUR_IMGBB_API_KEY" \
    -F "image=@Screenshot.png" \
    https://api.imgbb.com/1/upload
```

Using Python **requests**:

```python
import mimetypes
import requests

url = "https://api.imgbb.com/1/upload"
file_path = "Screenshot.png"
api_key = "YOUR_IMGBB_API_KEY"
mime = mimetypes.guess_type(file_path)[0] or "image/png"

payload = {"key": api_key}

with open(file_path, "rb") as file_handle:
    response = requests.post(
        url,
        data=payload,
        files={"image": (file_path, file_handle, mime)},
        headers={"User-Agent": "kde-purpose-plugins/1.0 (KDE Plasma)"},
        timeout=60,
    )

data = response.json()
print("Uploaded link:", data["data"]["url"])
```
