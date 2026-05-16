#!/usr/bin/env python3
"""
VPN pre-flight: bring up a non-US Wireguard tunnel before the daily run.

Usage:
    python scripts/vpn_preflight.py --connect
    python scripts/vpn_preflight.py --disconnect
    python scripts/vpn_preflight.py --status

`--connect`: tries each configured tunnel in order, verifies the public IP
is outside the US within a timeout, and exits 0 only when verified. On
failure, all tunnels are torn down and the script exits non-zero so the
caller (e.g. daily_paper_run wrapper) refuses to proceed.

`--disconnect`: tears down any tunnel this script brought up. Safe to call
even when nothing is connected.

`--status`: prints which tunnel (if any) is active and the current public
IP / country.

Designed to be called from a wrapper invoked by `launchd`. Requires
passwordless `sudo` for `wg-quick up/down` on the configured paths — see
the sudoers entry committed alongside this script.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

HOME = Path.home()
WG_CONFIG_DIR = HOME / ".proton-wg"
WG_QUICK = "/opt/homebrew/bin/wg-quick"  # Homebrew install path on Apple Silicon

# Tunnels tried in order. First one to verify wins.
TUNNELS = [
    WG_CONFIG_DIR / "proton-nl.conf",
    WG_CONFIG_DIR / "proton-jp.conf",
]

GEO_ENDPOINTS = [
    "https://ipinfo.io/json",
    "https://ifconfig.co/json",
]

VERIFY_TIMEOUT_S = 15
HTTP_TIMEOUT_S = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s vpn_preflight %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public IP / geo lookup
# ---------------------------------------------------------------------------


def get_public_ip_info() -> tuple[str | None, str | None]:
    """
    Return (ip, country_code) using whichever geo endpoint responds first.
    Returns (None, None) if all endpoints fail.
    """
    for url in GEO_ENDPOINTS:
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT_S)
            if r.status_code != 200:
                continue
            j = r.json()
            ip = j.get("ip")
            country = j.get("country") or j.get("country_iso")
            if ip and country:
                return ip, country.upper()
        except Exception as exc:
            log.debug(f"geo lookup {url} failed: {exc}")
    return None, None


# ---------------------------------------------------------------------------
# wg-quick wrappers
# ---------------------------------------------------------------------------


def run_wg(action: str, conf: Path) -> tuple[int, str]:
    """Run `sudo -n wg-quick <action> <conf>`. Returns (returncode, combined output)."""
    cmd = ["sudo", "-n", WG_QUICK, action, str(conf)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


# wg-quick on macOS doesn't name the kernel interface after the config; the
# kernel gets `utunN` and wg-quick records the mapping in a state file at
# /var/run/wireguard/<name>.name. The parent dir is world-readable, so we
# can detect liveness without sudo by checking for that file's existence.
WG_STATE_DIR = Path("/var/run/wireguard")


def is_tunnel_active(conf: Path) -> bool:
    """True iff wg-quick has a live state file for this conf."""
    return (WG_STATE_DIR / f"{conf.stem}.name").exists()


def teardown_all() -> None:
    """Bring down every configured tunnel. Always attempts `wg-quick down`;
    a non-zero return is treated as 'already down' and logged at debug.
    """
    for conf in TUNNELS:
        rc, out = run_wg("down", conf)
        if rc == 0:
            log.info(f"tore down {conf.stem}")
        else:
            log.debug(f"down {conf.stem} returned rc={rc} (likely not up): {out.strip()}")


# ---------------------------------------------------------------------------
# Connect path with verification
# ---------------------------------------------------------------------------


def connect_and_verify(conf: Path) -> bool:
    """Bring up `conf`, poll public IP, return True if exit country is non-US."""
    log.info(f"bringing up {conf.stem}")
    rc, out = run_wg("up", conf)
    if rc != 0:
        log.error(f"wg-quick up failed for {conf.stem}: {out.strip()}")
        return False

    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    while time.monotonic() < deadline:
        ip, country = get_public_ip_info()
        if ip and country:
            log.info(f"current public IP={ip} country={country}")
            if country != "US":
                return True
            log.warning(f"{conf.stem}: exit country is still US — failing closed")
            return False
        time.sleep(1)

    log.error(f"{conf.stem}: geo verification timed out after {VERIFY_TIMEOUT_S}s")
    return False


def cmd_connect() -> int:
    # If anything is already up, treat that as authoritative — verify it.
    for conf in TUNNELS:
        if is_tunnel_active(conf):
            ip, country = get_public_ip_info()
            if country and country != "US":
                log.info(
                    f"already connected via {conf.stem}; IP={ip} country={country}"
                )
                return 0
            log.warning(f"existing tunnel {conf.stem} resolves to {country}; tearing down")
    teardown_all()

    for conf in TUNNELS:
        if not conf.exists():
            log.error(f"missing config: {conf}")
            continue
        if connect_and_verify(conf):
            return 0
        # Failed this one — tear down before trying the next
        rc, out = run_wg("down", conf)
        if rc != 0:
            log.warning(f"cleanup down for {conf.stem} rc={rc}: {out.strip()}")

    log.error("all tunnels failed verification")
    return 2


def cmd_disconnect() -> int:
    teardown_all()
    return 0


def cmd_status() -> int:
    active = [c for c in TUNNELS if is_tunnel_active(c)]
    ip, country = get_public_ip_info()
    payload = {
        "active": [c.stem for c in active],
        "public_ip": ip,
        "country": country,
    }
    print(json.dumps(payload, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--connect", action="store_true")
    group.add_argument("--disconnect", action="store_true")
    group.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.connect:
        return cmd_connect()
    if args.disconnect:
        return cmd_disconnect()
    if args.status:
        return cmd_status()
    return 1


if __name__ == "__main__":
    sys.exit(main())
