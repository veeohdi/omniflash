#!/usr/bin/env bash
# ==============================================================================
# OmniFlash Installer for macOS
# ==============================================================================

set -e

echo "====================================================="
echo "Installing OmniFlash (Pixel 4 XL Flasher for macOS)"
echo "====================================================="

if ! command -v python3 &> /dev/null; then
    echo "[!] Python 3 could not be found."
    echo "    Please install Python 3 or Homebrew first."
    exit 1
fi

echo "Installing Python dependencies..."
python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade >/dev/null 2>&1
python3 -m pip install flask --break-system-packages

if ! command -v fastboot &> /dev/null; then
    echo "[i] Fastboot not found in PATH."
    if command -v brew &> /dev/null; then
        echo "Installing Android platform-tools via Homebrew..."
        brew install --cask android-platform-tools || true
    fi
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$APP_DIR/run.sh" "$APP_DIR/wait_for_server.py"

echo ""
echo "====================================================="
echo "Installation Complete!"
echo "Run OmniFlash with: ./run.sh"
echo "====================================================="
