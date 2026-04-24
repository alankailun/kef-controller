from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..config import AppConfig
from .speaker_models import SpeakerIdentity, normalize_mac, normalize_model_label, normalize_name

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


def http_get_kef_data(ip: str, path: str, timeout: float, roles: str = "value"):
    try:
        query = urllib.parse.urlencode({"path": path, "roles": roles})
        url = f"http://{ip}/api/getData?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "kef-controller/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        data = json.loads(payload.decode("utf-8", errors="ignore"))
        return data if isinstance(data, list) and data else None
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


def _prioritize_seed(hosts: list[str], seed_ip: Optional[str]) -> list[str]:
    if seed_ip and seed_ip in hosts:
        return [seed_ip] + [h for h in hosts if h != seed_ip]
    return hosts


def parse_kef_release_text(release_text: str) -> str:
    if not release_text:
        return ""
    return normalize_model_label(str(release_text).split("_", 1)[0].strip())


def looks_like_supported_kef_model(model: str, config: AppConfig) -> bool:
    normalized = normalize_model_label(model)
    if not normalized:
        return False
    allowed = {normalize_model_label(item) for item in config.supported_w2_models}
    return normalized in allowed


def identity_matches_expectation(identity: SpeakerIdentity, config: AppConfig, known_mac: str = "") -> tuple[bool, str]:
    expected_mac = normalize_mac(config.expected_speaker_mac) or normalize_mac(known_mac)
    expected_name = normalize_name(config.expected_speaker_name)

    if expected_mac and identity.mac != expected_mac:
        return False, "expected_mac_mismatch"

    if expected_name:
        if normalize_name(identity.speaker_name) != expected_name:
            return False, "expected_name_mismatch"
        return True, "expected_name"

    if expected_mac:
        return True, "expected_mac"

    return True, "no_expectation"


def identify_kef_device(ip: str, config: AppConfig) -> Optional[SpeakerIdentity]:
    release_data = http_get_kef_data(ip, "settings:/releasetext", timeout=config.blind_discovery_http_timeout)
    if not release_data:
        return None

    if not isinstance(release_data[0], dict):
        return None
    release_text = release_data[0].get("string_", "")
    speaker_model = parse_kef_release_text(release_text)
    if not looks_like_supported_kef_model(speaker_model, config):
        return None

    mac_address = ""
    mac_data = http_get_kef_data(ip, "settings:/system/primaryMacAddress", timeout=config.blind_discovery_http_timeout)
    if mac_data and isinstance(mac_data[0], dict):
        mac_address = mac_data[0].get("string_", "")

    speaker_name = ""
    name_data = http_get_kef_data(ip, "settings:/deviceName", timeout=config.blind_discovery_http_timeout)
    if name_data and isinstance(name_data[0], dict):
        speaker_name = name_data[0].get("string_", "")

    return SpeakerIdentity(
        ip=ip,
        mac=normalize_mac(mac_address),
        mac_display=mac_address,
        speaker_name=speaker_name,
        speaker_model=speaker_model,
        firmware_version=release_text,
        backend=config.backend_name,
    )


def _candidate_summary(candidate: SpeakerIdentity) -> str:
    return (
        f"{candidate.ip}:{candidate.speaker_model or '?'}:{candidate.speaker_name or '<unnamed>'}:"
        f"{candidate.mac_display or candidate.mac or '<no-mac>'}"
    )


def discover_kef_device_blind(known_mac: str, seed_ip: Optional[str], config: AppConfig, log) -> Optional[SpeakerIdentity]:
    normalized_known_mac = normalize_mac(known_mac)
    expected_name = normalize_name(config.expected_speaker_name)
    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        log.info("No scan networks are available, so a full device scan cannot run")
        return None

    log.info(
        "Starting full KEF device scan | "
        f"expected_name={config.expected_speaker_name or '<empty>'} "
        f"known_mac={known_mac or '<empty>'} normalized={normalized_known_mac or '<empty>'} "
        f"seed_ip={seed_ip or '<empty>'} networks={[str(n) for n in networks]} "
        f"probe_port={config.mac_discovery_tcp_port} probe_timeout={config.mac_discovery_probe_timeout:.2f}s "
        f"http_timeout={config.blind_discovery_http_timeout:.2f}s probe_workers={config.mac_discovery_max_workers} "
        f"identify_workers={config.blind_discovery_max_workers}"
    )

    candidates_by_ip: dict[str, SpeakerIdentity] = {}

    for network in networks:
        hosts = _prioritize_seed([str(host) for host in network.hosts()], seed_ip)

        with ThreadPoolExecutor(max_workers=config.mac_discovery_max_workers) as executor:
            reachable_pairs = zip(
                hosts,
                executor.map(
                    lambda host_ip: probe_ip_port(host_ip, config.mac_discovery_tcp_port, config.mac_discovery_probe_timeout),
                    hosts,
                ),
            )
            reachable_hosts = [host_ip for host_ip, ok in reachable_pairs if ok]

        if not reachable_hosts:
            continue

        max_workers = max(1, min(config.blind_discovery_max_workers, len(reachable_hosts)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for identity in executor.map(lambda host_ip: identify_kef_device(host_ip, config), reachable_hosts):
                if not identity:
                    continue
                candidates_by_ip[identity.ip] = identity
                if normalized_known_mac and identity.mac == normalized_known_mac:
                    log.info(
                        f"Full scan matched the known MAC address | ip={identity.ip} | "
                        f"mac={identity.mac_display or identity.mac} | model={identity.speaker_model} | "
                        f"name={identity.speaker_name or '<unnamed>'}"
                    )
                    return identity.with_match("known_mac")

    candidates = list(candidates_by_ip.values())
    if not candidates:
        log.info(
            f"Full scan did not find any supported KEF device | "
            f"expected_name={config.expected_speaker_name or '<empty>'} "
            f"known_mac={known_mac or '<empty>'} | seed_ip={seed_ip or '<empty>'}"
        )
        return None

    if expected_name:
        name_matches = [candidate for candidate in candidates if normalize_name(candidate.speaker_name) == expected_name]
        if len(name_matches) == 1:
            candidate = name_matches[0]
            log.info(
                f"Full scan matched the expected speaker name | ip={candidate.ip} | "
                f"model={candidate.speaker_model} | name={candidate.speaker_name or '<unnamed>'} | "
                f"mac={candidate.mac_display or candidate.mac or '<no-mac>'}"
            )
            return candidate.with_match("expected_name")
        if len(name_matches) > 1:
            summary = ", ".join(_candidate_summary(candidate) for candidate in name_matches)
            log.info(f"Full scan found multiple devices with the same expected name; refusing auto-select | candidates=[{summary}]")
            return None

    if len(candidates) == 1:
        candidate = candidates[0]
        ok, reason = identity_matches_expectation(candidate, config, known_mac)
        if ok:
            log.info(
                f"Full scan found exactly one supported KEF device | ip={candidate.ip} | "
                f"mac={candidate.mac_display or candidate.mac} | model={candidate.speaker_model} | "
                f"name={candidate.speaker_name or '<unnamed>'}"
            )
            return candidate.with_match(reason if reason != "no_expectation" else "unique")

    summary = ", ".join(_candidate_summary(candidate) for candidate in candidates)
    log.info(f"Full scan found multiple KEF devices and could not pick one safely | candidates=[{summary}]")
    return None


def discover_ip_by_mac(target_mac: str, seed_ip: Optional[str], config: AppConfig, log) -> Optional[str]:
    normalized_target = normalize_mac(target_mac)
    if not normalized_target:
        return None

    arp_before = read_arp_table(log)
    for ip, mac in arp_before.items():
        if mac == normalized_target:
            return ip

    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        log.info("No scan networks are available, so MAC-based IP recovery cannot run")
        return None

    log.info(
        "Starting MAC-based IP recovery | "
        f"target_mac={target_mac} normalized={normalized_target} "
        f"seed_ip={seed_ip or '<empty>'} networks={[str(n) for n in networks]} "
        f"port={config.mac_discovery_tcp_port} timeout={config.mac_discovery_probe_timeout:.2f}s "
        f"workers={config.mac_discovery_max_workers}"
    )

    for network in networks:
        hosts = _prioritize_seed([str(host) for host in network.hosts()], seed_ip)

        with ThreadPoolExecutor(max_workers=config.mac_discovery_max_workers) as executor:
            list(
                executor.map(
                    lambda ip: probe_ip_port(ip, config.mac_discovery_tcp_port, config.mac_discovery_probe_timeout),
                    hosts,
                )
            )

        arp_map = read_arp_table(log)
        for ip, mac in arp_map.items():
            if mac == normalized_target:
                log.info(f"Recovered the speaker IP from MAC address | mac={target_mac} | ip={ip} | network={network}")
                return ip

    log.info(f"MAC-based IP recovery did not find a match | mac={target_mac} | seed_ip={seed_ip or '<empty>'}")
    return None
