# pizero2-stack

A reproducible, Git-managed setup for a **Raspberry Pi Zero 2 W** running **DietPi**, configured as a minimal remote node on a **Tailscale** mesh. Services run in Docker containers and are exposed exclusively via `tailscale serve` (HTTPS, no open firewall ports for web UIs) or guarded by UFW (SSH).

## Services

| Service | What | Access |
| ------- | ---- | ------ |
| [Gatus](https://github.com/TwinProduction/gatus) — uptime monitor | `GATUS_PORT` (8080) | Tailscale via `serve` |
| [Ntfy](https://ntfy.sh) — push notifications | `NTFY_PORT` (2586) | Tailscale via `serve` |
| SSH | `SSH_PORT` | LAN + Tailscale |

Gatus monitors services on other tailnet devices (e.g. an RPi5). Ntfy delivers push alerts from Gatus and other scripts.

---

## Repository Layout

```text
pizero2-stack/
├── system/
│   ├── firstboot/
│   │   ├── base/
│   │   │   ├── dietpi.txt.base                      # Full official DietPi config (tracked)
│   │   │   └── Automation_Custom_Script.sh.base     # First-boot script template ({{REPO_URL}} placeholder)
│   │   ├── firstboot.conf.example                   # User config template (fill and gitignore)
│   │   ├── prepare_firstboot.py                     # Validate + render out/ from firstboot.conf
│   │   └── out/                                     # Git-ignored — copy these to SD card
│   │       ├── dietpi.txt
│   │       ├── Automation_Custom_Script.sh    (copy all 4 files to SD card)
│   │       ├── system_env
│   │       └── docker_env
│   ├── scripts/
│   │   ├── configure_firewall.sh                    # UFW rules (re-runnable, reads system/.env)
│   │   ├── configure_ssh.sh                         # SSH hardening (re-runnable, reads system/.env)
│   │   ├── configure_fail2ban.sh                    # fail2ban jail (re-runnable, reads system/.env)
│   │   └── configure_tailscale.sh                   # Tailscale auth + serve (reads system/.env + docker/.env)
│   └── .env.example                                 # Host env template (gitignored when filled)
├── docker/
│   ├── docker-compose.yml                           # Service definitions (Gatus, Ntfy)
│   ├── .env.example                                 # Docker env template (gitignored when filled)
│   └── gatus.yaml.example                           # Gatus starter config with Ntfy alerting
├── .gitignore
└── README.md
```

> **Never commit:** `system/firstboot/firstboot.conf`, `system/firstboot/out/`, `system/.env`, `docker/.env` — all gitignored. They contain passwords and credentials.

---

## Setup Workflow

```text
dev machine                                    SD card                   Pi Zero 2
─────────────────────────────────────────────  ─────────────────         ──────────────────────────
1. edit system/firstboot/firstboot.conf        ← out/ copied here  →     boots, runs script,
2. python3 system/firstboot/prepare_firstboot.py                          provisions itself,
   → validates config                                                      starts stack
   → writes system/firstboot/out/                                          (no SSH needed)
```

### Step 1 — Flash DietPi

Flash the [DietPi image for Raspberry Pi 2/3/4/Zero 2](https://dietpi.com/#download) to a microSD card (use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) or `dd`). Do not configure anything yet.

### Step 2 — Fill in your config

```bash
cp system/firstboot/firstboot.conf.example system/firstboot/firstboot.conf
nano system/firstboot/firstboot.conf   # fill in every CHANGE_ME / REPLACE_WITH
```

Required values:

| Key | What |
| --- | ---- |
| `HOSTNAME` | Pi hostname (e.g. `pi-node`) |
| `TZ` | Timezone (e.g. `Europe/Rome`) |
| `GLOBAL_PASSWORD` | Fallback console password (≥12 chars) |
| `SSH_PUBKEY` | Your workstation's public key (`cat ~/.ssh/id_ed25519.pub`) |
| `REPO_URL` | Your fork URL |
| `SSH_PORT` | Non-standard SSH port (1024–65535) |
| `TAILSCALE_AUTHKEY` | From [Tailscale admin](https://login.tailscale.com/admin/settings/keys) — enables full automation |
| `NTFY_ADMIN_PASSWORD` | Password for the `ntfyadmin` account (app login) |
| `NTFY_PUBLISH_PASSWORD` | Password for the `publisher` account used by scripts (≥16 chars, `openssl rand -hex 24`) |

### Step 3 — Generate and validate boot files

```bash
python3 system/firstboot/prepare_firstboot.py
```

This validates all config values and writes files to `system/firstboot/out/`:

- `dietpi.txt` — official DietPi config with your values patched in
- `Automation_Custom_Script.sh` — provisioning script (copies env files into place)
- `system_env` — host config (SSH port, Tailscale authkey, etc.)
- `docker_env` — Docker config (ports, Ntfy credential hashes, etc.)

> Requires the Python `bcrypt` package: `pip3 install bcrypt`

Fix any errors it reports, then continue.

### Step 4 — Copy to SD card

Mount the SD card's FAT32 boot partition, then:

```bash
cp system/firstboot/out/dietpi.txt                    /Volumes/boot/dietpi.txt   # overwrite existing
cp system/firstboot/out/Automation_Custom_Script.sh   /Volumes/boot/
cp system/firstboot/out/system_env                    /Volumes/boot/
cp system/firstboot/out/docker_env                    /Volumes/boot/
```

Adjust the mount path for your OS (`/Volumes/boot` on macOS, `/media/$USER/boot` on Linux).

### Step 5 — Boot

Unmount, insert into Pi, power on. Wait ~10–15 minutes.

The Pi will: install all software, clone this repo, run the provisioning scripts, authenticate Tailscale, start the Docker stack.

At the end of the log (`/var/tmp/dietpi/logs/pizero2-provision.log`) you'll see the service URLs.

---

## Configure Gatus

`Automation_Custom_Script.sh` copies `docker/gatus.yaml.example` to `${CONFIG_ROOT}/gatus/config.yaml` if no config exists yet. Edit it before or after first start:

```bash
ssh -p <SSH_PORT> dietpi@<tailscale-ip>
nano ~/docker/config/gatus/config.yaml
docker compose -f ~/pizero2-stack/docker/docker-compose.yml restart gatus
```

Fill in your Tailscale hostname, Ntfy port, and `NTFY_PUBLISH_PASSWORD`. Add endpoints for the services you want to monitor.

**Container networking note:** to monitor Ntfy from Gatus (same Pi), use `http://ntfy:80` — Docker resolves the container name on the shared bridge network. Use Tailscale HTTPS URLs for services on other machines (e.g. RPi5).

---

## First-run Configuration

**Ntfy**: install the [ntfy app](https://ntfy.sh/#download) on your phone, add server `https://<pi-hostname>:<NTFY_PORT>`, log in with username `ntfyadmin` and your `NTFY_ADMIN_PASSWORD` from `firstboot.conf`.

Set `NTFY_BASE_URL` in `docker/.env` once you know your Tailscale hostname — this enables click-through links in notifications:

```bash
# On the Pi:
nano ~/pizero2-stack/docker/.env
# Add: NTFY_BASE_URL=https://<pi-hostname>.<tailnet>.ts.net:2586
docker compose -f ~/pizero2-stack/docker/docker-compose.yml up -d ntfy
```

**Gatus**: open `https://<pi-hostname>:<GATUS_PORT>` — dashboard is read-only, no login required.

> Tailscale hostnames: `<machine-name>.<tailnet-name>.ts.net`. Run `tailscale status` to find yours.

### Sending notifications from scripts

ntfy creates a `publisher` user at startup (admin role) whose password is `NTFY_PUBLISH_PASSWORD` from `firstboot.conf`.

**From the Pi Zero 2:**

```bash
curl -u "publisher:${NTFY_PUBLISH_PASSWORD}" \
     -d "your message" \
     "http://localhost:${NTFY_PORT}/your-topic"
```

**From other tailnet devices (e.g. RPi5):**

```bash
curl -u "publisher:${NTFY_PUBLISH_PASSWORD}" \
     -d "your message" \
     "https://<pi-hostname>:${NTFY_PORT}/your-topic"
```

---

## Re-running Individual Scripts

All provisioning scripts are standalone and idempotent — safe to re-run after the initial setup:

```bash
# Re-apply UFW rules (e.g. after changing SSH_PORT)
sudo bash system/scripts/configure_firewall.sh

# Re-harden SSH (e.g. after regenerating sshd_config)
sudo bash system/scripts/configure_ssh.sh

# Re-apply fail2ban jail config
sudo bash system/scripts/configure_fail2ban.sh

# Re-authenticate Tailscale or reconfigure serve (e.g. after adding a new service)
sudo bash system/scripts/configure_tailscale.sh
```

By default each script reads from its standard location:

- `configure_firewall.sh`, `configure_ssh.sh`, `configure_fail2ban.sh` → `system/.env`
- `configure_tailscale.sh` → `system/.env` + `docker/.env`

Pass a different path as the first argument if needed.

---

## Day-to-Day Operations

```bash
# Start / stop stack
docker compose up -d
docker compose down

# Restart individual service
docker compose restart gatus
docker compose restart ntfy

# View logs
docker compose logs -f gatus
docker compose logs -f ntfy

# Update all images
docker compose pull && docker compose up -d && docker image prune -f

# Resource usage
docker stats
```

Run these from `~/pizero2-stack/docker/`.

---

## Re-provisioning from Scratch

1. Edit `system/firstboot/firstboot.conf` (restore from backup or refill from example).
2. `python3 system/firstboot/prepare_firstboot.py`
3. Copy `system/firstboot/out/` to a freshly-flashed SD card.
4. Power on — done.

---

## Firewall Reference

UFW rules applied by `configure_firewall.sh`:

| Port | Protocol | Source | Purpose |
| ---- | -------- | ------ | ------- |
| `SSH_PORT` | TCP | LAN subnet | SSH fallback |
| `SSH_PORT` | TCP | 100.64.0.0/10 | SSH over Tailscale |
| 41641 | UDP | any | Tailscale WireGuard |

Web UI ports are **not** in UFW — they bind to `127.0.0.1` and are accessed exclusively via `tailscale serve`.

---

## Hardware Notes

- **RAM:** 512 MB. Stack (Gatus + Ntfy) uses ~30–50 MB idle.
- **SD card:** DietPi logs on `tmpfs` by default. Service data writes to `CONFIG_ROOT`. Use a high-endurance microSD (Samsung Endurance, SanDisk Max Endurance).

---

## Security Notes

- **SSH**: key-only, `PermitRootLogin no`, `MaxAuthTries 3`. fail2ban bans after 3 failures (1-hour ban).
- **Web UIs**: bind to `127.0.0.1`, accessible only via `tailscale serve` (Tailscale-issued HTTPS certs). Not reachable from LAN.
- **Ntfy**: `deny-all` access by default — all users must be created explicitly.
- **Tailscale ACL**: restrict which tailnet nodes can reach the Pi for additional isolation.
- **`system/firstboot/firstboot.conf`**: gitignored. Back it up securely (password manager, encrypted storage).
- **Tailscale authkey**: rotate at [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys) after first boot.
