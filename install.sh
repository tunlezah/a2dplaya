#!/bin/bash
# A2DPlaya Installer
# Installs system dependencies and Python packages for A2DPlaya
# Supports Debian/Ubuntu (apt) and Fedora/RHEL (dnf/yum)

set -e

echo "============================================"
echo "  A2DPlaya Installer"
echo "  Bluetooth A2DP to Chromecast/AirPlay Bridge"
echo "============================================"
echo ""

# Detect package manager
if command -v apt-get &> /dev/null; then
    PKG_MGR="apt"
elif command -v dnf &> /dev/null; then
    PKG_MGR="dnf"
elif command -v yum &> /dev/null; then
    PKG_MGR="yum"
else
    echo "Warning: Unsupported package manager. Install dependencies manually."
    PKG_MGR="none"
fi

# Install system dependencies
install_system_deps() {
    echo "[1/4] Installing system dependencies..."

    if [ "$PKG_MGR" = "apt" ]; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq \
            python3 python3-pip python3-venv \
            bluetooth bluez bluez-tools \
            pulseaudio-module-bluetooth \
            alsa-utils \
            python3-dbus python3-gi \
            avahi-utils \
            libglib2.0-dev
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
        sudo $PKG_MGR install -y \
            python3 python3-pip \
            bluez bluez-tools \
            pulseaudio-module-bluetooth \
            alsa-utils alsa-lib-devel \
            python3-dbus \
            avahi-tools
    fi

    echo "  System dependencies installed."
}

# Create Python virtual environment
setup_venv() {
    echo "[2/4] Setting up Python environment..."

    INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
    VENV_DIR="$INSTALL_DIR/venv"

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi

    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip -q
    pip install -e "$INSTALL_DIR" -q
    pip install -r "$INSTALL_DIR/requirements.txt" -q

    echo "  Python environment ready."
}

# Configure Bluetooth
setup_bluetooth() {
    echo "[3/4] Configuring Bluetooth..."

    # Enable and start Bluetooth service
    if command -v systemctl &> /dev/null; then
        sudo systemctl enable bluetooth 2>/dev/null || true
        sudo systemctl start bluetooth 2>/dev/null || true
    fi

    # Add user to bluetooth group
    if getent group bluetooth > /dev/null 2>&1; then
        sudo usermod -aG bluetooth "$USER" 2>/dev/null || true
    fi

    # Configure BlueZ for A2DP sink (if config file exists)
    BLUEZ_MAIN="/etc/bluetooth/main.conf"
    if [ -f "$BLUEZ_MAIN" ]; then
        # Ensure Class is set for audio sink
        if ! grep -q "^Class" "$BLUEZ_MAIN"; then
            echo "" | sudo tee -a "$BLUEZ_MAIN" > /dev/null
            echo "# A2DPlaya: Set device class to Audio Sink" | sudo tee -a "$BLUEZ_MAIN" > /dev/null
            echo "Class = 0x200414" | sudo tee -a "$BLUEZ_MAIN" > /dev/null
        fi

        # Enable auto-pairing
        if ! grep -q "^AutoEnable" "$BLUEZ_MAIN"; then
            echo "AutoEnable=true" | sudo tee -a "$BLUEZ_MAIN" > /dev/null
        fi
    fi

    echo "  Bluetooth configured."
}

# Create systemd service (optional)
setup_service() {
    echo "[4/4] Setting up systemd service..."

    INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
    SERVICE_FILE="/etc/systemd/system/a2dplaya.service"

    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=A2DPlaya - Bluetooth Audio Bridge
After=bluetooth.target network.target sound.target
Wants=bluetooth.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python -m a2dplaya
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable a2dplaya 2>/dev/null || true

    echo "  Systemd service created."
    echo "  Start with: sudo systemctl start a2dplaya"
}

# Main
echo "This installer will:"
echo "  - Install system packages (bluetooth, audio, python)"
echo "  - Set up a Python virtual environment"
echo "  - Configure Bluetooth for A2DP sink mode"
echo "  - Create a systemd service (optional)"
echo ""
read -p "Continue? [Y/n] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo "Installation cancelled."
    exit 0
fi

install_system_deps
setup_venv
setup_bluetooth

read -p "Install as systemd service? [Y/n] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    setup_service
fi

echo ""
echo "============================================"
echo "  Installation complete!"
echo ""
echo "  Start A2DPlaya:"
echo "    source venv/bin/activate"
echo "    python -m a2dplaya"
echo ""
echo "  Or as a service:"
echo "    sudo systemctl start a2dplaya"
echo ""
echo "  Web UI: http://$(hostname -I | awk '{print $1}'):8080"
echo "============================================"
