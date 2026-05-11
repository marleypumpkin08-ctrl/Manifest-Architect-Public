#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Manifest Studio"
DIR_NAME="ManifestArchitect"
SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/.local/share/$DIR_NAME"
APP_LIST="$HOME/.local/share/applications"
ICON="$TARGET/icon.svg"
DESKTOP_FILE="$APP_LIST/manifest-studio.desktop"

# ---------- helpers ----------
do_install() {
    echo "==> Creating $TARGET..."
    mkdir -p "$TARGET"
    mkdir -p "$TARGET/log"
    cp "$SRC/manifest_studio.py" "$TARGET/"
    cp "$SRC/steam_injector.py" "$TARGET/"
    cp "$SRC/update_engine.py" "$TARGET/"
    cp "$SRC/game_database.py" "$TARGET/"

    echo "==> Creating launcher wrapper..."
    cat > "$TARGET/launch.sh" << WRAPPER_EOF
#!/usr/bin/env bash
cd "$TARGET" || exit 1
LOG="$TARGET/log/launch.log"
echo "[LOG] Started at \$(date)" > "\$LOG"
python3 manifest_studio.py >> "\$LOG" 2>&1
EXIT_CODE=\$?
echo "[LOG] Exited with code \$EXIT_CODE at \$(date)" >> "\$LOG"
exit \$EXIT_CODE
WRAPPER_EOF
    chmod +x "$TARGET/launch.sh"

    echo "==> Generating app icon..."
    cat > "$ICON" << 'SVGEOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#3584e4"/>
      <stop offset="100%" stop-color="#1a5fb4"/>
    </linearGradient>
    <linearGradient id="box" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f6f5f4"/>
      <stop offset="100%" stop-color="#deddda"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="28" fill="url(#bg)"/>
  <rect x="20" y="32" width="88" height="72" rx="8" fill="url(#box)" stroke="#b0afad" stroke-width="2"/>
  <rect x="32" y="48" width="64" height="6" rx="3" fill="#9a9996"/>
  <rect x="32" y="62" width="48" height="6" rx="3" fill="#9a9996"/>
  <rect x="32" y="76" width="56" height="6" rx="3" fill="#9a9996"/>
  <circle cx="92" cy="88" r="18" fill="#2ec27e" stroke="#fff" stroke-width="3"/>
  <path d="M85 89l5 4 8-8" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
SVGEOF

    echo "==> Writing .desktop file..."
    mkdir -p "$APP_LIST"
    cat > "$DESKTOP_FILE" << DESKTOP_EOF
[Desktop Entry]
Version=1.0
Name=Manifest Studio
Comment=Steam manifest management tool
Exec=$TARGET/launch.sh
Icon=$ICON
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
DESKTOP_EOF

    chmod +x "$TARGET/manifest_studio.py"
    chmod +x "$TARGET/steam_injector.py"

    update-desktop-database "$APP_LIST" 2>/dev/null || true

    echo ""
    echo "Done. $APP_NAME installed."
    echo "Launch it from your app drawer or run:"
    echo "  $TARGET/launch.sh"
    echo ""
    echo "If the app closes immediately, check the log:"
    echo "  cat $TARGET/log/launch.log"
}

# ---------- command dispatch ----------
case "${1:-}" in
    uninstall|--uninstall)
        echo "==> Uninstalling $APP_NAME..."
        rm -rf "$TARGET"
        rm -f "$DESKTOP_FILE"
        update-desktop-database "$APP_LIST" 2>/dev/null || true
        echo "Done. $APP_NAME has been removed."
        exit 0
        ;;
    update|--update)
        echo "==> Updating $APP_NAME..."
        if [ ! -d "$TARGET" ]; then
            echo "Error: $APP_NAME is not installed. Run without arguments to install first."
            exit 1
        fi
        do_install
        exit 0
        ;;
    version|--version|-v)
        if [ -f "$TARGET/update_engine.py" ]; then
            python3 -c "import sys; sys.path.insert(0, '$TARGET'); from update_engine import CURRENT_VERSION; print(CURRENT_VERSION)"
        else
            echo "Not installed"
        fi
        exit 0
        ;;
    help|--help|-h)
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  (no args)    Install $APP_NAME"
        echo "  uninstall    Remove $APP_NAME"
        echo "  update       Re-copy latest files from project directory"
        echo "  version      Show installed version"
        echo "  help         Show this message"
        exit 0
        ;;
esac

echo "==> Installing dependencies..."
if [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ] || grep -qi 'fedora\|nobara' /etc/os-release 2>/dev/null; then
    sudo dnf install -y python3-gobject gtk4 libadwaita
else
    echo "Warning: not a Fedora/Nobara system. Attempting dnf anyway..."
    sudo dnf install -y python3-gobject gtk4 libadwaita || true
fi

do_install
