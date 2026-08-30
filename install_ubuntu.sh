#!/usr/bin/env bash
# ==============================================================================
# OmniFlash Installer for Ubuntu / Debian / Linux
# ==============================================================================

set -e

echo "====================================================="
echo "⚡ Installing OmniFlash (Pixel 4 XL Flasher)"
echo "====================================================="

if ! command -v python3 &> /dev/null; then
    echo "[!] Python 3 is not installed. Installing python3 and pip..."
    sudo apt update
    sudo apt install -y python3 python3-pip
fi

echo "Installing Python dependencies..."
python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade >/dev/null 2>&1
python3 -m pip install flask --break-system-packages

echo "Ensuring Fastboot & Android udev rules are installed..."
if ! command -v fastboot &> /dev/null || ! dpkg -s android-sdk-platform-tools-common >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y fastboot adb android-sdk-platform-tools-common || true
fi

# Ensure user is in plugdev group for fastboot USB access without sudo
if ! groups "$USER" | grep &>/dev/null '\bplugdev\b'; then
    sudo usermod -aG plugdev "$USER" || true
    echo "[i] Added $USER to plugdev group for USB access."
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_FILE="$HOME/.local/share/applications/omniflash.desktop"

echo "Creating Desktop Shortcut..."
mkdir -p "$HOME/.local/share/applications"
cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=OmniFlash (Pixel 4 XL)
Comment=Official Google Factory Image Flasher for Pixel 4 XL (coral)
Exec=bash -c "cd '$APP_DIR' && ./run.sh"
Icon=$APP_DIR/icon.svg
Terminal=true
Categories=Development;Utility;
EOF

chmod +x "$DESKTOP_FILE"
chmod +x "$APP_DIR/run.sh" "$APP_DIR/wait_for_server.py"

echo ""
echo "====================================================="
echo "✓ Installation Complete!"
echo "You can launch OmniFlash from your application menu or by running:"
echo "  cd $APP_DIR && ./run.sh"
echo "====================================================="
