#!/usr/bin/env bash
# Install / update `rvl` (and `rvl-server`).
#
# Rules:
#   - uv missing  -> install uv (via mise if present, else curl)
#   - install rvl -> force install (uv tool install --force)
#
# Usage:
#   ./install.sh [--help|-h]
#
# Idempotent: exits 0 on success.

set -euo pipefail

REPO="git+https://github.com/tayaee/remote-vscode-launcher.git"
PKG="remote-vscode-launcher"

log() { echo "[install.sh] $*" >&2; }

usage() {
    echo "Usage: ./install.sh [--help|-h]"
}

for arg in "$@"; do
    case "$arg" in
        --help|-h) usage; exit 0 ;;
        *) log "unknown argument: $arg"; usage >&2; exit 2 ;;
    esac
done

add_to_path() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$1:$PATH" ;;
    esac
}

ensure_local_path() {
    add_to_path "$HOME/.local/share/mise/shims"
    add_to_path "$HOME/.local/bin"
    add_to_path "$HOME/.cargo/bin"
}

ensure_uv() {
    ensure_local_path
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    if command -v mise >/dev/null 2>&1; then
        log "uv not found; installing via mise..."
        echo "+ mise use -g uv"
        mise use -g uv
        ensure_local_path
    else
        log "uv not found; installing via curl..."
        if ! command -v curl >/dev/null 2>&1; then
            log "error: curl not found; install curl or mise first."
            exit 1
        fi
        echo "+ curl -LsSf https://astral.sh/uv/install.sh | sh"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        ensure_local_path
    fi
    if ! command -v uv >/dev/null 2>&1; then
        log "error: uv install finished but 'uv' is still not on PATH."
        log "hint: open a new shell (or export PATH=\"\$HOME/.local/bin:\$PATH\")."
        exit 1
    fi
    log "uv ready: $(uv --version)"
}

do_install() {
    log "installing ${PKG} (uv tool install --force)..."
    echo + uv tool install --from "$REPO" --force "$PKG"
    uv tool install --from "$REPO" --force "$PKG"
    ensure_local_path
    hash -r 2>/dev/null || true
    if command -v rvl >/dev/null 2>&1; then
        log "done: $(rvl --version 2>&1 || echo 'rvl installed')"
    else
        log "warning: install finished but 'rvl' is not on PATH."
        log "hint: export PATH=\"\$HOME/.local/bin:\$PATH\" (or reopen the shell)."
    fi
    if command -v rvl-server >/dev/null 2>&1; then
        log "server ok: $(rvl-server --version 2>&1 || echo 'rvl-server installed')"
    else
        log "warning: 'rvl-server' is not on PATH."
    fi
}

main() {
    ensure_uv
    do_install
}

main
(set -x; rvl --version)
(set -x; rvl-server --version)
