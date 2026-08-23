#!/usr/bin/env bash
# uninstall.sh — remove the usage-dashboard plugin from a Hermes install (macOS/Linux).
#
# Safety: each folder is only deleted if it contains OUR manifest.json
# (name == "usage-dashboard"), so we never delete someone else's plugin.
#
# Usage: ./uninstall.sh [--hermes-dir PATH] [--dry-run]
# Exit codes: 0 ok · 1 Hermes dir not found · 5 safety check failed

set -u

HERMES_DIR="${HERMES_DIR:-}"
DRY_RUN=0
SHIFT_NEEDED=0

while [ $# -gt 0 ]; do
    case "$1" in
        --hermes-dir) SHIFT_NEEDED=1 ;;
        --dry-run|-n) DRY_RUN=1 ;;
        *)
            if [ "$SHIFT_NEEDED" = "1" ]; then
                HERMES_DIR="$1"; SHIFT_NEEDED=0
            else
                echo "ERROR: unknown argument '$1'" >&2
                echo "Usage: $0 [--hermes-dir PATH] [--dry-run]" >&2
                exit 3
            fi ;;
    esac
    shift
done

die() { printf 'ERROR: %s\n' "$1" >&2; exit "$2"; }

if [ -z "$HERMES_DIR" ]; then
    for cand in \
        "$HOME/.local/share/hermes" \
        "$HOME/Library/Application Support/hermes"
    do
        if [ -d "$cand" ]; then HERMES_DIR="$cand"; break; fi
    done
fi
[ -n "$HERMES_DIR" ] && [ -d "$HERMES_DIR" ] ||
    die "Hermes directory not found. Pass it explicitly: ./uninstall.sh --hermes-dir /path/to/hermes" 1

is_ours() { # $1 = candidate folder
    m="$1/manifest.json"
    [ -f "$m" ] || return 1
    grep -q '"name"[[:space:]]*:[[:space:]]*"usage-dashboard"' "$m"
}

REMOVED_ANY=0
for t in \
    "$HERMES_DIR/plugins/usage-dashboard" \
    "$HERMES_DIR/desktop-plugins/usage-dashboard"
do
    if [ ! -d "$t" ]; then
        echo "--  not installed: $t"
        continue
    fi
    if ! is_ours "$t"; then
        die "Safety check failed: '$t' exists but has no usage-dashboard manifest.json — refusing to delete." 5
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "[dry-run] would remove $t"
    else
        rm -rf "$t"
        printf '\033[32mremoved %s\033[0m\n' "$t"
    fi
    REMOVED_ANY=1
done

if [ "$REMOVED_ANY" = "0" ] && [ "$DRY_RUN" = "0" ]; then
    echo "Nothing to uninstall — usage-dashboard is not installed."
fi

echo ""
printf '\033[33mRestart Hermes once — backend mounts are refreshed at process start\033[0m\n'
exit 0
