#!/usr/bin/env sh
# sah installer — drops the `sah` CLI into ~/.local/bin and persists the Hub URL.
#
# Usage on a client laptop:
#   curl http://192.168.3.16:9000/sah/install.sh | sh
#   curl http://192.168.3.16:9000/sah/install.sh | sh -s -- --hub http://other:9000
set -eu

HUB="${SAH_HUB:-http://192.168.3.16:9000}"
while [ $# -gt 0 ]; do
    case "$1" in
        --hub) HUB="$2"; shift 2;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done

BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/sah"
mkdir -p "$BIN_DIR" "$CONFIG_DIR"

echo "Downloading sah from ${HUB}/sah/sah ..."
curl -fsSL "${HUB}/sah/sah" -o "${BIN_DIR}/sah"
chmod +x "${BIN_DIR}/sah"

printf '%s\n' "$HUB" > "${CONFIG_DIR}/hub"

echo "Installed: ${BIN_DIR}/sah"
echo "Hub:       ${HUB}"
case ":$PATH:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "warning: ${BIN_DIR} is not on your PATH" >&2;;
esac
echo
echo "Try: sah info"
