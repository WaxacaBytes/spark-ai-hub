#!/usr/bin/env sh
# sah installer — drops the `sah` CLI into ~/.local/bin and persists the Hub
# address(es). The Hub serves this script with its own stable names injected,
# so the client stores a name that survives the server's IP changing (DHCP).
#
# Usage on a client laptop:
#   curl -fsSL http://<hub>:9000/sah/install.sh | sh
#   curl -fsSL http://<hub>:9000/sah/install.sh | sh -s -- --hub http://other:9000
set -eu

# Candidate Hub URLs, most-stable first. The Hub replaces the marker below
# when it serves this script; if the marker survives (script run standalone),
# treat it as empty.
CANDIDATES="@SAH_CANDIDATES@"
case "$CANDIDATES" in *@SAH_CANDIDATES@*) CANDIDATES="" ;; esac

# Explicit overrides win and go to the front of the list.
HUB="${SAH_HUB:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --hub) HUB="$2"; shift 2;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done
[ -n "$HUB" ] && CANDIDATES="$HUB $CANDIDATES"
[ -n "$CANDIDATES" ] && [ "$CANDIDATES" != " " ] || CANDIDATES="http://localhost:9000"

# De-duplicate while preserving order (POSIX-safe, no arrays).
ORDERED=""
seen=""
for c in $CANDIDATES; do
    c="${c%/}"
    [ -n "$c" ] || continue
    case " $seen " in *" $c "*) continue ;; esac
    seen="$seen $c"
    ORDERED="$ORDERED $c"
done

BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/sah"
mkdir -p "$BIN_DIR" "$CONFIG_DIR"

# Download the CLI from the first candidate that actually serves it.
DOWNLOAD_HUB=""
for c in $ORDERED; do
    echo "Trying ${c}/sah/sah ..."
    if curl -fsSL -m 8 "${c}/sah/sah" -o "${BIN_DIR}/sah" 2>/dev/null; then
        DOWNLOAD_HUB="$c"
        break
    fi
done
if [ -z "$DOWNLOAD_HUB" ]; then
    echo "Could not reach the Hub at any of:${ORDERED}" >&2
    exit 1
fi
chmod +x "${BIN_DIR}/sah"

# Persist every candidate (stable name first). sah probes this list on each
# run and uses the first that answers, so a changed server IP self-heals.
: > "${CONFIG_DIR}/hub"
for c in $ORDERED; do
    printf '%s\n' "$c" >> "${CONFIG_DIR}/hub"
done

echo "Installed: ${BIN_DIR}/sah"
echo "Hub:      ${DOWNLOAD_HUB}"
printf 'Saved %s address(es) for automatic reconnection.\n' "$(echo "$ORDERED" | wc -w | tr -d ' ')"

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
