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

# Ensure ${BIN_DIR} is on PATH — both for this session and future shells.
ensure_path_line() {
    rc_file="$1"
    [ -n "$rc_file" ] || return 0
    # Create the rc file if missing so the export persists.
    [ -e "$rc_file" ] || : > "$rc_file"
    if ! grep -Fq "# >>> sah PATH >>>" "$rc_file" 2>/dev/null; then
        {
            printf '\n# >>> sah PATH >>>\n'
            printf 'case ":$PATH:" in *":%s:"*) ;; *) export PATH="%s:$PATH";; esac\n' \
                "$BIN_DIR" "$BIN_DIR"
            printf '# <<< sah PATH <<<\n'
        } >> "$rc_file"
        echo "Added ${BIN_DIR} to PATH in ${rc_file}"
    fi
}

case ":$PATH:" in
    *":${BIN_DIR}:"*)
        on_path=1
        ;;
    *)
        on_path=0
        # Pick rc files based on the user's login shell, falling back to common ones.
        login_shell="$(basename "${SHELL:-}")"
        case "$login_shell" in
            zsh)  ensure_path_line "${ZDOTDIR:-$HOME}/.zshrc" ;;
            bash)
                # macOS bash reads .bash_profile for login shells; Linux uses .bashrc.
                if [ "$(uname -s)" = "Darwin" ]; then
                    ensure_path_line "$HOME/.bash_profile"
                else
                    ensure_path_line "$HOME/.bashrc"
                fi
                ;;
            fish) ensure_path_line "$HOME/.config/fish/config.fish" ;;
            *)
                # Best-effort: touch whichever rc files exist.
                [ -f "$HOME/.zshrc" ]        && ensure_path_line "$HOME/.zshrc"
                [ -f "$HOME/.bashrc" ]       && ensure_path_line "$HOME/.bashrc"
                [ -f "$HOME/.bash_profile" ] && ensure_path_line "$HOME/.bash_profile"
                ;;
        esac
        ;;
esac

echo
if [ "${on_path:-1}" -eq 1 ]; then
    echo "Try: sah info"
else
    echo "Open a new terminal (or run: exec \$SHELL -l) and then: sah info"
fi
