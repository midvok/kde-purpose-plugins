#!/usr/bin/env bash
#==============================================================================
# install.sh - Install kde-purpose-plugins into KDE Share menus
#==============================================================================
# Description:
# - Builds each plugin's KPackage directory structure and copies it into:
#   ~/.local/share/kpackage/Purpose/<plugin>/
# - Installs Python plugins by default; use -i to select an implementation.
# - Re-run after making changes: installed files are copies, not symlinks.
#
# Usage: ./install.sh [-l|--list] [-i IMPL] [-r|--remove] [PLUGIN...]
#
#------------------------------------------------------------------------------
# Author: midvok
# Update: 2026-10-22
#==============================================================================

# Bash strict mode
set -euo pipefail

#------------------------------------------------------------------------------
# HELP & USAGE
#------------------------------------------------------------------------------
usage() {
    cat <<EOF
Install or remove KDE Purpose export plugins in ~/.local/share/kpackage/Purpose/.
Installs Python plugins by default unless specified otherwise with -i.

Usage: ./install.sh [OPTIONS] [PLUGIN...]

Options:
  -l, --list    List available plugins and their implementations
  -i IMPL       Plugin implementation to install (default: python)
  -r, --remove  Remove the specified plugins (or all installed plugins)
  -h, --help    Display this help message

Arguments:
  PLUGIN        One or more plugin IDs to install (default: all)
EOF
}

#------------------------------------------------------------------------------
# CONSTANTS & GLOBALS
#------------------------------------------------------------------------------
# Resolve absolute paths: repository root, source tree, and Purpose install dir.
REPO=$(cd "$(dirname "$0")" && pwd)
SRC=$REPO/src
DEST=${XDG_DATA_HOME:-$HOME/.local/share}/kpackage/Purpose

# Target implementation (defaults to python)
TARGET_IMPL=python

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
# Discovers all existing plugins across src/ and prints a formatted table
# showing their available implementations (e.g. bash, python).
#------------------------------------------------------------------------------
list_plugins() {
    local dir rel impl id
    local -A seen=() plugin_impls=()
    local plugins=()

    # Discover unique plugin IDs and implementations across all source trees
    for dir in "$SRC"/*/*/; do
        [[ -f $dir/metadata.json ]] || continue
        rel=${dir#"$SRC"/}
        impl=${rel%%/*}
        id=${dir%/}
        id=${id##*/}

        if [[ -z ${seen[$id]:-} ]]; then
            seen[$id]=1
            plugins+=("$id")
        fi

        if [[ -z ${plugin_impls[$id]:-} ]]; then
            plugin_impls[$id]="$impl"
        else
            plugin_impls[$id]="${plugin_impls[$id]}, $impl"
        fi
    done

    # Sort plugin IDs alphabetically
    ((${#plugins[@]})) && mapfile -t plugins < <(printf '%s\n' "${plugins[@]}" | sort)

    # Format output as an aligned table
    {
        printf '%s\t%s\n' "PLUGIN" "AVAILABLE"
        for id in "${plugins[@]}"; do
            printf '%s\t%s\n' "$id" "${plugin_impls[$id]}"
        done
    } | column -t -s $'\t'
}

#------------------------------------------------------------------------------
# Prints the unique IDs of all available plugins for implementation $1
# (defaults to $TARGET_IMPL).
#------------------------------------------------------------------------------
all_plugins() {
    local impl=${1:-$TARGET_IMPL} dir

    # Find directories containing metadata.json for the target implementation
    for dir in "$SRC/$impl"/*/; do
        [[ -f $dir/metadata.json ]] || continue
        dir=${dir%/}
        printf '%s\n' "${dir##*/}"
    done
}

#------------------------------------------------------------------------------
# INSTALL
#------------------------------------------------------------------------------
# Installs plugin $1 into $DEST/$1 using implementation $TARGET_IMPL:
#   metadata.json + contents/code/main.* (+ purpose_common.py for Python)
#
# Copies files rather than creating symlinks: Python resolves symlinked scripts
# to their canonical target directory before populating sys.path, which would
# prevent main.py from finding purpose_common.py next to it.
#------------------------------------------------------------------------------
install_plugin() {
    local id=$1 impl=$TARGET_IMPL src target code

    src=$SRC/$impl/$id
    target=$DEST/$id
    code=$target/contents/code
    [[ -f $src/metadata.json ]] || die "Plugin '$id' has no '$impl' implementation."

    # Safely remove existing installation and create KPackage directory layout
    remove_plugin "$id" || true
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
# REMOVE
#------------------------------------------------------------------------------
# Safely removes plugin $1 from $DEST if present. Returns 0 if removed, 1 if
# the plugin was not installed.
#
# Only removes a symlink (e.g. an old Stow link) or a directory verified to
# contain a Purpose metadata.json file, never touching unexpected folders.
#------------------------------------------------------------------------------
remove_plugin() {
    local target=$DEST/$1

    if [[ -L $target ]]; then
        # Remove legacy symlinks (e.g. from Stow)
        rm -f "$target"
        return 0
    elif [[ -f $target/metadata.json ]]; then
        # Verified Purpose plugin folder: safe to delete
        rm -rf "$target"
        return 0
    elif [[ -e $target ]]; then
        die "$target exists but is not a Purpose plugin, not touching it."
    fi

    return 1
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
    local remove=0 list=0 plugins=() id

    TARGET_IMPL=python

    # Parse CLI options
    while (($#)); do
        case $1 in
            -l | --list) list=1 ;;
            -i) shift
                (($#)) || die "Option -i requires an argument (e.g. 'bash', 'python')"
                TARGET_IMPL=$1
                ;;
            -r | --remove) remove=1 ;;
            -h | --help) usage; exit 0 ;;
            -*) die "Unknown option: $1 (see --help)" ;;
            *) plugins+=("$1") ;;
        esac
        shift
    done

    # List plugins
    if ((list)); then
        list_plugins
        exit 0
    fi

    # Validate target implementation exists when installing
    if ! ((remove)); then
        [[ -d $SRC/$TARGET_IMPL ]] || \
            die "Unknown implementation '$TARGET_IMPL' ($SRC/$TARGET_IMPL does not exist)."
    fi

    # Default to all discovered plugins for the target implementation
    ((${#plugins[@]})) || mapfile -t plugins < <(all_plugins "$TARGET_IMPL")
    ((${#plugins[@]})) || die "No plugins found for implementation: $TARGET_IMPL"

    # Validate destination directory before making changes
    check_dest

    # Install or remove each target plugin
    for id in "${plugins[@]}"; do
        if ((remove)); then
            if remove_plugin "$id"; then
                printf 'Removed %s\n' "$id"
            fi
        else
            install_plugin "$id"
        fi
    done

    # Verify dependencies for installed variant
    ((remove)) || check_deps "$TARGET_IMPL"
}

main "$@"
