#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# Core Configuration
SERVICE_NAME="mcp-fileserver"
SYSTEMD_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
APP_DIR="/opt/mcp_fileserver"

# Default Values
IP_ADDR="127.0.0.1"
PORT="8123"
WHEEL_FILE=""

# Visual Color Indicators
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Helper for showing script usage instructions
usage() {
    echo "Usage: sudo $0 [-w wheel_file] [-i ip_address] [-p port]"
    echo "  -w: Path to a specific .whl file (optional; auto-detects in 'dist/' if omitted)"
    echo "  -i: IP address to bind to (default: 127.0.0.1)"
    echo "  -p: Port number to listen on (default: 8123)"
    exit 1
}

# Parse command line flags using getopts
while getopts "w:i:p:h" opt; do
    case ${opt} in
        w ) WHEEL_FILE=$OPTARG ;;
        i ) IP_ADDR=$OPTARG ;;
        p ) PORT=$OPTARG ;;
        h ) usage ;;
        \? ) usage ;;
    esac
done

echo -e "${GREEN}=== Starting MCP File Server Service Installer ===${NC}"

# 1. Enforce root privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run this installer with sudo or as root.${NC}"
    exit 1
fi

# 2. Enforce Linux environment
if [ "$(uname)" != "Linux" ]; then
    echo -e "${RED}Error: Systemd services are only supported on Linux environments.${NC}"
    exit 1
fi

# 3. Capture the real human user who triggered sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_GROUP=$(id -gn "$REAL_USER")
echo "Configuring service to run under user context: ${REAL_USER}"

# 4. Determine which .whl file to use
if [ -n "$WHEEL_FILE" ]; then
    if [ ! -f "$WHEEL_FILE" ]; then
        echo -e "${RED}Error: Specified wheel file not found at: ${WHEEL_FILE}${NC}"
        exit 1
    fi
    echo "Using user-specified wheel file: ${WHEEL_FILE}"
else
    echo "No wheel file specified. Searching local 'dist/' directory..."
    WHEEL_FILE=$(find dist/ -name "*.whl" | head -n 1)

    if [ -z "$WHEEL_FILE" ]; then
        echo -e "${RED}Error: No built .whl file found in the 'dist/' directory.${NC}"
        echo "Please specify a file using the -w flag."
        exit 1
    fi
    echo "Found default wheel file: ${WHEEL_FILE}"
fi

echo "Target Network Settings -> ${IP_ADDR}:${PORT}"

# 5. Ask for the storage root directory path from the user (Strict Enforced, One Shot)
echo -e "\n--- Storage Directory Configuration ---"
echo "Please specify the root directory this server is authorized to access."
read -e -p "Enter absolute path: " USER_INPUT_PATH
# Clean up any accidental leading or trailing whitespace
MCP_FS_ROOT_PATH=$(echo "$USER_INPUT_PATH" | xargs)
# Check: Enforce that input is not blank
if [ -z "$MCP_FS_ROOT_PATH" ]; then
    echo -e "${RED}Error: Installation aborted. A root path must be explicitly provided.${NC}"
    exit 1
fi
# Check: Enforce that the directory must actively exist
if [ ! -d "$MCP_FS_ROOT_PATH" ]; then
    echo -e "${RED}Error: Installation aborted. The directory '${MCP_FS_ROOT_PATH}' does not exist.${NC}"
    exit 1
fi
echo -e "${GREEN}Using storage root directory: ${MCP_FS_ROOT_PATH}${NC}\n"


# 6. Create a clean system isolation directory
echo "Creating application directory at ${APP_DIR}..."
mkdir -p "${APP_DIR}"

echo "Setting up Python virtual environment..."
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install "${WHEEL_FILE}"

# Set runtime folder ownership to the real user
chown -R "${REAL_USER}:${REAL_GROUP}" "${APP_DIR}"


# 7. Generate the Systemd Service Configuration Unit File dynamically
echo "Generating systemd unit file at ${SYSTEMD_PATH}..."
cat <<EOF > "${SYSTEMD_PATH}"
[Unit]
Description=MCP File Server Background Daemon
After=network.target

[Service]
User=${REAL_USER}
Group=${REAL_GROUP}
WorkingDirectory=${APP_DIR}
# IP and Port are safely forwarded straight to your script as arguments
ExecStart=${APP_DIR}/.venv/bin/mcp-server --host ${IP_ADDR} --port ${PORT}
Environment=PYTHONUNBUFFERED=1
Environment=MCP_FS_ROOT_PATH=${MCP_FS_ROOT_PATH}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 8. Reload systemd, enable, and fire up the engine daemon background layers
echo "Registering daemon profiles and starting the background service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo -e "${GREEN}=== Installation Completed Successfully! ===${NC}"
echo -e "Your daemon is running safely in the background under user '${REAL_USER}'."
echo -e "-> View operational status:   ${GREEN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "-> Monitor stream print logs: ${GREEN}sudo journalctl -u ${SERVICE_NAME} -f${NC}"