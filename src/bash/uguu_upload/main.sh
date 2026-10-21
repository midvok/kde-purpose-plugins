#!/usr/bin/env bash
#==============================================================================
# uguu_upload - KDE Purpose plugin: share a file to uguu.se
#==============================================================================
# Description:
#   Uploads one file to uguu.se (temporary hosting, no account), copies the
#   link to the clipboard via Klipper and shows a Plasma notification.
#   Works from any Share > Export menu: Spectacle, Dolphin, Krusader.
#   Started by KDE Purpose from a Share menu; not meant to be run by hand.
#
# Usage:
#   main.sh --server SOCKET [--pluginType Export] [--pluginPath PATH]
#
# Protocol (purpose/src/externalprocess/processjob.cpp):
#   in  : "<byte count>\n" + CBOR map {"mimeType": ..., "urls": [...]}
#   out : one JSON object per line: percent | output | error + errorText
#
# Dependencies:
#   socat, curl, jq : gated by exec: constraints in metadata.json
#   busctl          : checked at runtime (part of systemd)
#
# Install: ~/.local/share/kpackage/Purpose/uguu_upload/contents/code/main.sh
#------------------------------------------------------------------------------
# Author: midvok
# Update: 2026-10-22
#==============================================================================

#------------------------------------------------------------------------------
# CONNECT SOCKET
#------------------------------------------------------------------------------
# Connects to the Purpose Unix socket and sets sock_in, sock_out, sock_pid.
#
# Step 1: Connect first.
# - Must connect before any code or command can fail: if the plugin exits
#   without connecting, ProcessJob dereferences a null socket and crashes
#   the host app.
# - Coproc file descriptors are closed in subshells, so they are duplicated
#   to ordinary descriptors. Originals are closed so socat sees EOF on finish.
# - socat -t 0.1: Qt never closes its side, so do not wait the default 0.5 s.
#------------------------------------------------------------------------------
connect_socket() {
    coproc SOCK { socat -t 0.1 - "UNIX-CONNECT:$server"; }

    # shellcheck disable=SC2153  # SOCK_PID is set by coproc
    sock_pid=$SOCK_PID
    local co_in=${SOCK[0]} co_out=${SOCK[1]}

    exec {sock_in}<&"$co_in" {sock_out}>&"$co_out"
    exec {co_in}<&- {co_out}>&-
}

# Send one JSON line to Purpose.
reply() {
    printf '%s\n' "$1" >&"$sock_out"
}

# Close the socket and wait for socat to flush the last reply.
finish() {
    exec {sock_out}>&-
    wait "$sock_pid"
}

#------------------------------------------------------------------------------
# FAIL
#------------------------------------------------------------------------------
# Reports an error to the host app and as a notification, then exits.
#
# Report error with code 100, never 1:
# - Error code 100 (KJob::UserDefinedError) informs Purpose of failure. Spectacle
#   treats error code 1 as "cancelled by user" and silently drops the message.
# - Because host applications display error messages inconsistently (Krusader
#   suppresses error dialogs), the plugin also triggers a desktop notification.
#------------------------------------------------------------------------------
fail() {
    reply "$(jq -nc --arg t "$1" '{error: 100, errorText: $t}')"
    finish
    notify dialog-error "Uguu upload failed" "$(esc "$1")" critical
    exit 1
}

# Fail unless command $1 is installed.
require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "'$1' is required but not installed."
}

#------------------------------------------------------------------------------
# NOTIFY
#------------------------------------------------------------------------------
# Shows a Plasma notification via the freedesktop Notifications D-Bus API.
# Errors are ignored: a failed notification must not hide the upload result.
#
# Arguments:
#   $1 : icon name (e.g. "cloud-upload", "dialog-error")
#   $2 : summary title
#   $3 : body markup (escape user text with esc)
#   $4 : "critical" for an urgent notification (optional)
#
# D-Bus call details:
# - Signature 'susssasa{sv}i' argument layout:
#   app(s), replaces_id(u), icon(s), summary(s), body(s), actions(as),
#   hints(a{sv}), timeout(i).
# - "--" stops busctl from parsing the -1 (default timeout) as an option.
# - Setting urgency = 2 keeps error notifications visible until dismissed.
#------------------------------------------------------------------------------
notify() {
    local hints=(0)

    [[ ${4-} == critical ]] && hints=(1 urgency y 2)

    busctl --user call -- org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify 'susssasa{sv}i' \
        Uguu 0 "$1" "$2" "$3" 0 "${hints[@]}" -1 >/dev/null
}

# Print $1 escaped for notification markup (e.g. "Tom & Jerry.png").
# Quoted replacements prevent bash 5.2+ from interpreting '&' as the match.
esc() {
    local s=${1//&/'&amp;'}

    s=${s//</'&lt;'}
    printf '%s' "${s//>/'&gt;'}"
}

# Copy text to the clipboard via Plasma's Klipper service.
# Uses D-Bus to communicate with Klipper, which retains clipboard history
# after the plugin process exits.
copy_to_clipboard() {
    busctl --user call org.kde.klipper /klipper org.kde.klipper.klipper \
        setClipboardContents s "$1" >/dev/null 2>&1
}

#------------------------------------------------------------------------------
# CBOR DECODER
#------------------------------------------------------------------------------
# Minimal CBOR reader over the global byte array "bytes" at index "pos".
# Covers what QCborValue::fromJsonValue() emits: definite-length maps and
# arrays, text strings, integers, booleans, null and doubles.
#------------------------------------------------------------------------------

#------------------------------------------------------------------------------
# CBOR HEAD
#------------------------------------------------------------------------------
# Reads one CBOR item header and sets:
#   major : major type (0 uint, 3 text, 4 array, 5 map, 7 simple/float)
#   arg   : length, count, or value
#
# Header byte layout:
# - Top 3 bits (b / 32): major type.
# - Low 5 bits (b % 32): "additional info".
#
# Additional info decoding:
# - 0..23  : argument encoded directly.
# - 24..27 : argument follows in 1, 2, 4, or 8 bytes (2 ** (ai - 24)).
# - >27    : indefinite length (never emitted by Qt here).
#
# Arithmetic note:
# - Uses division and modulo instead of bit shifts (<<) so editors do not
#   mistake them for heredocs.
#------------------------------------------------------------------------------
cbor_head() {
    local b=$((bytes[pos++])) ai n

    major=$((b / 32))
    ai=$((b % 32))
    arg=$ai

    if ((ai >= 24 && ai <= 27)); then
        n=$((2 ** (ai - 24)))
        arg=0
        while ((n--)); do
            arg=$((arg * 256 + bytes[pos++]))
        done
    elif ((ai > 27)); then
        return 1 # indefinite length: never emitted by Qt here
    fi
}

# Read one text string into global variable "str".
cbor_str() {
    cbor_head && ((major == 3)) || return 1
    local hex

    printf -v hex '\\x%02x' "${bytes[@]:pos:arg}"
    printf -v str %b "$hex"
    ((pos += arg))
}

#------------------------------------------------------------------------------
# CBOR SKIP
#------------------------------------------------------------------------------
# Skips one item of any type, including nested arrays and maps.
#------------------------------------------------------------------------------
cbor_skip() {
    cbor_head || return 1
    local m=$major a=$arg

    case $m in
        2 | 3) ((pos += a)) ;;
        4) while ((a--)); do cbor_skip || return 1; done ;;
        5) ((a *= 2)); while ((a--)); do cbor_skip || return 1; done ;;
        6) cbor_skip ;;
    esac
    return 0
}

# Parse the command line Purpose starts the plugin with.
# Sets "server" from the --server argument; other arguments are ignored.
parse_args() {
    server=
    while (($#)); do
        [[ $1 == --server ]] && server=${2-}
        shift
    done
}

#------------------------------------------------------------------------------
# READ PAYLOAD
#------------------------------------------------------------------------------
# Reads the CBOR payload from the socket into the byte array "bytes".
#
# Step 2: Read request before failing.
# - The complete request payload must be read before validating dependencies
#   or input files: closing a socket with unread data resets the connection
#   and drops the error reply.
# - Purpose sends "<byte count>\n" followed by the binary CBOR payload.
#------------------------------------------------------------------------------
read_payload() {
    local len

    read -r len <&"$sock_in"
    mapfile -t bytes < <(head -c "$len" <&"$sock_in" | od -An -v -tu1 -w1)
    ((${#bytes[@]} == len)) || fail "Short read from Purpose (${#bytes[@]}/$len bytes)."
}

#------------------------------------------------------------------------------
# PARSE URLS
#------------------------------------------------------------------------------
# Extracts the "urls" array from the payload map into global array "urls".
#------------------------------------------------------------------------------
parse_urls() {
    local keys n

    urls=()
    pos=0

    cbor_head && ((major == 5)) || fail "Unexpected payload from Purpose."

    for ((keys = arg; keys--; )); do
        cbor_str || fail "Unexpected payload from Purpose."
        if [[ $str == urls ]]; then
            cbor_head && ((major == 4)) || fail "Unexpected payload from Purpose."
            for ((n = arg; n--; )); do
                cbor_str || fail "Unexpected payload from Purpose."
                urls+=("$str")
            done
        else
            cbor_skip || fail "Unexpected payload from Purpose."
        fi
    done
}

#------------------------------------------------------------------------------
# RESOLVE PATH
#------------------------------------------------------------------------------
# Validates the single file:// URL and sets "path" to a readable local file.
# Percent-decodes the path and verifies the file exists and is readable.
#------------------------------------------------------------------------------
resolve_path() {
    ((${#urls[@]} == 1)) || fail "Select exactly one file (got ${#urls[@]})."
    [[ ${urls[0]} == file://* ]] || fail "Only local files are supported."

    path=${urls[0]#file://}
    printf -v path '%b' "${path//%/\\x}" # percent-decode

    [[ -f $path ]] || fail "Not a regular file: $path"
    [[ -r $path ]] || fail "Cannot read $path"
}

#------------------------------------------------------------------------------
# UPLOAD
#------------------------------------------------------------------------------
# Uploads "path" to uguu.se via multipart HTTP POST and sets "link".
#
# Step 4: Upload file and report progress.
# - The filename is quoted for curl -F, so names with , ; " work safely.
# - Uguu replies {"files": [{"url": ...}]} on success, or
#   {"description": "<error>"} on failure.
#------------------------------------------------------------------------------
upload() {
    local p resp code body f err

    reply '{"percent": 10}'
    p=${path//\\/\\\\}
    p=${p//\"/\\\"}

    resp=$(curl -sS --max-time 60 -w '\n%{http_code}' \
        -F "files[]=@\"$p\"" https://uguu.se/upload) ||
        fail "Upload failed: curl exit code $? (details in the journal)"

    code=${resp##*$'\n'}
    body=${resp%$'\n'*}

    mapfile -t f < <(jq -r '.files[0].url // "", .description // .error // ""' <<<"$body" 2>/dev/null)
    link=${f[0]-}
    err=${f[1]-}
    [[ -n $link ]] || fail "Uguu upload failed (HTTP $code): ${err:-unexpected response}"
}

#------------------------------------------------------------------------------
# REPORT SUCCESS
#------------------------------------------------------------------------------
# Copies the link to Klipper and displays a desktop notification.
#
# Step 5: Run slow desktop extras after closing the Purpose socket.
# - Runs only after closing the Purpose socket so the host application finishes
#   its task immediately without waiting for D-Bus calls.
#------------------------------------------------------------------------------
report_success() {
    local copied filename

    if copy_to_clipboard "$link"; then
        copied="Link copied to clipboard."
    else
        copied="Klipper is not running, so the link was <b>not</b> copied."
    fi

    filename=${path##*/}

    notify cloud-upload "Uploaded to Uguu" \
        "<b>$(esc "$filename")</b><br><a href=\"$link\">$link</a><br>$copied Expires in ~3 hours."
}

#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
main() {
    parse_args "$@"

    # Step 1: Establish socket before anything can fail (prevents host app crash)
    connect_socket

    # Step 2: Read payload before checking dependencies (prevents socket reset)
    read_payload

    # Step 3: Validate dependencies and input file
    require_command busctl
    parse_urls
    resolve_path

    # Step 4: Upload file and report progress
    upload
    reply "$(jq -nc --arg u "$link" '{percent: 100, output: {url: $u}}')"

    # Step 5: Close socket so host finishes immediately, then notify and copy
    finish
    report_success
}

main "$@"
