#!/usr/bin/env bash
# configure_ssh.sh — Harden sshd_config (custom port, key-only auth, session limits).
# Re-runnable: idempotent sed replacements, validates config before reloading sshd.
#
# Usage: sudo bash configure_ssh.sh [/path/to/system/.env]
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

DIETPI_USER="${DIETPI_USER:-dietpi}"

# Safety check — must have a valid key before enforcing key-only auth
AUTH_KEYS="/home/${DIETPI_USER}/.ssh/authorized_keys"
[[ -s "$AUTH_KEYS" ]] \
    || error "No authorized_keys at ${AUTH_KEYS}. Add your SSH public key first to avoid lockout."

# AUTO_SETUP_SSH_SERVER_INDEX=-2 in dietpi.txt ensures OpenSSH is pre-installed.
# This fallback handles the semi-automated path where the template was not used.
if [[ ! -f /etc/ssh/sshd_config ]]; then
    info "openssh-server not found — installing…"
    apt-get install -y -qq openssh-server
fi

SSHD_CONFIG="/etc/ssh/sshd_config"
cp -n "${SSHD_CONFIG}" "${SSHD_CONFIG}.bak"

apply_ssh() {
    local key="$1" val="$2"
    local escaped_val
    escaped_val=$(printf '%s' "$val" | sed 's/[&\]/\\&/g')
    if grep -qE "^#?[[:space:]]*${key}[[:space:]]" "${SSHD_CONFIG}"; then
        sed -i "s|^#*[[:space:]]*${key}[[:space:]].*|${key} ${escaped_val}|" "${SSHD_CONFIG}"
    else
        printf '%s %s\n' "$key" "$val" >> "${SSHD_CONFIG}"
    fi
}

apply_ssh "Port"                            "${SSH_PORT}"
apply_ssh "PasswordAuthentication"          "no"
apply_ssh "PermitRootLogin"                 "no"
apply_ssh "PubkeyAuthentication"            "yes"
apply_ssh "ChallengeResponseAuthentication" "no"
apply_ssh "KbdInteractiveAuthentication"    "no"
apply_ssh "X11Forwarding"                   "no"
apply_ssh "MaxAuthTries"                    "3"
apply_ssh "LoginGraceTime"                  "30"
apply_ssh "ClientAliveInterval"             "300"
apply_ssh "ClientAliveCountMax"             "2"

sed -i '/^AllowUsers/d' "${SSHD_CONFIG}"
printf 'AllowUsers %s\n' "$DIETPI_USER" >> "${SSHD_CONFIG}"

mkdir -p /run/sshd
sshd -t || error "SSH config validation failed — check ${SSHD_CONFIG} before retrying."

if systemctl is-active --quiet ssh 2>/dev/null; then
    systemctl reload ssh
else
    systemctl enable --now ssh
fi

if systemctl is-active --quiet dropbear 2>/dev/null; then
    systemctl stop dropbear
    systemctl disable dropbear
    info "dropbear disabled — openssh is now active."
fi

info "SSH hardened on port ${SSH_PORT}."
