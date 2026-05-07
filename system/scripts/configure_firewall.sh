#!/usr/bin/env bash
# configure_firewall.sh — Apply UFW firewall rules.
# Re-runnable: resets all rules and re-applies from .env values.
#
# Usage: sudo bash configure_firewall.sh [/path/to/system/.env]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ "$EUID" -ne 0 ]] && error "Run as root: sudo bash $0"

ENV_FILE="${1:-/home/dietpi/pizero2-stack/system/.env}"
[[ -f "$ENV_FILE" ]] || error "system/.env not found at '${ENV_FILE}'. Pass path as first argument."

while IFS='=' read -r key val; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${key// }" ]] && continue
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    val="${val%%#*}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\'}" ; val="${val%\'}"
    val="${val#\"}" ; val="${val%\"}"
    export "${key}=${val}"
done < "$ENV_FILE"

[[ -z "${SSH_PORT:-}" ]] && error "SSH_PORT not set in ${ENV_FILE}."
[[ "${SSH_PORT}" =~ ^[0-9]+$ ]] && (( SSH_PORT >= 1024 && SSH_PORT <= 65535 )) \
    || error "SSH_PORT=${SSH_PORT} must be between 1024 and 65535."

if [[ -z "${LAN_SUBNET:-}" ]]; then
    _gw_iface=$(ip route show default 2>/dev/null | awk 'NR==1 {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')
    if [[ -n "$_gw_iface" ]]; then
        LAN_SUBNET=$(ip route show | awk -v iface="$_gw_iface" '/proto kernel/ && $0 ~ ("dev " iface) {print $1}' | head -1)
    else
        LAN_SUBNET=$(ip route show | awk '/proto kernel/ && !/^169\.254/ && !/^127\./ && !/^172\.1[6-9]\./ && !/^172\.[23][0-9]\./ && !/^172\.3[01]\./ {print $1}' | head -1)
    fi
    [[ -n "${LAN_SUBNET:-}" ]] \
        && warn "LAN_SUBNET not set — auto-detected as ${LAN_SUBNET}. Set LAN_SUBNET in .env to pin this." \
        || error "LAN_SUBNET not set and could not be auto-detected. Set it in .env (e.g. 192.168.1.0/24)."
fi

info "Configuring UFW (SSH_PORT=${SSH_PORT}, LAN=${LAN_SUBNET})…"
apt-get install -y -qq ufw

if ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "UFW is active — resetting all rules before re-applying."
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow in on lo
ufw allow out on lo
ufw allow from "${LAN_SUBNET}"  to any port "${SSH_PORT}" proto tcp comment "SSH from LAN"
ufw allow from 100.64.0.0/10   to any port "${SSH_PORT}" proto tcp comment "SSH from Tailscale"
ufw allow 41641/udp comment "Tailscale WireGuard"
ufw --force enable

info "UFW enabled. Current rules:"
ufw status verbose
