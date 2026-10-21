#!/usr/bin/env bash
#==============================================================================
# run.sh - Run the kde-purpose-plugins integration test suite
#==============================================================================
# Description:
# - Checks that required CLI tools and Python packages are installed, then runs
#   test_plugins.py using local fakes for HTTP uploads and D-Bus calls.
# - Pass -v (or --verbose) to show plugin stderr output when a test case fails.
#
# Usage: ./tests/run.sh [-v]
#
#------------------------------------------------------------------------------
# Update: 2026-10-20
#==============================================================================

# Bash strict mode
set -euo pipefail

#------------------------------------------------------------------------------
# Fail unless every command in "$@" is available on PATH.
#------------------------------------------------------------------------------
require_commands() {
    local cmd missing=()
    for cmd; do
        command -v "$cmd" >/dev/null || missing+=("$cmd")
    done
    ((${#missing[@]} == 0)) || {
        echo "Missing required commands: ${missing[*]}" >&2
        exit 1
    }
}

#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
# Verify test dependencies and run test_plugins.py.
#------------------------------------------------------------------------------
main() {
    require_commands python3 socat jq
    python3 -c 'import requests' 2>/dev/null || {
        echo "Missing Python module: requests (install python3-requests)" >&2
        exit 1
    }

    cd "$(dirname "$0")"
    python3 test_plugins.py "$@"
}

main "$@"
