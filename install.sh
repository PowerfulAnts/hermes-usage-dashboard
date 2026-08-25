#!/usr/bin/env bash
# install.sh — install the usage-dashboard plugin into a Hermes install (macOS/Linux).
#
# Copies:
#   dashboard/plugin_api.py   -> <hermes>/plugins/usage-dashboard/dashboard/plugin_api.py
#   dashboard/manifest.json   -> <hermes>/plugins/usage-dashboard/dashboard/manifest.json
#   dashboard/sources.py      -> <hermes>/plugins/usage-dashboard/dashboard/sources.py
#   desktop-plugins/usage-dashboard/plugin.js
#                             -> <hermes>/desktop-plugins/usage-dashboard/plugin.js
#
# Usage: ./install.sh [--hermes-dir PATH] [--dry-run]
# Exit codes: 0 ok · 1 Hermes dir not found · 2 repo sources missing · 3 copy failed

set -u

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
HERMES_DIR="${HERMES_DIR:-}"
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --hermes-dir) shift_needed=1 ;;                       # handled below
        --dry-run|-n) DRY_RUN=1 ;;
        *)
            if [ "${shift_needed:-0}" = "1" ]; then
                HERMES_DIR="$arg"; shift_needed=0
            else
                echo "ERROR: unknown argument '$arg'" >&2
                echo "Usage: $0 [--hermes-dir PATH] [--dry-run]" >&2
                exit 3
            fi ;;
    esac
done
# support "--hermes-dir PATH" given as two words
if [ -z "$HERMES_DIR" ] && [ $# -ge 2 ] && [ "$1" = "--hermes-dir" ]; then
    HERMES_DIR="$2"
fi

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    OK  %s\n' "$1"; }
warn() { printf '    !!  %s\n' "$1" >&2; }
die()  { printf 'ERROR: %s\n' "$1" >&2; exit "$2"; }

# ---------------------------------------------------------------- locate Hermes
if [ -z "$HERMES_DIR" ]; then
    for cand in \
        "$HOME/.local/share/hermes" \
        "$HOME/Library/Application Support/hermes"
    do
        if [ -d "$cand" ]; then
            HERMES_DIR="$cand"
            break
        fi
    done
fi
if [ -z "$HERMES_DIR" ] || [ ! -d "$HERMES_DIR" ]; then
    die "Hermes directory not found. Looked in:
      \$HOME/.local/share/hermes
      \$HOME/Library/Application Support/hermes
    Install Hermes first, or point the installer at it explicitly:
      ./install.sh --hermes-dir '/path/to/hermes'
    (or: HERMES_DIR=/path/to/hermes ./install.sh)" 1
fi
step "Hermes dir: $HERMES_DIR"

PLUGIN_ROOT="$HERMES_DIR/plugins/usage-dashboard/dashboard"
DESKTOP_DEST="$HERMES_DIR/desktop-plugins/usage-dashboard"
# A previous universal-tracker install may have left a backend/ tree behind;
# remove it so dead adapters cannot shadow the current single-module backend.
LEGACY_BACKEND_DEST="$PLUGIN_ROOT/backend"

# ------------------------------------------------------------- source manifest
SRC_PLUGIN_API="$REPO_ROOT/dashboard/plugin_api.py"
SRC_MANIFEST="$REPO_ROOT/dashboard/manifest.json"
SRC_SOURCES="$REPO_ROOT/dashboard/sources.py"
SRC_DESKTOP_JS="$REPO_ROOT/desktop-plugins/usage-dashboard/plugin.js"

for f in "$SRC_PLUGIN_API" "$SRC_MANIFEST" "$SRC_SOURCES" "$SRC_DESKTOP_JS"; do
    [ -e "$f" ] || die "repo source missing: $f" 2
done

copy_one() { # src dst_dir
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] $1 -> $2/$(basename "$1")"
        return 0
    fi
    mkdir -p "$2" || die "cannot create $2" 3
    cp -f "$1" "$2/" || die "copy failed: $1" 3
}

step "Copying plugin files"
copy_one "$SRC_PLUGIN_API" "$PLUGIN_ROOT"
copy_one "$SRC_MANIFEST"   "$PLUGIN_ROOT"
copy_one "$SRC_SOURCES"    "$PLUGIN_ROOT"
copy_one "$SRC_DESKTOP_JS" "$DESKTOP_DEST"

if [ "$DRY_RUN" = "1" ]; then
    step "Dry run complete — nothing was written."
    exit 0
fi

# Remove legacy adapter tree from earlier plugin versions (v2.x), if present.
if [ -d "$LEGACY_BACKEND_DEST" ]; then
    rm -rf "$LEGACY_BACKEND_DEST" || die "cannot remove legacy backend at $LEGACY_BACKEND_DEST" 3
    warn "Removed legacy backend/ tree from a previous install"
fi

# ------------------------------------------------------------ post-copy verify
step "Verifying installed files"
FAILED=0
for f in "$PLUGIN_ROOT/manifest.json" "$PLUGIN_ROOT/plugin_api.py" \
         "$PLUGIN_ROOT/sources.py" "$DESKTOP_DEST/plugin.js"; do
    if [ -e "$f" ]; then ok "$(basename "$f")"; else warn "MISSING $f"; FAILED=1; fi
done

# Best-effort byte-compile of the installed backend (skipped if no python).
PYBIN=""
command -v python3 >/dev/null 2>&1 && PYBIN="python3"
[ -z "$PYBIN" ] && command -v python >/dev/null 2>&1 && PYBIN="python"
if [ -n "$PYBIN" ]; then
    step "Byte-compiling installed backend"
    if "$PYBIN" -m compileall -q "$PLUGIN_ROOT" >/dev/null 2>&1; then
        ok "compiled cleanly"
    else
        warn "compileall reported issues (best-effort check, continuing)"
    fi
else
    echo "    --  python not found, skipping compile check"
fi

[ "$FAILED" = "1" ] && die "verification failed — see MISSING lines above" 4

echo ""
printf '\033[32mInstalled usage-dashboard into %s\033[0m\n' "$HERMES_DIR"
printf '\033[33mRestart Hermes once — backend mounts at process start\033[0m\n'
exit 0
