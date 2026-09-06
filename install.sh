#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

# Core Configuration
SERVICE_NAME="mcp-fileserver"
SYSTEMD_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
APP_DIR="/opt/mcp_fileserver"
PKG_NAME="mcp_fs"
PRIVATE_FOLDER=".private"
START_SCRIPT="mcp-file-server"
DB_FILE_NAME="checksumdb.sqlite"

# Default Values
IP_ADDR="127.0.0.1"
PORT="8123"
WHEEL_FILE=""
FORCE_REINSTALL=false

# Visual Color Indicators
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color


PREV_DIR=""
if [ -f "$SYSTEMD_PATH" ]; then
    EXTRACTED_PATH=$(grep "Environment=MCP_FS_ROOT_DIR=" "$SYSTEMD_PATH" | sed 's/Environment=MCP_FS_ROOT_DIR=//')
    if [ -n "$EXTRACTED_PATH" ]; then
        PREV_DIR="$EXTRACTED_PATH"
    fi
fi

# Helper for showing script usage instructions
usage() {
    echo "Usage: sudo $0 [-w wheel_file] [-i ip_address] [-p port] [-f]"
    echo "  -w: Path to a specific .whl file (optional; auto-detects in 'dist/' if omitted)"
    echo "  -i: IP address to bind to (default: 127.0.0.1)"
    echo "  -p: Port number to listen on (default: 8123)"
    echo "  -f: Force reinstall of the application package"
    exit 1
}

# Parse command line flags using getopts
while getopts "w:i:p:fh" opt; do
    case ${opt} in
        w ) WHEEL_FILE=$OPTARG ;;
        i ) IP_ADDR=$OPTARG ;;
        p ) PORT=$OPTARG ;;
        f ) FORCE_REINSTALL=true ;;
        h ) usage ;;
        \? ) usage ;;
    esac
done

echo -e "${GREEN}=== Starting MCP File Server Service Installer ===${NC}"

# Enforce root privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run this installer with sudo or as root.${NC}"
    exit 1
fi

# Enforce Linux environment
if [ "$(uname)" != "Linux" ]; then
    echo -e "${RED}Error: Systemd services are only supported on Linux environments.${NC}"
    exit 1
fi

# Capture the real human user who triggered sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_GROUP=$(id -gn "$REAL_USER")
if [ -n "$SUDO_USER" ]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME=$(realpath "$HOME")
fi
echo "Configuring service to run under user context: ${REAL_USER}"

# Determine which .whl file to use
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

# Ask for the storage root directory path from the user (Strict Enforced, One Shot)
echo -e "\n--- Storage Directory Configuration ---"
echo "Please specify the root directory this server is authorized to access."
read -e -i "$PREV_DIR" -p "Enter absolute path: " USER_INPUT_PATH
# Clean up any accidental leading or trailing whitespace
MCP_FS_ROOT_DIR=$(echo "$USER_INPUT_PATH" | xargs)
# Check: Enforce that input is not blank
if [ -z "$MCP_FS_ROOT_DIR" ]; then
    echo -e "${RED}Error: Installation aborted. A root path must be explicitly provided.${NC}"
    exit 1
fi
# Check: Enforce that the directory must actively exist
if [ ! -d "$MCP_FS_ROOT_DIR" ]; then
    echo -e "${RED}Error: Installation aborted. The directory '${MCP_FS_ROOT_DIR}' does not exist.${NC}"
    exit 1
elif [[ ! "$MCP_FS_ROOT_DIR" == "$REAL_HOME"/* ]]; then
    echo -e "${RED}Error: The directory is outside the current user's space! ${NC}"
    exit 1
fi
echo -e "${GREEN}Using storage root directory: ${MCP_FS_ROOT_DIR}${NC}"

MCP_FS_PRIVATE_DIR="${MCP_FS_ROOT_DIR}/${PRIVATE_FOLDER}"
echo "Creating private storage directory at ${MCP_FS_PRIVATE_DIR}."
if mkdir -p "${MCP_FS_PRIVATE_DIR}"; then
    echo -e "${GREEN}Created private storage directory at ${MCP_FS_PRIVATE_DIR}.${NC}"
else
    echo -e "${RED}Error: Failed to create private storage directory ${MCP_FS_PRIVATE_DIR}.${NC}" >&2
    exit 1
fi
if [ -n "$SUDO_USER" ]; then
    chown -R "${REAL_USER}:${REAL_GROUP}" "${MCP_FS_PRIVATE_DIR}"
fi
MCP_FS_DB_FILE="${MCP_FS_PRIVATE_DIR}/${DB_FILE_NAME}"
echo -e "Checksum database will be stored at ${MCP_FS_DB_FILE}."

# Create a clean system isolation directory
echo "Creating application directory at ${APP_DIR}..."
mkdir -p "${APP_DIR}"

echo "Setting up Python virtual environment..."
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip

# Build the pip install command dynamically to support force reinstall flags
PIP_ARGS=("install")
if [ "$FORCE_REINSTALL" = true ]; then
    echo "⚠️ Force reinstall requested. Purging build caches..."
    PIP_ARGS+=("--force-reinstall" "--no-cache-dir")
fi
PIP_ARGS+=("${WHEEL_FILE}")

"${APP_DIR}/.venv/bin/pip" "${PIP_ARGS[@]}"

# Set runtime folder ownership to the real user
chown -R "${REAL_USER}:${REAL_GROUP}" "${APP_DIR}"


# Generate the Systemd Service Configuration Unit File dynamically
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
ExecStart=${APP_DIR}/.venv/bin/${START_SCRIPT} --host ${IP_ADDR} --port ${PORT}
Environment=PYTHONUNBUFFERED=1
Environment=MCP_FS_ROOT_DIR=${MCP_FS_ROOT_DIR}
Environment=MCP_FS_PRIVATE_DIR=${MCP_FS_PRIVATE_DIR}
Environment=MCP_FS_DB_FILE=${MCP_FS_DB_FILE}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Write env variables to a file for application level reference
SITE_PACKAGES_DIR=$("${APP_DIR}/.venv/bin/python" -c "import site; print(site.getsitepackages()[0])")
ENV_FILE="${SITE_PACKAGES_DIR}/${PKG_NAME}/.env"
echo "Writing environment variables to ${ENV_FILE}..."
cat <<EOF > "${ENV_FILE}"
# Environment Variables for MCP File Server
MCP_FS_ROOT_DIR=${MCP_FS_ROOT_DIR}
MCP_FS_PRIVATE_DIR=${MCP_FS_PRIVATE_DIR}
MCP_FS_DB_FILE=${MCP_FS_DB_FILE}
EOF

USER_BASHRC="${REAL_HOME}/.bashrc"
if [ -f "$USER_BASHRC" ]; then
    if ! grep -q "alias mcp-fs-maintain=" "$USER_BASHRC"; then
        echo "Adding local shortcut command to ${USER_BASHRC}..."
        echo "alias mcp-fs-maintain='${APP_DIR}/.venv/bin/mcp-file-server-maintain'" >> "$USER_BASHRC"
        chown "${REAL_USER}:${REAL_GROUP}" "$USER_BASHRC"
        source ~/.bashrc
    fi
fi

# Reload systemd, enable, and fire up the engine daemon background layers
echo "Registering daemon profiles and starting the background service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo -e "${GREEN}=== Installation Completed Successfully! ===${NC}"
echo -e "Your daemon is running safely in the background under user '${REAL_USER}'."
echo -e "-> View operational status:   ${GREEN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "-> Monitor stream print logs: ${GREEN}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "-> Alias command ready: ${GREEN}mcp-fs-maintain for maintenace${NC}"
