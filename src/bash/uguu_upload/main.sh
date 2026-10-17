#!/usr/bin/env bash
#==============================================================================
# uguu_upload - KDE Purpose plugin: share a file to uguu.se
#==============================================================================
# Usage: main.sh --server SOCKET --pluginType Export --pluginPath PATH
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
#   socat, curl, jq : gated by exec: constraints in metadata.json
#   busctl          : checked at runtime (part of systemd)
#
# Install: ~/.local/share/kpackage/Purpose/uguu_upload/contents/code/main.sh
#------------------------------------------------------------------------------
# Update: 2026-10-02
#==============================================================================

#------------------------------------------------------------------------------
# SOCKET
#------------------------------------------------------------------------------
# Connects to the Purpose socket and sets sock_in, sock_out, sock_pid.
#
# - Must run before anything can fail: if we exit without connecting,
#     ProcessJob dereferences a null socket and crashes the host app.
# - Coproc fds are closed in subshells, so they are moved to ordinary fds.
#     The originals are closed too, or socat never sees EOF.
# - socat -t 0.1: Qt never closes its side, so don't wait the default 0.5 s.
#------------------------------------------------------------------------------
connect_socket() {
    coproc SOCK { socat -t 0.1 - "UNIX-CONNECT:$server"; }
    # shellcheck disable=SC2153  # SOCK_PID is set by coproc
    sock_pid=$SOCK_PID
    local co_in=${SOCK[0]} co_out=${SOCK[1]}
    exec {sock_in}<&"$co_in" {sock_out}>&"$co_out"
    exec {co_in}<&- {co_out}>&-
}

#------------------------------------------------------------------------------
# Sends one JSON line to Purpose.
#------------------------------------------------------------------------------
reply() {
    printf '%s\n' "$1" >&"$sock_out"
}

#------------------------------------------------------------------------------
# Closes the socket and waits for socat to flush the last reply.
#------------------------------------------------------------------------------
finish() {
    exec {sock_out}>&-
    wait "$sock_pid"
}

#------------------------------------------------------------------------------
# ERRORS
#------------------------------------------------------------------------------
# Reports an error to the host app and as a notification, then exits.
#
# - Error code 100 (KJob::UserDefinedError): Spectacle treats 1 as
#     "cancelled" and silently drops the message.
# - Hosts show errors inconsistently (Krusader not at all), hence the
#     extra notification.
#------------------------------------------------------------------------------
fail() {
    reply "$(jq -nc --arg t "$1" '{error: 100, errorText: $t}')"
    finish
    notify dialog-error "Uguu upload failed" "$(esc "$1")" critical
    exit 1
}

#------------------------------------------------------------------------------
# Fails unless command $1 is installed.
#------------------------------------------------------------------------------
require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "'$1' is required but not installed."
}

#------------------------------------------------------------------------------
# DESKTOP
#------------------------------------------------------------------------------
# Shows a Plasma notification via the freedesktop Notifications D-Bus API.
#   $1 : icon name
#   $2 : summary
#   $3 : body (markup, escape with esc)
#   $4 : "critical" for an urgent notification (optional)
#
# - "--" stops busctl from parsing the -1 (default timeout) as an option.
#------------------------------------------------------------------------------
notify() {
    local hints=(0)
    [[ ${4-} == critical ]] && hints=(1 urgency y 2)
    busctl --user call -- org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify 'susssasa{sv}i' \
        Uguu 0 "$1" "$2" "$3" 0 "${hints[@]}" -1 >/dev/null
}

#------------------------------------------------------------------------------
# Prints $1 escaped for notification markup (e.g. "Tom & Jerry.png").
#
# - Replacements are quoted: since bash 5.2 an unquoted & means "the match".
#------------------------------------------------------------------------------
esc() {
    local s=${1//&/'&amp;'}
    s=${s//</'&lt;'}
    printf '%s' "${s//>/'&gt;'}"
}

#------------------------------------------------------------------------------
# Copies $1 to the clipboard via Klipper, which keeps it after we exit.
#------------------------------------------------------------------------------
copy_to_clipboard() {
    busctl --user call org.kde.klipper /klipper org.kde.klipper.klipper \
        setClipboardContents s "$1" >/dev/null 2>&1
}

#------------------------------------------------------------------------------
# CBOR
#------------------------------------------------------------------------------
# Minimal CBOR reader over the global byte array "bytes" at index "pos".
# Covers what QCborValue::fromJsonValue() emits: definite-length maps and
# arrays, text strings, integers, booleans, null and doubles.
#------------------------------------------------------------------------------

#------------------------------------------------------------------------------
# Reads one item header and sets:
#   major : major type (0 uint, 3 text, 4 array, 5 map, 7 simple/float)
#   arg   : length, count or value
#
# - Header byte: top 3 bits = major type, low 5 bits = "additional info".
#     Info 24..27 means the argument follows in 1, 2, 4 or 8 bytes.
# - No bit shifts (<<): VS Code mistakes them for heredocs.
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

#------------------------------------------------------------------------------
# Reads one text string into "str".
#------------------------------------------------------------------------------
cbor_str() {
    cbor_head && ((major == 3)) || return 1
    local hex
    printf -v hex '\\x%02x' "${bytes[@]:pos:arg}"
    printf -v str %b "$hex"
    ((pos += arg))
}

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

#------------------------------------------------------------------------------
# INPUT
#------------------------------------------------------------------------------
# Sets "server" from the --server argument; other arguments are ignored.
#------------------------------------------------------------------------------
parse_args() {
    server=
    while (($#)); do
        [[ $1 == --server ]] && server=${2-}
        shift
    done
}

#------------------------------------------------------------------------------
# Reads the CBOR payload from the socket into the byte array "bytes".
#------------------------------------------------------------------------------
read_payload() {
    local len
    read -r len <&"$sock_in"
    mapfile -t bytes < <(head -c "$len" <&"$sock_in" | od -An -v -tu1 -w1)
    ((${#bytes[@]} == len)) || fail "Short read from Purpose (${#bytes[@]}/$len bytes)."
}

#------------------------------------------------------------------------------
# Extracts the "urls" array from the payload map into "urls".
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
# Validates the single file:// URL and sets "path" to a readable local file.
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
# Uploads "path" to uguu.se and sets "link".
#
# - The filename is quoted for curl -F, so names with , ; " work.
# - Uguu replies {"files": [{"url": ...}]} or {"description": "<error>"}.
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
# Copies the link and shows the success notification.
# Runs after the socket is closed, so the host app never waits for it.
#------------------------------------------------------------------------------
report_success() {
    local copied shown
    if copy_to_clipboard "$link"; then
        copied="Link copied to clipboard."
    else
        copied="Klipper is not running, so the link was <b>not</b> copied."
    fi
    shown=${path/#"$HOME"/'~'}
    notify cloud-upload "Uploaded to Uguu" \
        "<b>$(esc "$shown")</b><br><a href=\"$link\">$link</a><br>$copied Expires in ~3 hours."
}

#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
main() {
    parse_args "$@"
    connect_socket # first, see connect_socket
    require_command busctl
    read_payload
    parse_urls
    resolve_path
    upload
    reply "$(jq -nc --arg u "$link" '{percent: 100, output: {url: $u}}')"
    finish
    report_success
}

main "$@"
