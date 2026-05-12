#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="YOUR_GITHUB_USERNAME"
REPO_NAME="Manifest-Architect-Public"
BINARY="ManifestStudio"
VERSION="1.0.0"

INSTALL_DIR="/usr/local/bin"
APP_LIST="/usr/local/share/applications"
ICON_DIR="/usr/local/share/icons/hicolor/scalable/apps"

# ---------- command dispatch ----------
case "${1:-}" in
    uninstall|--uninstall)
        echo "==> Uninstalling Manifest Studio..."
        sudo rm -f "$INSTALL_DIR/manifest-studio"
        sudo rm -f "$APP_LIST/manifest-studio.desktop"
        sudo rm -f "$ICON_DIR/manifest-studio.svg"
        sudo gtk-update-icon-cache -f /usr/local/share/icons/hicolor/ 2>/dev/null || true
        update-desktop-database "$APP_LIST" 2>/dev/null || true
        echo "Done. Manifest Studio has been removed."
        exit 0
        ;;
    update|--update)
        echo "==> Updating Manifest Studio..."
        exec "$0"  # re-run full install (re-downloads + overwrites)
        ;;
    version|--version|-v)
        echo "$VERSION"
        exit 0
        ;;
    help|--help|-h)
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  (no args)    Install Manifest Studio globally"
        echo "  uninstall    Remove Manifest Studio"
        echo "  update       Re-download latest release from GitHub"
        echo "  version      Show version"
        echo "  help         Show this message"
        exit 0
        ;;
esac

# ---------- distro detection ----------
if [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ] || grep -qi 'fedora\|nobara' /etc/os-release 2>/dev/null; then
    echo "[Detected] Fedora-based (Nobara / Fedora)"
    DEP_CMD="sudo dnf install -y"
    DEPS=(python3-gobject gtk4 libadwaita)
elif [ -f /etc/debian_version ]; then
    echo "[Detected] Debian-based (Ubuntu / Pop!_OS / Mint)"
    DEP_CMD="sudo apt install -y"
    DEPS=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1)
elif grep -qi 'opensuse' /etc/os-release 2>/dev/null; then
    echo "[Detected] openSUSE"
    DEP_CMD="sudo zypper install -y"
    DEPS=(python3-gobject python3-gi-gtk4 libadwaita)
elif [ -f /etc/arch-release ]; then
    echo "[Detected] Arch Linux"
    DEP_CMD="sudo pacman -S --noconfirm"
    DEPS=(python-gobject gtk4 libadwaita)
else
    echo "[!] Unsupported distribution."
    echo "    Please install the following manually:"
    echo "    - python3-gobject (PyGObject)"
    echo "    - gtk4"
    echo "    - libadwaita (libadwaita-1)"
    exit 1
fi

# ---------- install dependencies ----------
echo ""
echo "==> Installing dependencies..."
$DEP_CMD "${DEPS[@]}"

# ---------- download binary ----------
echo ""
echo "==> Downloading latest binary..."
DOWNLOAD_URL="https://github.com/$REPO_OWNER/$REPO_NAME/releases/download/v$VERSION/$BINARY"
TMP_BIN="/tmp/$BINARY"

if command -v curl &>/dev/null; then
    curl -Lfo "$TMP_BIN" "$DOWNLOAD_URL"
elif command -v wget &>/dev/null; then
    wget -O "$TMP_BIN" "$DOWNLOAD_URL"
else
    echo "[!] Need curl or wget to download."
    exit 1
fi

if [ ! -s "$TMP_BIN" ]; then
    echo "[!] Download failed or file is empty."
    echo "    Check the release URL: $DOWNLOAD_URL"
    exit 1
fi

# ---------- install binary ----------
echo ""
echo "==> Installing to $INSTALL_DIR..."
sudo mv "$TMP_BIN" "$INSTALL_DIR/manifest-studio"
sudo chmod +x "$INSTALL_DIR/manifest-studio"
echo "    Installed: $INSTALL_DIR/manifest-studio"

# ---------- desktop entry ----------
echo ""
echo "==> Creating desktop entry..."
ICON_PATH="$ICON_DIR/manifest-studio.svg"

# generate SVG icon
sudo mkdir -p "$ICON_DIR"
sudo mkdir -p "$APP_LIST"

cat > /tmp/manifest-studio-icon.svg << 'SVGEOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#3584e4"/>
      <stop offset="100%" stop-color="#1a5fb4"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="28" fill="url(#bg)"/>
  <rect x="20" y="32" width="88" height="72" rx="8" fill="#f6f5f4" stroke="#deddda" stroke-width="2"/>
  <rect x="32" y="48" width="64" height="6" rx="3" fill="#9a9996"/>
  <rect x="32" y="62" width="48" height="6" rx="3" fill="#9a9996"/>
  <rect x="32" y="76" width="56" height="6" rx="3" fill="#9a9996"/>
  <circle cx="92" cy="88" r="18" fill="#2ec27e" stroke="#fff" stroke-width="3"/>
  <path d="M85 89l5 4 8-8" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
SVGEOF

sudo mv /tmp/manifest-studio-icon.svg "$ICON_PATH"
sudo gtk-update-icon-cache -f /usr/local/share/icons/hicolor/ 2>/dev/null || true

# write .desktop file
sudo tee "$APP_LIST/manifest-studio.desktop" > /dev/null << DESKTOP_EOF
[Desktop Entry]
Version=1.0
Name=Manifest Studio
Comment=Steam manifest management tool
Exec=$INSTALL_DIR/manifest-studio
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
DESKTOP_EOF

# ---------- finalize ----------
echo ""
echo "==> Updating desktop database..."
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_LIST" 2>/dev/null || true
fi

echo ""
echo "======================================"
echo "  Manifest Studio $VERSION installed!"
echo "======================================"
echo ""
echo "  Run:   manifest-studio"
echo "  Or find 'Manifest Studio' in your app launcher."
echo ""
