#!/usr/bin/env python3
"""
prepare_firstboot.py — Validate firstboot.conf and generate SD card boot files.

Reads:
  system/firstboot/firstboot.conf      (gitignored — fill from firstboot.conf.example)

Generates in system/firstboot/out/ (gitignored — copy all four to the SD card boot partition):
  out/dietpi.txt                       (official base + your values patched in)
  out/Automation_Custom_Script.sh      (REPO_URL substituted)
  out/system_env                       (host config: SSH, Tailscale, network)
  out/docker_env                       (docker config: ports, volumes, Ntfy credentials)

Run from anywhere inside the repo:
  python3 system/firstboot/prepare_firstboot.py
"""

import os
import re
import stat
import sys
from pathlib import Path

try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None

# ── ANSI colours ───────────────────────────────────────────────────────────────
_use_colour = sys.stdout.isatty() and (sys.platform != "win32" or os.environ.get("WT_SESSION"))
def _c(code: str) -> str:
    return code if _use_colour else ""

RED    = _c("\033[0;31m")
GREEN  = _c("\033[0;32m")
YELLOW = _c("\033[1;33m")
BOLD   = _c("\033[1m")
NC     = _c("\033[0m")

_errors:   list[str] = []
_warnings: list[str] = []


def _result(ok: bool, label: str, detail: str = "", warn_only: bool = False) -> None:
    if ok:
        print(f"  {GREEN}✓{NC} {label}")
    elif warn_only:
        msg = f"  {YELLOW}⚠{NC} {label}"
        if detail:
            msg += f"  [{detail}]"
        print(msg)
        _warnings.append(label)
    else:
        msg = f"  {RED}✗{NC} {label}"
        if detail:
            msg += f"  [{detail}]"
        print(msg)
        _errors.append(label)


def _section(title: str) -> None:
    print(f"\n{BOLD}{title}{NC}")


def _parse_kv(path: Path) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split("#")[0].strip().strip("'\"")
        kv[key.strip()] = val
    return kv


# ── Mapping: firstboot.conf key → dietpi.txt AUTO_SETUP_* key ─────────────────
# STATIC_IP and TZ are handled separately below.
DIETPI_MAP: dict[str, str] = {
    "HOSTNAME":        "AUTO_SETUP_NET_HOSTNAME",
    "LOCALE":          "AUTO_SETUP_LOCALE",
    "KEYBOARD_LAYOUT": "AUTO_SETUP_KEYBOARD_LAYOUT",
    "GLOBAL_PASSWORD": "AUTO_SETUP_GLOBAL_PASSWORD",
    "SSH_PUBKEY":      "AUTO_SETUP_SSH_PUBKEY",
    "STATIC_MASK":     "AUTO_SETUP_NET_STATIC_MASK",
    "STATIC_GATEWAY":  "AUTO_SETUP_NET_STATIC_GATEWAY",
    "STATIC_DNS":      "AUTO_SETUP_NET_STATIC_DNS",
}

# Fixed values always applied (stack requirements — not user-configurable)
DIETPI_FIXED: dict[str, str] = {
    "AUTO_SETUP_SSH_SERVER_INDEX":          "-2",
    "SOFTWARE_DISABLE_SSH_PASSWORD_LOGINS": "1",
    "AUTO_SETUP_AUTOMATED":                 "1",
    "AUTO_SETUP_HEADLESS":                  "1",
    "AUTO_SETUP_AUTOSTART_TARGET_INDEX":    "0",
    "AUTO_SETUP_INSTALL_SOFTWARE_ID":       "17 58 73 105 134 152 162",
    "AUTO_SETUP_NET_ETHERNET_ENABLED":      "1",
    "AUTO_SETUP_NET_WIFI_ENABLED":          "0",
    "CONFIG_CHECK_APT_UPDATES":             "2",
    "CONFIG_CHECK_DIETPI_UPDATES":          "1",
    "SURVEY_OPTED_IN":                      "0",
}

# Keys written to out/system.env — read by configure_*.sh (non-Docker host config)
SYSTEM_ENV_KEYS: list[str] = [
    "SSH_PORT",
    "TAILSCALE_AUTHKEY",
    "DIETPI_USER",
    "LAN_SUBNET",
]

# Keys written to out/docker.env — read by docker-compose and configure_tailscale.sh
# NTFY_ADMIN_HASH and NTFY_PUBLISH_HASH are computed in build(), not read from conf directly.
DOCKER_ENV_KEYS: list[str] = [
    "TZ",
    "CONFIG_ROOT",
    "GATUS_PORT",
    "NTFY_PORT",
    "NTFY_ADMIN_HASH",
    "NTFY_PUBLISH_HASH",
]

# Defaults for optional env keys
ENV_DEFAULTS: dict[str, str] = {
    "DIETPI_USER":  "dietpi",
    "LAN_SUBNET":   "",
    "TZ":           "Europe/Rome",
    "CONFIG_ROOT":  "/home/dietpi/docker/config",
    "GATUS_PORT":   "8080",
    "NTFY_PORT":    "2586",
}


# ── Patch logic ────────────────────────────────────────────────────────────────

def _patch_dietpi(base: str, patches: dict[str, str]) -> str:
    """Apply key=value patches to dietpi.txt content.

    Matches both uncommented (KEY=...) and commented (#KEY=...) lines.
    Keys not found in the base file are appended at the end.
    """
    lines = base.splitlines(keepends=True)
    result: list[str] = []
    patched: set[str] = set()

    for line in lines:
        matched_key = None
        for key in patches:
            if re.match(rf'^#?\s*{re.escape(key)}\s*=', line):
                matched_key = key
                break
        if matched_key is not None:
            result.append(f"{matched_key}={patches[matched_key]}\n")
            patched.add(matched_key)
        else:
            result.append(line)

    for key, val in patches.items():
        if key not in patched:
            result.append(f"{key}={val}\n")

    return "".join(result)


# ── Validators ─────────────────────────────────────────────────────────────────

def check_gitignore(repo: Path) -> None:
    _section(".gitignore")
    path = repo / ".gitignore"
    if not path.exists():
        _result(False, ".gitignore exists")
        return
    content = path.read_text(encoding="utf-8")
    for sensitive in ["system/firstboot/firstboot.conf", "system/firstboot/out/"]:
        _result(sensitive in content, f"{sensitive} is listed in .gitignore")


def validate_conf(kv: dict[str, str]) -> None:
    _section("system/firstboot/firstboot.conf")

    placeholders = ("CHANGE_ME", "REPLACE_WITH", "<your-")

    # SSH public key
    pubkey = kv.get("SSH_PUBKEY", "")
    key_ok = (
        bool(pubkey)
        and not any(p in pubkey for p in placeholders)
        and pubkey.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-"))
    )
    _result(key_ok, "SSH_PUBKEY is set and looks like a valid public key",
            "placeholder or unrecognised format")

    # Global password
    pwd = kv.get("GLOBAL_PASSWORD", "")
    _result(
        bool(pwd) and not any(p in pwd for p in placeholders) and len(pwd) >= 12,
        "GLOBAL_PASSWORD is set, not a placeholder, and ≥12 chars",
        f"placeholder or too short (got {len(pwd)} chars)"
    )

    # Hostname
    hostname = kv.get("HOSTNAME", "")
    _result(bool(hostname), "HOSTNAME is set", "empty")

    # Timezone
    tz = kv.get("TZ", "")
    _result(bool(tz) and "/" in tz, "TZ looks valid",
            f"got '{tz}' — expected format like 'Europe/Rome'")

    # REPO_URL
    repo_url = kv.get("REPO_URL", "")
    _result(
        bool(repo_url) and not any(p in repo_url for p in placeholders),
        "REPO_URL is set and not a placeholder",
        f"got '{repo_url}' — update to your actual GitHub repo URL"
    )

    # SSH_PORT
    ssh_str = kv.get("SSH_PORT", "")
    ssh_ok = ssh_str.isdigit() and 1024 <= int(ssh_str) <= 65535
    _result(ssh_ok,
            f"SSH_PORT is numeric and in range 1024–65535  (got '{ssh_str}')",
            "missing, non-numeric, or out of safe range")

    # Static IP (only if set)
    static_ip = kv.get("STATIC_IP", "")
    if static_ip:
        ip_ok = re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", static_ip) is not None
        _result(ip_ok, f"STATIC_IP looks like a valid IPv4  (got '{static_ip}')",
                "invalid format")
        gw = kv.get("STATIC_GATEWAY", "")
        _result(
            bool(gw) and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", gw) is not None,
            "STATIC_GATEWAY is set when using static IP",
            f"got '{gw}'"
        )
    else:
        _result(True, "STATIC_IP not set — DHCP will be used")

    # Tailscale authkey
    tskey = kv.get("TAILSCALE_AUTHKEY", "")
    if tskey:
        key_looks_valid = tskey.startswith("tskey-") or len(tskey) > 20
        _result(key_looks_valid,
                "TAILSCALE_AUTHKEY looks like a valid Tailscale key",
                f"unexpected format (starts with '{tskey[:10]}')")
    else:
        _result(True, "TAILSCALE_AUTHKEY not set — manual mode (run 'tailscale up' after provisioning)")

    # Ntfy passwords
    ntfy_pass = kv.get("NTFY_ADMIN_PASSWORD", "")
    _result(
        bool(ntfy_pass) and not any(p in ntfy_pass for p in placeholders),
        "NTFY_ADMIN_PASSWORD set",
        "not set — ntfyadmin user will not be created automatically",
        warn_only=True
    )
    pub_pass = kv.get("NTFY_PUBLISH_PASSWORD", "")
    _result(
        bool(pub_pass) and len(pub_pass) >= 16
        and not any(p in pub_pass for p in placeholders),
        "NTFY_PUBLISH_PASSWORD set and ≥16 chars",
        "not set, too short, or still a placeholder — generate with: openssl rand -hex 24",
        warn_only=True
    )

    # Port uniqueness
    _section("Port uniqueness")
    port_map: dict[str, str] = {}
    for var, default in [("SSH_PORT", None), ("GATUS_PORT", "8080"), ("NTFY_PORT", "2586")]:
        val = kv.get(var, default or "")
        if val and val.isdigit():
            if val in port_map:
                _result(False, "No port conflicts",
                        f"{var} and {port_map[val]} both use port {val}")
            else:
                port_map[val] = var
    if len(port_map) == 3 or (not kv.get("SSH_PORT") and len(port_map) == 2):
        _result(True, f"All ports unique  {list(port_map.keys())}")


# ── Build ───────────────────────────────────────────────────────────────────────

def build(here: Path, repo: Path, kv: dict[str, str]) -> None:
    _section("Generating boot files")

    base_path       = here / "base" / "dietpi.txt.base"
    script_template = here / "base" / "Automation_Custom_Script.sh.base"
    out_dir         = here / "out"

    if not base_path.exists():
        _result(False, "system/firstboot/base/dietpi.txt.base exists")
        return
    if not script_template.exists():
        _result(False, "system/firstboot/base/Automation_Custom_Script.sh.base exists")
        return

    out_dir.mkdir(exist_ok=True)

    # Build dietpi.txt patches
    patches = dict(DIETPI_FIXED)

    for conf_key, dietpi_key in DIETPI_MAP.items():
        val = kv.get(conf_key, "")
        if val:
            patches[dietpi_key] = val

    # TZ → AUTO_SETUP_TIMEZONE
    tz = kv.get("TZ", ENV_DEFAULTS["TZ"])
    patches["AUTO_SETUP_TIMEZONE"] = tz

    # Static IP
    static_ip = kv.get("STATIC_IP", "")
    if static_ip:
        patches["AUTO_SETUP_NET_USESTATIC"] = "1"
        patches["AUTO_SETUP_NET_STATIC_IP"] = static_ip
    else:
        patches["AUTO_SETUP_NET_USESTATIC"] = "0"

    # 1. dietpi.txt
    base_content = base_path.read_text(encoding="utf-8")
    patched_content = _patch_dietpi(base_content, patches)
    out_dietpi = out_dir / "dietpi.txt"
    out_dietpi.write_text(patched_content, encoding="utf-8")
    _result(True, f"system/firstboot/out/dietpi.txt  ({out_dietpi.stat().st_size} bytes)")

    # 2. Automation_Custom_Script.sh — substitute {{REPO_URL}} only
    script = script_template.read_text(encoding="utf-8")
    script = script.replace("{{REPO_URL}}", kv.get("REPO_URL", ""))
    out_script_path = out_dir / "Automation_Custom_Script.sh"
    out_script_path.write_text(script, encoding="utf-8")
    out_script_path.chmod(out_script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    _result(True, "system/firstboot/out/Automation_Custom_Script.sh")

    # 3. system_env — host config read by configure_*.sh (copy this to SD card)
    sys_lines = ["# Generated by system/firstboot/prepare_firstboot.py — do not edit directly.\n"]
    for key in SYSTEM_ENV_KEYS:
        val = kv.get(key, ENV_DEFAULTS.get(key, ""))
        sys_lines.append(f"{key}={val}\n")
    out_system = out_dir / "system_env"
    out_system.write_text("".join(sys_lines), encoding="utf-8")
    _result(True, "system/firstboot/out/system_env")

    # 4. docker.env — docker config read by docker-compose and configure_tailscale.sh
    #    Bcrypt hashes for ntfy users (ntfyadmin + publisher) are computed here.
    admin_pass   = kv.get("NTFY_ADMIN_PASSWORD", "")
    publish_pass = kv.get("NTFY_PUBLISH_PASSWORD", "")
    need_bcrypt  = bool(admin_pass or publish_pass)
    if need_bcrypt and _bcrypt is None:
        _result(False, "bcrypt required for NTFY hash generation",
                "install with: pip3 install bcrypt")
        return
    if admin_pass:
        kv["NTFY_ADMIN_HASH"] = _bcrypt.hashpw(
            admin_pass.encode(), _bcrypt.gensalt(rounds=10)
        ).decode()
    else:
        kv["NTFY_ADMIN_HASH"] = ""
    if publish_pass:
        kv["NTFY_PUBLISH_HASH"] = _bcrypt.hashpw(
            publish_pass.encode(), _bcrypt.gensalt(rounds=10)
        ).decode()
    else:
        kv["NTFY_PUBLISH_HASH"] = ""

    docker_lines = ["# Generated by system/firstboot/prepare_firstboot.py — do not edit directly.\n"]
    for key in DOCKER_ENV_KEYS:
        val = kv.get(key, ENV_DEFAULTS.get(key, ""))
        docker_lines.append(f"{key}={val}\n")
    out_docker = out_dir / "docker_env"
    out_docker.write_text("".join(docker_lines), encoding="utf-8")
    _result(True, "system/firstboot/out/docker_env")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    here = Path(__file__).resolve().parent          # system/firstboot/
    repo = here.parent.parent                        # repo root
    conf_path = here / "firstboot.conf"

    print(f"{BOLD}pizero2-stack — prepare_firstboot{NC}")
    print(f"Repo: {repo}")

    if not conf_path.exists():
        print(f"\n{RED}✗ system/firstboot/firstboot.conf not found.{NC}")
        print("  Copy and fill in the template:")
        print("    cp system/firstboot/firstboot.conf.example system/firstboot/firstboot.conf")
        sys.exit(1)

    kv = _parse_kv(conf_path)

    check_gitignore(repo)
    validate_conf(kv)

    print()
    if _errors:
        print(f"{RED}{BOLD}✗ {len(_errors)} error(s) must be fixed before generating:{NC}")
        for e in _errors:
            print(f"   • {e}")
        if _warnings:
            print(f"{YELLOW}  + {len(_warnings)} warning(s) — see above{NC}")
        sys.exit(1)

    if _warnings:
        print(f"{YELLOW}{BOLD}⚠ {len(_warnings)} warning(s):{NC}")
        for w in _warnings:
            print(f"   • {w}")

    build(here, repo, kv)

    print()
    if _errors:
        print(f"{RED}{BOLD}✗ Build failed — see errors above.{NC}")
        sys.exit(1)

    print(f"{GREEN}{BOLD}✓ Boot files written to system/firstboot/out/{NC}")
    print()
    print("Copy these 4 files to the SD card boot partition:")
    print("  system/firstboot/out/dietpi.txt")
    print("  system/firstboot/out/Automation_Custom_Script.sh")
    print("  system/firstboot/out/system_env")
    print("  system/firstboot/out/docker_env")
    print()
    print("Overwrite the existing dietpi.txt on the boot partition.")


if __name__ == "__main__":
    main()
