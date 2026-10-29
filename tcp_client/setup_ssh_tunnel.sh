#!/bin/bash
# ============================================================================
# Setup SSH key authentication for Web UI reverse tunnel
#
# Run this ONCE on each client device that needs Web UI mode.
# It generates an SSH key (if none exists) and copies it to the server
# so the reverse tunnel can connect without a password.
#
# Usage:
#   bash setup_ssh_tunnel.sh <server_ip>
#
# Example:
#   bash setup_ssh_tunnel.sh 139.162.31.224
# ============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "${CYAN}[STEP]${NC} $*"; }

SERVER="${1:-}"
if [ -z "$SERVER" ]; then
    error "Usage: bash setup_ssh_tunnel.sh <server_ip>"
fi

SSH_KEY="$HOME/.ssh/id_rsa"

echo ""
info "============================================"
info "  SSH Key Setup for Web UI Tunnel"
info "============================================"
info "Server: $SERVER"
echo ""

# ── Generate SSH key if none exists ──────────────────────────────────────────
if [ -f "$SSH_KEY" ]; then
    info "SSH key already exists: $SSH_KEY"
else
    step "Generating SSH key..."
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t rsa -b 4096 -N "" -f "$SSH_KEY" -q
    info "SSH key generated: $SSH_KEY"
fi

# ── Copy key to server ───────────────────────────────────────────────────────
step "Copying SSH key to $SERVER (you'll be prompted for the server password)..."
ssh-copy-id -i "$SSH_KEY.pub" "root@$SERVER" 2>/dev/null || {
    # Fallback if ssh-copy-id is not available
    warn "ssh-copy-id failed, trying manual copy..."
    cat "$SSH_KEY.pub" | ssh "root@$SERVER" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
}

# ── Test connection ──────────────────────────────────────────────────────────
step "Testing SSH connection..."
if ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$SERVER" "echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
    info "SSH key authentication works!"
else
    error "SSH key authentication test failed — check server SSH config"
fi

# ── Test reverse tunnel ──────────────────────────────────────────────────────
step "Testing reverse tunnel (port 19999)..."
ssh -N -o ExitOnForwardFailure=yes -R 19999:localhost:22 "root@$SERVER" &
TUNNEL_PID=$!
sleep 2

if kill -0 "$TUNNEL_PID" 2>/dev/null; then
    info "Reverse tunnel test OK — SSH tunneling is working!"
    kill "$TUNNEL_PID" 2>/dev/null
    wait "$TUNNEL_PID" 2>/dev/null || true
else
    warn "Reverse tunnel test may have failed — check server GatewayPorts setting"
fi

echo ""
info "============================================"
info "  Setup Complete!"
info "============================================"
echo ""
info "The client can now use SSH reverse tunnels for Web UI mode."
info "Make sure GatewayPorts is enabled on the server:"
info "  grep GatewayPorts /etc/ssh/sshd_config"
echo ""
