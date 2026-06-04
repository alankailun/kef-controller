from __future__ import annotations

import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from ...config import AppConfig
from ..speaker_models import SpeakerIdentity, normalize_mac
from .identity import identify_kef_device
from .network import build_candidate_networks, probe_ip_port, read_arp_table

_SEED_IDENTITY_HTTP_TIMEOUT_FLOOR_S = 1.50
_SEED_IDENTITY_RETRY_DELAY_S = 0.35


def _candidate_summary(candidate: SpeakerIdentity) -> str:
    return (
        f"{candidate.ip}:{candidate.speaker_model or '?'}:{candidate.speaker_name or '<unnamed>'}:"
        f"{candidate.mac_display or candidate.mac or '<no-mac>'}"
    )


def _sort_identity_key(identity: SpeakerIdentity) -> tuple[int, str]:
    try:
        return (int(ipaddress.IPv4Address(identity.ip)), identity.speaker_name.casefold())
    except ipaddress.AddressValueError:
        return (0, identity.speaker_name.casefold())


def _reachable_hosts(
    executor: ThreadPoolExecutor,
    hosts: list[str],
    config: AppConfig,
) -> list[str]:
    reachable_pairs = zip(
        hosts,
        executor.map(
            lambda host_ip: probe_ip_port(host_ip, config.mac_discovery_tcp_port, config.mac_discovery_probe_timeout),
            hosts,
        ),
    )
    return [host_ip for host_ip, ok in reachable_pairs if ok]


def _identify_hosts(
    executor: ThreadPoolExecutor,
    hosts: list[str],
    config: AppConfig,
) -> list[SpeakerIdentity]:
    identities: list[SpeakerIdentity] = []
    batch_size = max(1, config.blind_discovery_max_workers)
    for start in range(0, len(hosts), batch_size):
        batch = hosts[start : start + batch_size]
        found = executor.map(lambda host_ip: identify_kef_device(host_ip, config), batch)
        identities.extend(identity for identity in found if identity)
    return identities


def _shared_discovery_workers(host_count: int, config: AppConfig) -> int:
    return max(1, min(host_count, max(config.mac_discovery_max_workers, config.blind_discovery_max_workers)))


def _candidate_hosts(networks: list[ipaddress.IPv4Network], seed_ip: Optional[str]) -> list[str]:
    seen: set[str] = set()
    hosts: list[str] = []
    for network in networks:
        for host in network.hosts():
            host_ip = str(host)
            if host_ip in seen:
                continue
            seen.add(host_ip)
            if seed_ip and host_ip == seed_ip:
                hosts.insert(0, host_ip)
            else:
                hosts.append(host_ip)
    return hosts


def _scan_candidate_hosts(
    networks: list[ipaddress.IPv4Network],
    seed_ip: Optional[str],
    config: AppConfig,
) -> tuple[list[str], list[str], list[SpeakerIdentity]]:
    hosts = _candidate_hosts(networks, seed_ip)
    if not hosts:
        return [], [], []

    with ThreadPoolExecutor(max_workers=_shared_discovery_workers(len(hosts), config)) as executor:
        reachable_hosts = _reachable_hosts(executor, hosts, config)
        identities = _identify_hosts(executor, reachable_hosts, config) if reachable_hosts else []
    return hosts, reachable_hosts, identities


def _probe_seed_identity(
    seed_ip: Optional[str],
    config: AppConfig,
    log,
    *,
    phase: str,
    delay_before: float = 0.0,
) -> Optional[SpeakerIdentity]:
    if not seed_ip:
        return None
    if delay_before > 0:
        time.sleep(delay_before)

    started = time.monotonic()
    timeout = max(config.blind_discovery_http_timeout, _SEED_IDENTITY_HTTP_TIMEOUT_FLOOR_S)
    identity = identify_kef_device(seed_ip, config, timeout=timeout)
    duration_ms = int((time.monotonic() - started) * 1000)
    if identity:
        log.info(
            "Seed KEF identity probe succeeded | "
            f"phase={phase} seed_ip={seed_ip} timeout={timeout:.2f}s "
            f"duration_ms={duration_ms} candidate={_candidate_summary(identity)}"
        )
        return identity

    log.info(
        "Seed KEF identity probe missed | "
        f"phase={phase} seed_ip={seed_ip} timeout={timeout:.2f}s duration_ms={duration_ms}"
    )
    return None


def _notify_candidate(
    on_candidate: Optional[Callable[[SpeakerIdentity], None]],
    identity: SpeakerIdentity,
    log,
) -> None:
    if on_candidate is None:
        return
    try:
        on_candidate(identity)
    except Exception as exc:
        log.info(f"Speaker scan candidate callback failed | ip={identity.ip or '<empty>'} | {exc}")


def discover_kef_devices(
    seed_ip: Optional[str],
    config: AppConfig,
    log,
    *,
    on_candidate: Optional[Callable[[SpeakerIdentity], None]] = None,
) -> list[SpeakerIdentity]:
    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        log.info("No scan networks are available, so a manual KEF device scan cannot run")
        return []

    scan_started = time.monotonic()
    total_reachable_hosts = 0
    total_identified_kef = 0
    log.info(
        "Starting manual KEF device scan | "
        f"seed_ip={seed_ip or '<empty>'} networks={[str(n) for n in networks]} "
        f"probe_port={config.mac_discovery_tcp_port} probe_timeout={config.mac_discovery_probe_timeout:.2f}s "
        f"http_timeout={config.blind_discovery_http_timeout:.2f}s probe_workers={config.mac_discovery_max_workers} "
        f"identify_workers={config.blind_discovery_max_workers}"
    )

    candidates_by_ip: dict[str, SpeakerIdentity] = {}
    seed_identity = _probe_seed_identity(seed_ip, config, log, phase="manual_scan_start")
    if seed_identity:
        total_identified_kef += 1
        candidates_by_ip[seed_identity.ip] = seed_identity
        _notify_candidate(on_candidate, seed_identity, log)

    probe_started = time.monotonic()
    hosts, reachable_hosts, identities = _scan_candidate_hosts(networks, seed_ip, config)
    total_reachable_hosts += len(reachable_hosts)
    total_identified_kef += len(identities)
    for identity in identities:
        candidates_by_ip[identity.ip] = identity
        _notify_candidate(on_candidate, identity, log)

    log.info(
        "Manual scan networks finished | "
        f"networks={[str(n) for n in networks]} hosts={len(hosts)} "
        f"reachable_hosts_count={len(reachable_hosts)} identified_kef_count={len(identities)} "
        f"duration_ms={int((time.monotonic() - probe_started) * 1000)}"
    )

    if seed_ip and seed_ip not in candidates_by_ip:
        seed_identity = _probe_seed_identity(
            seed_ip,
            config,
            log,
            phase="manual_scan_after_network_miss",
            delay_before=_SEED_IDENTITY_RETRY_DELAY_S,
        )
        if seed_identity:
            total_identified_kef += 1
            candidates_by_ip[seed_identity.ip] = seed_identity
            _notify_candidate(on_candidate, seed_identity, log)

    candidates = sorted(candidates_by_ip.values(), key=_sort_identity_key)
    summary = ", ".join(_candidate_summary(candidate) for candidate in candidates) or "<none>"
    log.info(
        f"Manual KEF device scan finished | count={len(candidates)} "
        f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
        f"duration_ms={int((time.monotonic() - scan_started) * 1000)} | candidates=[{summary}]"
    )
    return candidates


def discover_kef_device_blind(known_mac: str, seed_ip: Optional[str], config: AppConfig, log) -> Optional[SpeakerIdentity]:
    normalized_known_mac = normalize_mac(known_mac)
    if not normalized_known_mac:
        log.info("Full KEF target recovery requires a Target Speaker MAC")
        return None

    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        log.info("No scan networks are available, so a full device scan cannot run")
        return None

    scan_started = time.monotonic()
    total_reachable_hosts = 0
    total_identified_kef = 0
    log.info(
        "Starting full KEF device scan | "
        f"known_mac={known_mac or '<empty>'} normalized={normalized_known_mac or '<empty>'} "
        f"seed_ip={seed_ip or '<empty>'} networks={[str(n) for n in networks]} "
        f"probe_port={config.mac_discovery_tcp_port} probe_timeout={config.mac_discovery_probe_timeout:.2f}s "
        f"http_timeout={config.blind_discovery_http_timeout:.2f}s probe_workers={config.mac_discovery_max_workers} "
        f"identify_workers={config.blind_discovery_max_workers}"
    )

    candidates_by_ip: dict[str, SpeakerIdentity] = {}
    seed_identity = _probe_seed_identity(seed_ip, config, log, phase="full_scan_start")
    if seed_identity:
        total_identified_kef += 1
        candidates_by_ip[seed_identity.ip] = seed_identity
        if seed_identity.mac == normalized_known_mac:
            log.info(
                f"Seed identity matched the Target Speaker MAC | ip={seed_identity.ip} | "
                f"mac={seed_identity.mac_display or seed_identity.mac} | model={seed_identity.speaker_model} | "
                f"name={seed_identity.speaker_name or '<unnamed>'} "
                f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
            )
            return seed_identity.with_match("target_mac")

    probe_started = time.monotonic()
    hosts, reachable_hosts, identities = _scan_candidate_hosts(networks, seed_ip, config)
    total_reachable_hosts += len(reachable_hosts)
    total_identified_kef += len(identities)
    log.info(
        "Full scan networks finished | "
        f"networks={[str(n) for n in networks]} hosts={len(hosts)} "
        f"reachable_hosts_count={len(reachable_hosts)} identified_kef_count={len(identities)} "
        f"duration_ms={int((time.monotonic() - probe_started) * 1000)}"
    )

    for identity in identities:
        candidates_by_ip[identity.ip] = identity
        if normalized_known_mac and identity.mac == normalized_known_mac:
            log.info(
                f"Full scan matched the Target Speaker MAC | ip={identity.ip} | "
                f"mac={identity.mac_display or identity.mac} | model={identity.speaker_model} | "
                f"name={identity.speaker_name or '<unnamed>'} "
                f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
                f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
            )
            return identity.with_match("target_mac")

    if seed_ip and seed_ip not in candidates_by_ip:
        seed_identity = _probe_seed_identity(
            seed_ip,
            config,
            log,
            phase="full_scan_after_network_miss",
            delay_before=_SEED_IDENTITY_RETRY_DELAY_S,
        )
        if seed_identity:
            total_identified_kef += 1
            candidates_by_ip[seed_identity.ip] = seed_identity
            if seed_identity.mac == normalized_known_mac:
                log.info(
                    f"Seed retry matched the Target Speaker MAC | ip={seed_identity.ip} | "
                    f"mac={seed_identity.mac_display or seed_identity.mac} | model={seed_identity.speaker_model} | "
                    f"name={seed_identity.speaker_name or '<unnamed>'} "
                    f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
                )
                return seed_identity.with_match("target_mac")

    candidates = list(candidates_by_ip.values())
    if not candidates:
        log.info(
            f"Full scan did not find any supported KEF device | "
            f"known_mac={known_mac or '<empty>'} | seed_ip={seed_ip or '<empty>'} "
            f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
            f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
        )
        return None

    if len(candidates) == 1:
        candidate = candidates[0]
        if candidate.mac != normalized_known_mac:
            log.info(
                f"Full scan found one KEF device, but its MAC did not match the target | "
                f"ip={candidate.ip} | target_mac={known_mac or '<empty>'} | "
                f"actual_mac={candidate.mac_display or candidate.mac or '<no-mac>'} "
                f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
                f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
            )
            return None

        log.info(
            f"Full scan found exactly one supported KEF device | ip={candidate.ip} | "
            f"mac={candidate.mac_display or candidate.mac} | model={candidate.speaker_model} | "
            f"name={candidate.speaker_name or '<unnamed>'} "
            f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
            f"duration_ms={int((time.monotonic() - scan_started) * 1000)}"
        )
        return candidate.with_match("target_mac")

    summary = ", ".join(_candidate_summary(candidate) for candidate in candidates)
    log.info(
        f"Full scan found multiple KEF devices and could not pick one safely | "
        f"reachable_hosts_count={total_reachable_hosts} identified_kef_count={total_identified_kef} "
        f"duration_ms={int((time.monotonic() - scan_started) * 1000)} | candidates=[{summary}]"
    )
    return None


def discover_ip_by_mac(target_mac: str, seed_ip: Optional[str], _config: AppConfig, log) -> Optional[str]:
    normalized_target = normalize_mac(target_mac)
    if not normalized_target:
        return None

    arp_map = read_arp_table(log)
    for ip, mac in arp_map.items():
        if mac == normalized_target:
            log.info(
                f"Recovered the speaker IP from the existing ARP cache | "
                f"target_primary_mac={target_mac} | ip={ip}"
            )
            return ip

    seed_arp_mac = arp_map.get(seed_ip, "") if seed_ip else ""
    log.info(
        f"Existing ARP cache did not match the Target Speaker primary MAC | "
        f"target_primary_mac={target_mac} | seed_ip={seed_ip or '<empty>'} "
        f"seed_arp_mac={seed_arp_mac or '<empty>'} "
        f"note=arp_mac_may_differ_from_kef_primary_mac; falling_back_to_http_full_scan"
    )
    return None
