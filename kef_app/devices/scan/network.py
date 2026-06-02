from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
from typing import Optional

from ...config import AppConfig
from ..speaker_models import normalize_mac

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_routable_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified)


def get_local_ipv4_candidates(seed_ip: Optional[str] = None) -> list[str]:
    candidates: set[str] = set()

    for host in {socket.gethostname(), socket.getfqdn()}:
        if not host:
            continue
        try:
            for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
                ip = info[4][0]
                if is_routable_ipv4(ip):
                    candidates.add(ip)
        except socket.gaierror:
            pass

    for target in [seed_ip, "8.8.8.8", "1.1.1.1"]:
        if not target:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target, 80))
                ip = sock.getsockname()[0]
                if is_routable_ipv4(ip):
                    candidates.add(ip)
        except OSError:
            pass

    return sorted(candidates)


def build_candidate_networks(seed_ip: Optional[str], config: AppConfig, log) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    def add_network(spec: str):
        try:
            network = ipaddress.IPv4Network(spec, strict=False)
        except Exception:
            return
        if network.num_addresses <= 2:
            return
        if network.num_addresses - 2 > config.mac_discovery_max_hosts_per_network:
            log.info(
                f"Skipping oversized scan network | cidr={network} | "
                f"hosts={network.num_addresses - 2} | cap={config.mac_discovery_max_hosts_per_network}"
            )
            return
        key = str(network)
        if key not in seen:
            seen.add(key)
            networks.append(network)

    if seed_ip and is_routable_ipv4(seed_ip):
        add_network(f"{seed_ip}/{config.mac_discovery_subnet_prefix}")

    for ip in get_local_ipv4_candidates(seed_ip):
        add_network(f"{ip}/{config.mac_discovery_subnet_prefix}")

    for cidr in config.mac_discovery_extra_cidrs:
        if cidr:
            add_network(cidr)

    return networks


def parse_arp_table(output: str) -> dict[str, str]:
    arp_map: dict[str, str] = {}
    pattern = re.compile(r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<mac>(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2})")
    for match in pattern.finditer(output or ""):
        ip = match.group("ip")
        mac = normalize_mac(match.group("mac"))
        if is_routable_ipv4(ip) and mac:
            arp_map[ip] = mac
    return arp_map


def read_arp_table(log) -> dict[str, str]:
    try:
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
        return parse_arp_table(completed.stdout)
    except Exception as exc:
        log.info(f"Failed to read the ARP table | {exc}")
        return {}


def probe_ip_port(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((ip, port))
        return True
    except OSError:
        return False
