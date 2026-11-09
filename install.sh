#!/bin/bash
#
# LightShowPi Neo - System Dependencies Installer
#
# This script installs ONLY the system-level dependencies required for LightShowPi Neo.
# For complete installation instructions, see README.md
#
# This script must be run as root (it will use sudo if needed)

INSTALL_DIR="$( cd $(dirname $0) ; pwd -P )"

# Set up file-based logging
exec 1> >(tee install.log)

# Root check
if [ "$EUID" -ne 0 ]; then
    echo 'Install script requires root privileges!'
    if [ -x /usr/bin/sudo ]; then
        echo 'Switching now, enter the password for "'$USER'", if prompted.'
        sudo su -c "$0 $*"
    else
        echo 'Switching now, enter the password for "root"!'
        su root -c "$0 $*"
    fi
    exit $?
fi

# Wrapper for informational logging
log() {
    echo -ne "\e[1;34mlightshowpi-neo \e[m" >&2
    echo -e "[`date`] $@"
}

# Checks the return value of the last command to run
verify() {
    if [ $? -ne 0 ]; then
        echo "Encountered a fatal error: $@"
        exit 1
    fi
}

log "LightShowPi Neo - System Dependencies Installer"
log "================================================"
echo ""

# Check for supported Raspberry Pi version (3, 4, or 5)
log "Checking for supported Raspberry Pi hardware..."
if [ -f /proc/cpuinfo ]; then
    # Try new format first (Model)
    MODEL=$(grep -E "^Model" /proc/cpuinfo)

    if echo "$MODEL" | grep -qi "Raspberry Pi 5"; then
        log "Detected Raspberry Pi 5 - supported!"
    elif echo "$MODEL" | grep -qi "Raspberry Pi 4"; then
        log "Detected Raspberry Pi 4 - supported!"
    elif echo "$MODEL" | grep -qiE "Raspberry Pi 3|Raspberry Pi Zero 2"; then
        log "Detected Raspberry Pi 3 - supported!"
    else
        # Try old format (Hardware)
        HARDWARE=$(grep -E "^Hardware" /proc/cpuinfo | awk '{print $3}')
        case $HARDWARE in
            BCM2711)
                log "Detected Raspberry Pi 4 (via Hardware field) - supported!"
                ;;
            BCM2712)
                log "Detected Raspberry Pi 5 (via Hardware field) - supported!"
                ;;
            BCM2837)
                log "Detected Raspberry Pi 3 (via Hardware field) - supported!"
                ;;
            BCM2835|BCM2836)
                echo "ERROR: This version of LightShowPi requires Raspberry Pi 3, 4, or 5"
                echo "Detected: Pi 1, 2, or Zero (not supported)"
                echo "Please upgrade to a Raspberry Pi 3 or newer"
                exit 1
                ;;
            *)
                echo "WARNING: Could not definitively detect Pi model"
                echo "Hardware: $HARDWARE, Model: $MODEL"
                echo "Proceeding anyway, but this may not work on unsupported hardware"
                ;;
        esac
    fi
else
    echo "WARNING: Could not detect hardware - /proc/cpuinfo not found"
    echo "Proceeding anyway, but this may not work on unsupported hardware"
fi

echo ""
log "Installing system dependencies..."
log "This will install: python3-dev, python3-lgpio, libasound2-dev, alsa-lib, git"
echo ""

# Update package list
apt-get update
verify "apt-get update failed"

# Install system dependencies
log "Installing python3-lgpio (GPIO library for Pi 3/4/5)..."
apt-get install -y python3-lgpio
verify "Installation of python3-lgpio failed"

log "Installing libasound2-dev (ALSA development files)..."
apt-get install -y libasound2-dev
verify "Installation of libasound2-dev failed"

# Install ALSA lib packages for conda compatibility
log "Installing ALSA library packages for conda compatibility..."
apt-get install -y libasound2 alsa-utils
verify "Installation of ALSA packages failed"

echo ""
log "System dependencies installed successfully!"
echo ""

# Check if running non-interactively
if [ ! -t 0 ]; then
    echo "Running in non-interactive mode - skipping optional setup steps"
    echo "Run ./install.sh interactively to complete full setup"
    exit 0
fi

# Ask if user wants automated setup
echo "========================================================================"
echo "  Optional: Automated Setup"
echo "========================================================================"
echo ""
echo "Would you like to automatically complete the remaining setup steps?"
echo "This will:"
echo "  - Create conda environment (if conda is installed)"
echo "  - Link lgpio library"
echo "  - Set SYNCHRONIZED_LIGHTS_HOME environment variable"
echo "  - Create initial configuration file"
echo ""
read -p "Continue with automated setup? (y/N) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "========================================================================"
    echo "  NEXT STEPS - Manual Installation"
    echo "========================================================================"
    echo ""
    echo "To complete the installation manually, see README.md or run:"
    echo ""
    echo "1. Install Miniconda (if not already installed):"
    echo "   wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh"
    echo "   bash Miniconda3-latest-Linux-aarch64.sh"
    echo ""
    echo "2. Create conda environment:"
    echo "   conda env create -f environment.yml"
    echo "   conda activate lightshowpi-neo"
    echo ""
    echo "3. Link system lgpio to conda environment:"
    echo "   ./bin/link_lgpio.sh"
    echo ""
    echo "4. Set environment variable (add to ~/.bashrc):"
    echo "   export SYNCHRONIZED_LIGHTS_HOME=$INSTALL_DIR"
    echo ""
    echo "5. Configure your setup:"
    echo "   cp config/defaults.cfg config/overrides.cfg"
    echo "   nano config/overrides.cfg"
    echo ""
    echo "========================================================================"
    exit 0
fi

# Automated setup starts here
echo ""
log "Starting automated setup..."

# Step 1: Check for conda
if command -v conda &> /dev/null; then
    log "✓ Found conda installation"

    # Check if environment already exists
    if conda env list | grep -q "lightshowpi-neo"; then
        log "⚠  Environment 'lightshowpi-neo' already exists - skipping creation"
    else
        log "Creating conda environment from environment.yml..."
        # Run as the original user (not root)
        sudo -u $SUDO_USER conda env create -f "$INSTALL_DIR/environment.yml"
        verify "Failed to create conda environment"
        log "✓ Conda environment created"
    fi
else
    echo ""
    echo "⚠  WARNING: conda not found - skipping environment creation"
    echo "   Install Miniconda first, then re-run this script or manually:"
    echo "     conda env create -f environment.yml"
    echo ""
fi

# Step 2: Set environment variable
log "Setting up SYNCHRONIZED_LIGHTS_HOME environment variable..."

# Determine shell config file
if [ -n "$SUDO_USER" ]; then
    USER_HOME=$(eval echo ~$SUDO_USER)
else
    USER_HOME=$HOME
fi

# Check which shell config to use
if [ -f "$USER_HOME/.bashrc" ]; then
    SHELL_RC="$USER_HOME/.bashrc"
elif [ -f "$USER_HOME/.zshrc" ]; then
    SHELL_RC="$USER_HOME/.zshrc"
else
    SHELL_RC="$USER_HOME/.bashrc"
fi

# Check if already set
if grep -q "SYNCHRONIZED_LIGHTS_HOME" "$SHELL_RC" 2>/dev/null; then
    log "⚠  SYNCHRONIZED_LIGHTS_HOME already set in $SHELL_RC - skipping"
else
    echo "" >> "$SHELL_RC"
    echo "# LightShowPi Neo" >> "$SHELL_RC"
    echo "export SYNCHRONIZED_LIGHTS_HOME=$INSTALL_DIR" >> "$SHELL_RC"
    log "✓ Added SYNCHRONIZED_LIGHTS_HOME to $SHELL_RC"
fi

# Set for current session
export SYNCHRONIZED_LIGHTS_HOME=$INSTALL_DIR

# Step 3: Create initial config if it doesn't exist
if [ ! -f "$INSTALL_DIR/config/overrides.cfg" ]; then
    log "Creating initial configuration file..."
    cp "$INSTALL_DIR/config/defaults.cfg" "$INSTALL_DIR/config/overrides.cfg"
    # Make it writable by the user
    if [ -n "$SUDO_USER" ]; then
        chown $SUDO_USER:$SUDO_USER "$INSTALL_DIR/config/overrides.cfg"
    fi
    log "✓ Created config/overrides.cfg from defaults"
    echo "   Edit this file to customize your setup"
else
    log "⚠  config/overrides.cfg already exists - skipping"
fi

# Step 4: Link lgpio (only if conda env was created)
if conda env list | grep -q "lightshowpi-neo" 2>/dev/null; then
    log "Linking system lgpio to conda environment..."
    echo ""
    echo "Note: You'll need to manually run link_lgpio.sh after activating the environment:"
    echo "  conda activate lightshowpi-neo"
    echo "  ./bin/link_lgpio.sh"
    echo ""
else
    log "⚠  Conda environment not available - skipping lgpio link"
    log "   Run ./bin/link_lgpio.sh manually after creating the environment"
fi

echo ""
log "========================================================================"
log "  Installation Complete!"
log "========================================================================"
echo ""
echo "Next steps:"
echo ""
if conda env list | grep -q "lightshowpi-neo" 2>/dev/null; then
    echo "1. Start a new shell or run: source $SHELL_RC"
    echo "2. Activate environment: conda activate lightshowpi-neo"
    echo "3. Link lgpio: ./bin/link_lgpio.sh"
    echo "4. Edit configuration: nano config/overrides.cfg"
    echo "5. Test: pytest -v"
else
    echo "1. Install Miniconda, then run: conda env create -f environment.yml"
    echo "2. Activate environment: conda activate lightshowpi-neo"
    echo "3. Link lgpio: ./bin/link_lgpio.sh"
    echo "4. Start a new shell or run: source $SHELL_RC"
    echo "5. Edit configuration: nano config/overrides.cfg"
    echo "6. Test: pytest -v"
fi
echo ""
echo "For API/Web UI setup, see RELEASE_NOTES.md"
echo ""
echo "========================================================================"
exit 0
