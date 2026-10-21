#!/usr/bin/env bash
#==============================================================================
# install.sh - Install kde-purpose-plugins into KDE Share menus
#==============================================================================
# Description:
# - Builds each plugin's KPackage directory structure and copies it into:
#   ~/.local/share/kpackage/Purpose/<plugin>/
# - Re-run after making changes: installed files are copies, not symlinks.
#
# Usage: ./install.sh [--bash] [--uninstall] [PLUGIN...]
#
#------------------------------------------------------------------------------
# Author: midvok
# Update: 2026-10-21
#==============================================================================

# Bash strict mode
set -euo pipefail

#------------------------------------------------------------------------------
# HELP & USAGE
#------------------------------------------------------------------------------
usage() {
    cat <<EOF
Install or remove KDE Purpose export plugins in ~/.local/share/kpackage/Purpose/.

Usage: ./install.sh [OPTIONS] [PLUGIN...]

Options:
  --bash       Use the Bash version where available (e.g. uguu_upload)
  --uninstall  Remove the specified plugins (or all installed plugins)
  -h, --help   Display this help message

Arguments:
  PLUGIN       One or more plugin IDs to install (default: all in src/python/)
EOF
}

#------------------------------------------------------------------------------
# CONSTANTS & GLOBALS
#------------------------------------------------------------------------------
# Resolve absolute paths: repository root, source tree, and Purpose install dir.
REPO=$(cd "$(dirname "$0")" && pwd)
SRC=$REPO/src
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/kpackage/Purpose

# Global CLI flags
USE_BASH=0

#------------------------------------------------------------------------------
# HELPERS
#------------------------------------------------------------------------------
die() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

warn() {
    printf 'Warning: %s\n' "$1" >&2
}

#------------------------------------------------------------------------------
# DISCOVERY
#------------------------------------------------------------------------------
# Prints the unique IDs of all available plugins (one per line): discovers
# every directory under src/python/ except the shared common/ library.
#------------------------------------------------------------------------------
all_plugins() {
    local dir

    # Strip trailing slash with ${dir%/} and extract directory name with ${dir##*/}
    for dir in "$SRC"/python/*/; do
        dir=${dir%/}
        [[ ${dir##*/} != common ]] && printf '%s\n' "${dir##*/}"
    done
}

#------------------------------------------------------------------------------
# Returns the implementation variant ("bash" or "python") to install for
# plugin $1 based on availability and the --bash flag.
#------------------------------------------------------------------------------
implementation() {
    # Prefer Bash version if requested and available; otherwise use Python
    if ((USE_BASH)) && [[ -d $SRC/bash/$1 ]]; then
        echo bash
    else
        echo python
    fi
}

#------------------------------------------------------------------------------
# INSTALL
#------------------------------------------------------------------------------
# Installs plugin $1 into $DEST/$1:
#   metadata.json + contents/code/main.* (+ purpose_common.py for Python)
#
# Copies files rather than creating symlinks: Python resolves symlinked scripts
# to their canonical target directory before populating sys.path, which would
# prevent main.py from finding purpose_common.py next to it.
#------------------------------------------------------------------------------
install_plugin() {
    local id=$1 impl src target code

    impl=$(implementation "$id")
    src=$SRC/$impl/$id
    target=$DEST/$id
    code=$target/contents/code
    [[ -f $src/metadata.json ]] || die "Unknown plugin: $id"

    # Safely remove existing installation and create KPackage directory layout
    remove_plugin "$id"
    mkdir -p "$code"

    # Copy metadata to plugin root; scripts and libraries go to contents/code/
    cp "$src/metadata.json" "$target/"
    cp "$src"/main.* "$code/"
    [[ $impl == python ]] && cp "$SRC/python/common/purpose_common.py" "$code/"

    # Ensure main script is executable
    chmod +x "$code"/main.*
    printf 'Installed %-16s (%s) -> %s\n' "$id" "$impl" "$target"
}

#------------------------------------------------------------------------------
# UNINSTALL
#------------------------------------------------------------------------------
# Safely removes plugin $1 from $DEST if present.
#
# Only removes a symlink (e.g. an old Stow link) or a directory verified to
# contain a Purpose metadata.json file, never touching unexpected folders.
#------------------------------------------------------------------------------
remove_plugin() {
    local target=$DEST/$1

    if [[ -L $target ]]; then
        # Remove legacy symlinks (e.g. from Stow)
        rm -f "$target"
    elif [[ -f $target/metadata.json ]]; then
        # Verified Purpose plugin folder: safe to delete
        rm -rf "$target"
    elif [[ -e $target ]]; then
        die "$target exists but is not a Purpose plugin, not touching it."
    fi
}

#------------------------------------------------------------------------------
# SANITY CHECKS
#------------------------------------------------------------------------------
# Refuses to install through a symlinked kpackage/ or kpackage/Purpose/ folder:
# with a stow-folded directory, files would land in the target repository
# (e.g. dotfiles) rather than ~/.local/share.
#------------------------------------------------------------------------------
check_dest() {
    local dir

    # Prevent installing into stow-folded directories which could overwrite repo files
    for dir in "$DEST" "${DEST%/*}"; do
        if [[ -L $dir ]]; then
            die "$dir is a symlink (stow-folded?). Replace with a real folder first."
        fi
    done
    return 0
}

#------------------------------------------------------------------------------
# Verifies runtime dependencies for the installed plugins and warns if any
# required tools or modules are missing from the system.
#------------------------------------------------------------------------------
check_deps() {
    local impls=$1 cmd

    # Check Python requirements
    if [[ $impls == *python* ]]; then
        command -v python3 >/dev/null || warn "python3 is not installed."
        python3 -c 'import requests' 2>/dev/null || \
            warn "Python module 'requests' is missing: install python3-requests."
    fi

    # Check Bash requirements
    if [[ $impls == *bash* ]]; then
        for cmd in socat curl jq; do
            command -v "$cmd" >/dev/null || \
                warn "'$cmd' is not installed (needed by the Bash version)."
        done
    fi

    # Check D-Bus tool (used for notifications and Klipper clipboard)
    command -v busctl >/dev/null ||
        warn "'busctl' is not installed (part of systemd)."
}

#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
main() {
    local uninstall=0 plugins=() id impls=""
    USE_BASH=0

    # Parse command-line options and target plugin arguments
    while (($#)); do
        case $1 in
            --bash) USE_BASH=1 ;;
            --uninstall) uninstall=1 ;;
            -h | --help) usage; exit 0 ;;
            -*) die "Unknown option: $1 (see --help)" ;;
            *) plugins+=("$1") ;;
        esac
        shift
    done

    # Default to all discovered plugins if none were specified
    ((${#plugins[@]})) || mapfile -t plugins < <(all_plugins)

    # Validate destination directory before making changes
    check_dest

    # Install or remove each target plugin
    for id in "${plugins[@]}"; do
        if ((uninstall)); then
            remove_plugin "$id"
            printf 'Removed %s\n' "$id"
        else
            install_plugin "$id"
            impls+=" $(implementation "$id")"
        fi
    done

    # Verify dependencies for installed variants
    ((uninstall)) || check_deps "$impls"
}

main "$@"
