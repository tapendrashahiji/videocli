#!/bin/sh
# videocli installer for Linux and macOS.
#
#   curl -LsSf https://<project-domain>/install.sh | sh                      # asks which profile
#   curl -LsSf https://<project-domain>/install.sh | sh -s -- --profile artistic
#
# Steps: ensure uv, `uv tool install videocli`, then `videocli setup --profile NAME`.
# Safe to re-run: it upgrades in place.
#
# Environment:
#   VIDEOCLI_INSTALL_SOURCE  package spec for uv (default: videocli). Example:
#                            git+https://github.com/tapendrashahiji/videocli
set -eu

PROFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --profile=*) PROFILE="${1#--profile=}"; shift ;;
    -h|--help)
      echo "usage: install.sh [--profile lecture|finishing|artistic|full]"; exit 0 ;;
    *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "videocli-install: $*" >&2; }

SOURCE="${VIDEOCLI_INSTALL_SOURCE:-videocli}"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    say "installing uv"
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | sh
    else
      say "need curl or wget to install uv"; exit 3
    fi
    PATH="$HOME/.local/bin:$PATH"
  fi
fi
export PATH

say "installing $SOURCE"
uv tool install --upgrade --python ">=3.10" "$SOURCE"
uv tool update-shell >/dev/null 2>&1 || true

BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
VIDEOCLI="$BIN_DIR/videocli"
[ -x "$VIDEOCLI" ] || VIDEOCLI="videocli"

has_tty() { [ -t 2 ] && (exec </dev/tty) 2>/dev/null; }

if [ -n "$PROFILE" ]; then
  "$VIDEOCLI" setup --profile "$PROFILE"
elif has_tty; then
  # Under `curl ... | sh` standard input is this script, so setup reads the answer from the terminal.
  "$VIDEOCLI" setup </dev/tty >/dev/tty
else
  say "no terminal attached: installing the 'lecture' profile"
  "$VIDEOCLI" setup --profile lecture --json >/dev/null || true
  say "add more later with: videocli setup --profile finishing   (or --with GROUP)"
fi

say "done. Check with: videocli doctor"
