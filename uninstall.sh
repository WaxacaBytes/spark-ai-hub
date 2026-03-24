#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/sparkdeck"

echo ""
echo "  SparkDeck Uninstaller"
echo "  ====================="
echo ""

# ---------- stop and remove recipe containers ----------

if command -v docker &>/dev/null; then
    # Find all sparkdeck compose projects
    projects=$(docker ps -a --filter "name=sparkdeck-" --format "{{.Labels}}" 2>/dev/null | \
        grep -oP 'com\.docker\.compose\.project=\Ksparkdeck-[^ ,]+' | sort -u)

    if [ -n "$projects" ]; then
        echo "[sparkdeck] Stopping and removing recipe containers..."
        for project in $projects; do
            echo "  - $project"
            docker compose -p "$project" down --rmi all --volumes 2>/dev/null || true
        done
    fi

    # Catch any remaining sparkdeck containers
    remaining=$(docker ps -a --filter "name=sparkdeck-" --format "{{.ID}}" 2>/dev/null)
    if [ -n "$remaining" ]; then
        echo "[sparkdeck] Removing leftover containers..."
        docker rm -f $remaining 2>/dev/null || true
    fi

    # Remove dangling sparkdeck volumes
    volumes=$(docker volume ls --filter "name=sparkdeck" --format "{{.Name}}" 2>/dev/null)
    if [ -n "$volumes" ]; then
        echo "[sparkdeck] Removing volumes..."
        docker volume rm $volumes 2>/dev/null || true
    fi
fi

# ---------- remove sparkdeck directory ----------

if [ -d "$INSTALL_DIR" ]; then
    echo "[sparkdeck] Removing $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
else
    echo "[sparkdeck] $INSTALL_DIR not found, skipping."
fi

echo ""
echo "[sparkdeck] Uninstall complete."
echo ""
