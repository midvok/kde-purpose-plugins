#!/usr/bin/env python3
"""
End-to-end integration tests for kde-purpose-plugins.

Test execution flow:
1. Install: Copies each plugin into a temporary XDG_DATA_HOME via `install.sh`.
2. Emulate Purpose: Launches `main.*` over a Unix socket with CBOR request data.
3. Mock externals: Routes network uploads and D-Bus calls to local fakes.
4. Verify: Asserts socket replies, exit codes, Klipper copy, and notifications.

Usage:
    ./tests/run.sh [-v]
    python3 tests/test_plugins.py [-v]

Args:
    -v, --verbose   Show the plugin's stderr output when a test case fails

Examples:
    ./tests/run.sh
    ./tests/run.sh -v

Update: 2026-10-21
"""
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
REPO_DIR = TESTS_DIR.parent
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
UGUU_LINK = "https://h.uguu.se/AbCdEf.png"


@dataclass
class Case:
    """
    Describe one end-to-end plugin test case.

    Attributes:
        name:       Short human-readable description of the scenario.
        files:      Request URLs (relative to fixture HOME if no scheme is given).
        expect:     Expected output URL on success, or expected error substring.
        fake:       Upload fake mode ("ok", "big", "html", "down", "slow").
        notify:     Substring expected inside the desktop notification body.
        upload:     Expected metadata fields logged by the upload fake.
        no_klipper: If True, simulate Klipper not running on the desktop.
    """
    name: str
    files: list[str]
    expect: str
    fake: str = "ok"
    notify: str = ""
    upload: dict[str, Any] = field(default_factory=dict)
    no_klipper: bool = False

    @property
    def expects_success(self) -> bool:
        """Return True if this case expects a successful upload URL."""
        return self.expect.startswith("https://")


SHOT = "Pictures/My%20Shots/Screenshot_20261002_101500.png"
SPECIAL_FILE = 'a <b> & "c".png'
SPECIAL_URL = 'a%20%3Cb%3E%20&%20%22c%22.png'


def build_cases(offline_error: str) -> list[Case]:
    """
    Build the standard test suite for a plugin implementation.

    Args:
        offline_error: Expected error substring when the network is down
                       (differs slightly between Python requests and curl).
    Returns:
        List of test cases covering core upload, escaping, and error paths.
    """
    return [
        Case(
            "standard upload (%20 in path)",
            [SHOT],
            UGUU_LINK,
            notify="<b>Screenshot_20261002_101500.png</b>",
        ),
        Case(
            "special chars and HTML markup",
            [SPECIAL_URL],
            UGUU_LINK,
            notify='a &lt;b&gt; &amp; "c".png',
            upload={"name": SPECIAL_FILE},
        ),
        Case(
            "server rejects upload (HTTP 413)",
            [SHOT],
            "(HTTP 413): File too big",
            fake="big",
        ),
        Case(
            "non-JSON server error (HTTP 503)",
            [SHOT],
            "(HTTP 503): unexpected response",
            fake="html",
        ),
        Case(
            "network failure (offline)",
            [SHOT],
            offline_error,
            fake="down",
        ),
        Case(
            "invalid input (multiple files)",
            [SHOT, SHOT],
            "exactly one file (got 2)",
        ),
        Case(
            "invalid input (directory)",
            ["a%20folder"],
            "Not a regular file",
        ),
        Case(
            "Klipper not running",
            [SHOT],
            UGUU_LINK,
            notify="<b>not</b> copied",
            no_klipper=True,
        ),
    ]


#------------------------------------------------------------------------------
# CBOR ENCODING
#------------------------------------------------------------------------------
# KDE Purpose serializes the inputData JSON object into binary CBOR via Qt's
# QCborValue::fromJsonValue().toCbor(): definite lengths, shortest integer
# headers, big-endian floats, and sorted map keys.
#------------------------------------------------------------------------------
def cbor_encode(value: Any) -> bytes:
    """
    Encode a JSON-compatible Python value into binary CBOR.

    Args:
        value: Value to encode (bool, int, float, str, list, dict, or None).
    Returns:
        The CBOR-encoded byte sequence.
    Raises:
        ValueError: If an integer exceeds 64-bit unsigned range.
    """

    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError(f"Integer too large for CBOR: {n}")

    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, float):
        return b"\xfb" + struct.pack(">d", value)
    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(map(cbor_encode, value))
    return head(5, len(value)) + b"".join(
        cbor_encode(k) + cbor_encode(value[k]) for k in sorted(value)
    )


#------------------------------------------------------------------------------
# KDE PURPOSE PROCESSJOB EMULATION
#------------------------------------------------------------------------------
# Emulates `purpose/src/externalprocess/processjob.cpp`:
# 1. Purpose creates a listening Unix domain socket and starts the plugin with
#    `--server <socket_path>`.
# 2. The plugin must connect before exiting; otherwise ProcessJob dereferences
#    a null socket pointer and crashes the host app.
# 3. Purpose sends `<byte_length>\n` followed by the binary CBOR request map.
# 4. The plugin must read the request before closing the socket; closing with
#    unread bytes resets the connection and drops any error reply.
# 5. The plugin writes newline-delimited JSON status objects (`percent`,
#    `output`, or `error` + `errorText`) and closes the socket when done.
#------------------------------------------------------------------------------
def run_plugin(
    main_script: str, request: dict[str, Any], env: dict[str, str], workdir: Path
) -> tuple[list[dict[str, Any]], int, str]:
    """
    Execute a plugin script over a Purpose Unix socket and collect its replies.

    Args:
        main_script: Path to the installed plugin executable (`main.*`).
        request:     Request dictionary to send as CBOR.
        env:         Environment variables for the plugin subprocess.
        workdir:     Temporary directory where the Unix socket is created.
    Returns:
        A tuple of (parsed_json_replies, exit_code, stderr_text).
    Raises:
        AssertionError: If the plugin never connects or resets the socket.
    """
    server_path = str(workdir / "purpose.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(server_path)
    server.listen(1)
    server.settimeout(10)

    proc = subprocess.Popen(
        [
            main_script,
            "--server",
            server_path,
            "--pluginType",
            "Export",
            "--pluginPath",
            main_script,
        ],
        env=env,
        stderr=subprocess.PIPE,
    )
    try:
        conn, _ = server.accept()
    except TimeoutError:
        proc.kill()
        raise AssertionError(
            "Plugin never connected to the socket (would crash the host app)"
        ) from None
    finally:
        server.close()
        Path(server_path).unlink(missing_ok=True)

    payload = cbor_encode(request)
    conn.sendall(f"{len(payload)}\n".encode() + payload)
    conn.settimeout(30)

    data = b""
    try:
        while chunk := conn.recv(4096):
            data += chunk
    except ConnectionResetError:
        proc.kill()
        raise AssertionError(
            "Connection reset: plugin closed the socket without reading the request"
        ) from None
    finally:
        conn.close()

    _, stderr = proc.communicate(timeout=30)
    replies = [json.loads(line) for line in data.splitlines()]
    return replies, proc.returncode, stderr.decode(errors="replace")


def make_fixture_home(root: Path) -> Path:
    """Create a temporary HOME directory populated with sample test files."""
    home = root / "home"
    sample_files = [
        "Pictures/My Shots/Screenshot_20261002_101500.png",
        SPECIAL_FILE,
    ]
    for rel_path in sample_files:
        full_path = home / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"\x89PNG fake image data")
    (home / "a folder").mkdir()
    return home


def build_case_env(case: Case, home: Path, fake_log: Path,
                   dbus_log: Path) -> dict[str, str]:
    """Build the isolated environment variables for running a single test case."""
    env = dict(
        os.environ,
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        FAKE=case.fake,
        FAKE_LOG=str(fake_log),
        DBUS_LOG=str(dbus_log),
        PYTHONPATH=str(TESTS_DIR / "fakes"),
        PATH=f"{TESTS_DIR / 'fakes' / 'bin'}:{os.environ.get('PATH', '')}",
    )
    if case.no_klipper:
        env["FAKE_NO_KLIPPER"] = "1"
    else:
        env.pop("FAKE_NO_KLIPPER", None)
    return env


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    """Read all JSON lines from a log file, or return an empty list if missing."""
    if not path.exists():
        return []
    with open(path) as file_obj:
        return [json.loads(line) for line in file_obj if line.strip()]


#------------------------------------------------------------------------------
# TEST CASE EXECUTION & VERIFICATION
#------------------------------------------------------------------------------
# Runs a single Case against an installed plugin executable and checks:
# 1. Socket reply and exit code (link + exit 0 on success; error 100 + exit 1
#    on failure).
# 2. Klipper clipboard call (link copied on success unless Klipper is down).
# 3. Desktop notification call (shown on both success and failure; urgency
#    set to critical on failure).
# 4. Upload parameters recorded by the HTTP fake.
#------------------------------------------------------------------------------
def check_case(case: Case, main_script: str, root: Path, home: Path) -> list[str]:
    """
    Run one test case and return a list of failure descriptions (empty if passed).

    Args:
        case:        The test case definition to execute.
        main_script: Path to the installed plugin executable.
        root:        Temporary root directory for sockets and logs.
        home:        Fixture HOME directory containing sample files.
    Returns:
        List of human-readable problem strings; empty when all checks pass.
    """
    fake_log = root / "upload.log"
    dbus_log = root / "dbus.log"
    fake_log.unlink(missing_ok=True)
    dbus_log.unlink(missing_ok=True)

    urls = [u if "://" in u else f"file://{home}/{u}" for u in case.files]
    request = {"mimeType": "application/octet-stream", "urls": urls}
    env = build_case_env(case, home, fake_log, dbus_log)

    replies, code, stderr = run_plugin(main_script, request, env, root)
    uploads = read_json_lines(fake_log)
    dbus_calls = read_json_lines(dbus_log)
    final_reply = replies[-1] if replies else {}
    problems: list[str] = []

    # 1. Verify Purpose socket reply, exit code, and Klipper clipboard call
    if case.expects_success:
        actual_url = final_reply.get("output", {}).get("url")
        if actual_url != case.expect or code != 0:
            problems.append(
                f"expected link {case.expect}, got {final_reply} (exit {code})"
            )
        clipboard_texts = [
            c["text"] for c in dbus_calls if c.get("method") == "setClipboardContents"
        ]
        if not case.no_klipper and case.expect not in clipboard_texts:
            problems.append("link was not copied to Klipper")
    else:
        # Error code must be 100 (Spectacle treats 1 as user-cancelled)
        error_code = final_reply.get("error")
        error_text = final_reply.get("errorText", "")
        if error_code != 100 or case.expect not in error_text or code != 1:
            problems.append(
                f"expected error 100 containing {case.expect!r}, "
                f"got {final_reply} (exit {code})"
            )

    # 2. Verify Plasma desktop notification
    notifications = [c for c in dbus_calls if c.get("method") == "Notify"]
    if not notifications:
        problems.append("no desktop notification shown")
    else:
        last_note = notifications[-1]
        if case.notify and case.notify not in last_note["body"]:
            problems.append(
                f"notification body lacks {case.notify!r}: {last_note['body']!r}"
            )
        if not case.expects_success and not last_note["critical"]:
            problems.append("error notification was not marked critical")

    # 3. Verify uploaded file metadata
    for key, expected_val in case.upload.items():
        if not uploads or uploads[-1].get(key) != expected_val:
            problems.append(
                f"upload {key} should be {expected_val!r}, got {uploads[-1:]}"
            )

    if problems and VERBOSE and stderr.strip():
        problems.append(f"stderr: {stderr.strip()}")
    return problems


def run_suite(
    label: str,
    install_flags: list[str],
    plugin_id: str,
    cases: list[Case],
    root: Path,
    home: Path,
) -> tuple[int, int]:
    """
    Install a plugin variant into a temporary directory and run all test cases.

    Args:
        label:         Implementation label ("python" or "bash").
        install_flags: Extra CLI flags passed to `install.sh` (e.g. `["--bash"]`).
        plugin_id:     Plugin identifier (e.g. `"uguu_upload"`).
        cases:         List of test cases to run.
        root:          Temporary working directory.
        home:          Fixture HOME directory.
    Returns:
        Tuple of (passed_count, failed_count).
    """
    data_home = root / f"data-{label}"
    subprocess.run(
        [str(REPO_DIR / "install.sh"), *install_flags, plugin_id],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(os.environ, XDG_DATA_HOME=str(data_home)),
    )
    code_dir = (data_home / "kpackage" / "Purpose" / plugin_id / "contents" / "code")
    main_script = str(next(code_dir.glob("main.*")))

    passed = failed = 0
    for case in cases:
        try:
            problems = check_case(case, main_script, root, home)
        except AssertionError as err:
            problems = [str(err)]

        status = "PASS" if not problems else "FAIL"
        print(f"  {status}  {label:<6} {plugin_id}: {case.name}")
        for problem in problems:
            print(f"          {problem}")

        if problems:
            failed += 1
        else:
            passed += 1

    return passed, failed


def main() -> int:
    """Run all plugin test suites and return the overall exit status."""
    with tempfile.TemporaryDirectory(prefix="purpose-tests-") as tmp:
        root = Path(tmp)
        home = make_fixture_home(root)
        results = [
            run_suite(
                "python",
                [],
                "uguu_upload",
                build_cases("cannot connect to uguu.se"),
                root,
                home,
            ),
            run_suite(
                "bash",
                ["--bash"],
                "uguu_upload",
                build_cases("curl exit code 6"),
                root,
                home,
            ),
        ]

    passed = sum(p for p, _ in results)
    failed = sum(f for _, f in results)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
