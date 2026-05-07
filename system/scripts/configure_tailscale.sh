#!/usr/bin/env bash
# configure_tailscale.sh — Authenticate Tailscale and configure tailscale serve.
# Re-runnable: skips auth if already authenticated; serve config is idempotent.
#
# Usage: sudo bash configure_tailscale.sh [/path/to/system/.env] [/path/to/docker/.env]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ "$EUID" -ne 0 ]] && error "Run as root: sudo bash $0"

SYSTEM_ENV="${1:-/home/dietpi/pizero2-stack/system/.env}"
DOCKER_ENV="${2:-/home/dietpi/pizero2-stack/docker/.env}"
[[ -f "$SYSTEM_ENV" ]] || error "system/.env not found at '${SYSTEM_ENV}'. Pass path as first argument."
[[ -f "$DOCKER_ENV" ]] || error "docker/.env not found at '${DOCKER_ENV}'. Pass path as second argument."

_load_env() {
    local file="$1"
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
    done < "$file"
}
_load_env "$SYSTEM_ENV"
_load_env "$DOCKER_ENV"

GATUS_PORT="${GATUS_PORT:-8080}"
NTFY_PORT="${NTFY_PORT:-2586}"
TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"

command -v tailscale &>/dev/null \
    || error "Tailscale not found. Ensure AUTO_SETUP_INSTALL_SOFTWARE_ID includes 58 in dietpi.txt."

# Authenticate
if tailscale status 2>/dev/null | grep -q "^100\."; then
    info "Tailscale already authenticated — skipping tailscale up."
elif [[ -n "$TAILSCALE_AUTHKEY" ]]; then
    info "Authenticating Tailscale…"
    # Note: --authkey exposes the key in process listing (ps aux) for the
    # duration of this call. Unavoidable with the Tailscale CLI — rotate
    # the key at https://login.tailscale.com/admin/settings/keys after use.
    tailscale up --authkey="${TAILSCALE_AUTHKEY}"
    info "Tailscale authenticated. IP: $(tailscale ip 2>/dev/null || echo 'pending')"
else
    warn "TAILSCALE_AUTHKEY not set and Tailscale not authenticated."
    warn "Authenticate manually: tailscale up"
    warn "Then re-run this script to configure tailscale serve."
    exit 0
fi

# Configure serve
info "Configuring tailscale serve (Gatus=${GATUS_PORT}, Ntfy=${NTFY_PORT})…"
tailscale serve --bg --https="${GATUS_PORT}" localhost:"${GATUS_PORT}"
tailscale serve --bg --https="${NTFY_PORT}"  localhost:"${NTFY_PORT}"
info "Tailscale serve configured."
tailscale serve status
