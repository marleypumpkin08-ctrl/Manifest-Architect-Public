#!/usr/bin/env bash
# updater.sh — swaps old app files with downloaded update and restarts
# Usage: updater.sh <INSTALL_DIR> <TEMP_DIR> <LAUNCH_SCRIPT>

set -euo pipefail

INSTALL_DIR="$1"
TEMP_DIR="$2"
LAUNCH_SCRIPT="$3"

# Give the parent process time to fully exit
sleep 1

echo "[updater] Copying new files to $INSTALL_DIR"
cp "$TEMP_DIR"/*.py "$INSTALL_DIR/" 2>/dev/null || true
cp "$TEMP_DIR"/launch.sh "$INSTALL_DIR/" 2>/dev/null || true
cp "$TEMP_DIR"/icon.svg "$INSTALL_DIR/" 2>/dev/null || true

chmod +x "$INSTALL_DIR"/*.py 2>/dev/null || true
chmod +x "$INSTALL_DIR"/launch.sh 2>/dev/null || true

echo "[updater] Cleaning up $TEMP_DIR"
rm -rf "$TEMP_DIR"

echo "[updater] Restarting $LAUNCH_SCRIPT"
exec "$LAUNCH_SCRIPT"
