#!/usr/bin/env bash
# configure_fail2ban.sh — Configure fail2ban SSH jail.
# Re-runnable: overwrites jail config and restarts fail2ban.
#
# Usage: sudo bash configure_fail2ban.sh [/path/to/system/.env]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
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

command -v fail2ban-server &>/dev/null \
    || error "fail2ban not found. Ensure AUTO_SETUP_INSTALL_SOFTWARE_ID includes 73 in dietpi.txt."

info "Configuring fail2ban (port ${SSH_PORT})…"

_tmp=$(mktemp)
cat > "$_tmp" <<EOF
[sshd]
enabled  = true
port     = ${SSH_PORT}
maxretry = 3
bantime  = 3600
findtime = 600
EOF
mv "$_tmp" /etc/fail2ban/jail.d/sshd-local.conf
chmod 644 /etc/fail2ban/jail.d/sshd-local.conf

systemctl enable fail2ban
systemctl restart fail2ban
info "fail2ban configured: 3 retries, 1-hour ban on port ${SSH_PORT}."
