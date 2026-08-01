from __future__ import annotations

import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, TypeVar

from ...config import AppConfig
from ...structured_logging import log_structured
from ..speaker_models import SpeakerIdentity, normalize_mac
from .identity import identify_kef_device
from .network import build_candidate_networks, probe_ip_port, read_arp_table

_T = TypeVar("_T")
_SEED_IDENTITY_HTTP_TIMEOUT_FLOOR_S = 1.50
_SEED_IDENTITY_RETRY_DELAY_S = 0.35


def _candidate_summary(candidate: SpeakerIdentity) -> str:
    return (
        f"{candidate.ip}:{candidate.speaker_model or '?'}:{candidate.speaker_name or '<unnamed>'}:"
        f"{candidate.mac_display or candidate.mac or '<no-mac>'}"
    )


def _scan_log(
    log,
    tag: str,
    *,
    action: str,
    reason: str,
    trigger: str | None = None,
    **fields: object,
) -> None:
    """Write discovery diagnostics in the shared application log format."""
    log_structured(log, tag, action=action, reason=reason, trigger=trigger, **fields)


def _sort_identity_key(identity: SpeakerIdentity) -> tuple[int, str]:
    try:
        return (int(ipaddress.IPv4Address(identity.ip)), identity.speaker_name.casefold())
    except ipaddress.AddressValueError:
        return (0, identity.speaker_name.casefold())


# Submit every host at once and harvest completions as they arrive, so one
# slow probe only delays its own worker instead of a whole batch. Results are
# keyed by host and re-read in the caller's host order to keep output stable.
def _map_hosts_concurrently(
    executor: ThreadPoolExecutor,
    hosts: list[str],
    worker: Callable[[str], _T],
    *,
    should_continue: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> dict[str, _T]:
    futures = {executor.submit(worker, host_ip): host_ip for host_ip in hosts}
    results: dict[str, _T] = {}
    completed = 0
    for future in as_completed(futures):
        if should_continue is not None and not should_continue():
            for pending in futures:
                pending.cancel()
            break
        completed += 1
        if on_progress is not None:
            try:
                on_progress(completed)
            except Exception:
                pass
        try:
            results[futures[future]] = future.result()
        except Exception:
            continue
    return results


def _reachable_hosts(
    executor: ThreadPoolExecutor,
    hosts: list[str],
    config: AppConfig,
    *,
    should_continue: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> list[str]:
    results = _map_hosts_concurrently(
        executor,
        hosts,
        lambda host_ip: probe_ip_port(host_ip, config.mac_discovery_tcp_port, config.mac_discovery_probe_timeout),
        should_continue=should_continue,
        on_progress=on_progress,
    )
    return [host_ip for host_ip in hosts if results.get(host_ip)]


def _identify_hosts(
    executor: ThreadPoolExecutor,
    hosts: list[str],
    config: AppConfig,
    *,
    should_continue: Optional[Callable[[], bool]] = None,
) -> list[SpeakerIdentity]:
    results = _map_hosts_concurrently(
        executor,
        hosts,
        lambda host_ip: identify_kef_device(host_ip, config),
        should_continue=should_continue,
    )
    return [results[host_ip] for host_ip in hosts if results.get(host_ip)]


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
    *,
    should_continue: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> tuple[list[str], list[str], list[SpeakerIdentity]]:
    hosts = _candidate_hosts(networks, seed_ip)
    if not hosts:
        return [], [], []
    if should_continue is not None and not should_continue():
        return hosts, [], []

    with ThreadPoolExecutor(max_workers=_shared_discovery_workers(len(hosts), config)) as executor:
        reachable_hosts = _reachable_hosts(
            executor, hosts, config, should_continue=should_continue, on_progress=on_progress
        )
        identities = (
            _identify_hosts(executor, reachable_hosts, config, should_continue=should_continue)
            if reachable_hosts and (should_continue is None or should_continue())
            else []
        )
    return hosts, reachable_hosts, identities


def _log_network_scan_finished(
    log,
    *,
    scan_kind: str,
    network: ipaddress.IPv4Network,
    network_index: int,
    network_count: int,
    hosts: list[str],
    reachable_hosts: list[str],
    identities: list[SpeakerIdentity],
    started_mono: float,
    action: str,
    reason: str,
) -> None:
    _scan_log(
        log,
        "STEP",
        action=action,
        reason=reason,
        trigger="network_scan",
        step="network_finished",
        status=scan_kind.lower(),
        network=network,
        network_index=f"{network_index}/{network_count}",
        hosts_count=len(hosts),
        reachable_hosts_count=len(reachable_hosts),
        identified_kef_count=len(identities),
        duration_ms=int((time.monotonic() - started_mono) * 1000),
    )


def _probe_seed_identity(
    seed_ip: Optional[str],
    config: AppConfig,
    log,
    *,
    phase: str,
    action: str,
    reason: str,
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
        _scan_log(
            log,
            "STEP",
            action=action,
            reason=reason,
            trigger=phase,
            step="seed_identity_probe",
            status="found",
            seed_ip=seed_ip,
            timeout_s=f"{timeout:.2f}",
            duration_ms=duration_ms,
            candidate=_candidate_summary(identity),
        )
        return identity

    _scan_log(
        log,
        "STEP",
        action=action,
        reason=reason,
        trigger=phase,
        step="seed_identity_probe",
        status="not_found",
        seed_ip=seed_ip,
        timeout_s=f"{timeout:.2f}",
        duration_ms=duration_ms,
    )
    return None


def _notify_candidate(
    on_candidate: Optional[Callable[[SpeakerIdentity], None]],
    identity: SpeakerIdentity,
    log,
    *,
    action: str,
    reason: str,
) -> None:
    if on_candidate is None:
        return
    try:
        on_candidate(identity)
    except Exception as exc:
        _scan_log(
            log,
            "WARN",
            action=action,
            reason=reason,
            trigger="candidate_callback",
            cause="callback_failed",
            actual_ip=identity.ip or "<empty>",
            error=repr(exc),
        )


def discover_kef_devices(
    seed_ip: Optional[str],
    config: AppConfig,
    log,
    *,
    on_candidate: Optional[Callable[[SpeakerIdentity], None]] = None,
    should_continue: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> list[SpeakerIdentity]:
    action = "MANUAL_SCAN"
    reason = "manual_request"
    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        _scan_log(log, "SKIP", action=action, reason=reason, cause="no_scan_networks")
        return []

    scan_started = time.monotonic()
    total_reachable_hosts = 0
    total_identified_kef = 0
    _scan_log(
        log,
        "BEGIN",
        action=action,
        reason=reason,
        seed_ip=seed_ip or "<empty>",
        networks=",".join(str(network) for network in networks),
        probe_port=config.mac_discovery_tcp_port,
        probe_timeout_s=f"{config.mac_discovery_probe_timeout:.2f}",
        http_timeout_s=f"{config.blind_discovery_http_timeout:.2f}",
        probe_workers=config.mac_discovery_max_workers,
        identify_workers=config.blind_discovery_max_workers,
    )

    candidates_by_ip: dict[str, SpeakerIdentity] = {}
    seed_identity = _probe_seed_identity(
        seed_ip, config, log, phase="manual_scan_start", action=action, reason=reason
    )
    if seed_identity:
        total_identified_kef += 1
        candidates_by_ip[seed_identity.ip] = seed_identity
        _notify_candidate(on_candidate, seed_identity, log, action=action, reason=reason)

    probe_started = time.monotonic()
    total_hosts = 0
    for index, network in enumerate(networks, start=1):
        if should_continue is not None and not should_continue():
            break
        network_started = time.monotonic()
        checked_before_network = total_hosts
        hosts, reachable_hosts, identities = _scan_candidate_hosts(
            [network],
            seed_ip,
            config,
            should_continue=should_continue,
            on_progress=(
                (lambda done: on_progress(checked_before_network + done))
                if on_progress is not None
                else None
            ),
        )
        total_hosts += len(hosts)
        total_reachable_hosts += len(reachable_hosts)
        total_identified_kef += len(identities)
        for identity in identities:
            candidates_by_ip[identity.ip] = identity
            _notify_candidate(on_candidate, identity, log, action=action, reason=reason)
        _log_network_scan_finished(
            log,
            scan_kind="Manual",
            network=network,
            network_index=index,
            network_count=len(networks),
            hosts=hosts,
            reachable_hosts=reachable_hosts,
            identities=identities,
            started_mono=network_started,
            action=action,
            reason=reason,
        )

    _scan_log(
        log,
        "STEP",
        action=action,
        reason=reason,
        step="networks_finished",
        status="completed",
        networks=",".join(str(network) for network in networks),
        hosts_count=total_hosts,
        reachable_hosts_count=total_reachable_hosts,
        identified_kef_count=total_identified_kef,
        duration_ms=int((time.monotonic() - probe_started) * 1000),
    )

    if seed_ip and seed_ip not in candidates_by_ip and (should_continue is None or should_continue()):
        seed_identity = _probe_seed_identity(
            seed_ip,
            config,
            log,
            phase="manual_scan_after_network_miss",
            action=action,
            reason=reason,
            delay_before=_SEED_IDENTITY_RETRY_DELAY_S,
        )
        if seed_identity:
            total_identified_kef += 1
            candidates_by_ip[seed_identity.ip] = seed_identity
            _notify_candidate(on_candidate, seed_identity, log, action=action, reason=reason)

    candidates = sorted(candidates_by_ip.values(), key=_sort_identity_key)
    summary = ", ".join(_candidate_summary(candidate) for candidate in candidates) or "<none>"
    _scan_log(
        log,
        "END",
        action=action,
        reason=reason,
        outcome="candidates_found" if candidates else "no_candidates",
        count=len(candidates),
        reachable_hosts_count=total_reachable_hosts,
        identified_kef_count=total_identified_kef,
        duration_ms=int((time.monotonic() - scan_started) * 1000),
        candidates=summary,
    )
    return candidates


def discover_kef_device_blind(
    known_mac: str,
    seed_ip: Optional[str],
    config: AppConfig,
    log,
    *,
    should_continue: Optional[Callable[[], bool]] = None,
) -> Optional[SpeakerIdentity]:
    action = "TARGET_SCAN"
    reason = "target_recovery"
    normalized_known_mac = normalize_mac(known_mac)
    if not normalized_known_mac:
        _scan_log(log, "SKIP", action=action, reason=reason, cause="empty_target_mac")
        return None

    networks = build_candidate_networks(seed_ip, config, log)
    if not networks:
        _scan_log(log, "SKIP", action=action, reason=reason, cause="no_scan_networks")
        return None

    scan_started = time.monotonic()
    total_reachable_hosts = 0
    total_identified_kef = 0
    _scan_log(
        log,
        "BEGIN",
        action=action,
        reason=reason,
        target_mac=normalized_known_mac or "<empty>",
        seed_ip=seed_ip or "<empty>",
        networks=",".join(str(network) for network in networks),
        probe_port=config.mac_discovery_tcp_port,
        probe_timeout_s=f"{config.mac_discovery_probe_timeout:.2f}",
        http_timeout_s=f"{config.blind_discovery_http_timeout:.2f}",
        probe_workers=config.mac_discovery_max_workers,
        identify_workers=config.blind_discovery_max_workers,
    )

    if should_continue is not None and not should_continue():
        _scan_log(log, "SKIP", action=action, reason=reason, cause="cancelled_before_seed_probe")
        return None

    candidates_by_ip: dict[str, SpeakerIdentity] = {}
    seed_identity = _probe_seed_identity(seed_ip, config, log, phase="full_scan_start", action=action, reason=reason)
    if seed_identity and (should_continue is None or should_continue()):
        total_identified_kef += 1
        candidates_by_ip[seed_identity.ip] = seed_identity
        if seed_identity.mac == normalized_known_mac:
            _scan_log(
                log,
                "END",
                action=action,
                reason=reason,
                trigger="full_scan_start",
                outcome="target_matched_seed",
                actual_ip=seed_identity.ip,
                actual_mac=seed_identity.mac_display or seed_identity.mac,
                actual_speaker_model=seed_identity.speaker_model,
                actual_speaker_name=seed_identity.speaker_name or "<unnamed>",
                duration_ms=int((time.monotonic() - scan_started) * 1000),
            )
            return seed_identity.with_match("target_mac")

    probe_started = time.monotonic()
    total_hosts = 0
    for index, network in enumerate(networks, start=1):
        if should_continue is not None and not should_continue():
            break
        network_started = time.monotonic()
        hosts, reachable_hosts, identities = _scan_candidate_hosts(
            [network],
            seed_ip,
            config,
            should_continue=should_continue,
        )
        total_hosts += len(hosts)
        total_reachable_hosts += len(reachable_hosts)
        total_identified_kef += len(identities)
        _log_network_scan_finished(
            log,
            scan_kind="Full",
            network=network,
            network_index=index,
            network_count=len(networks),
            hosts=hosts,
            reachable_hosts=reachable_hosts,
            identities=identities,
            started_mono=network_started,
            action=action,
            reason=reason,
        )

        if should_continue is not None and not should_continue():
            break
        for identity in identities:
            if should_continue is not None and not should_continue():
                break
            candidates_by_ip[identity.ip] = identity
            if normalized_known_mac and identity.mac == normalized_known_mac:
                _scan_log(
                    log,
                    "END",
                    action=action,
                    reason=reason,
                    trigger="network_scan",
                    outcome="target_matched_network",
                    actual_ip=identity.ip,
                    actual_mac=identity.mac_display or identity.mac,
                    actual_speaker_model=identity.speaker_model,
                    actual_speaker_name=identity.speaker_name or "<unnamed>",
                    reachable_hosts_count=total_reachable_hosts,
                    identified_kef_count=total_identified_kef,
                    duration_ms=int((time.monotonic() - scan_started) * 1000),
                )
                return identity.with_match("target_mac")

    _scan_log(
        log,
        "STEP",
        action=action,
        reason=reason,
        step="networks_finished",
        status="completed",
        networks=",".join(str(network) for network in networks),
        hosts_count=total_hosts,
        reachable_hosts_count=total_reachable_hosts,
        identified_kef_count=total_identified_kef,
        duration_ms=int((time.monotonic() - probe_started) * 1000),
    )

    if seed_ip and seed_ip not in candidates_by_ip and (should_continue is None or should_continue()):
        seed_identity = _probe_seed_identity(
            seed_ip,
            config,
            log,
            phase="full_scan_after_network_miss",
            action=action,
            reason=reason,
            delay_before=_SEED_IDENTITY_RETRY_DELAY_S,
        )
        if seed_identity:
            total_identified_kef += 1
            candidates_by_ip[seed_identity.ip] = seed_identity
            if seed_identity.mac == normalized_known_mac:
                _scan_log(
                    log,
                    "END",
                    action=action,
                    reason=reason,
                    trigger="full_scan_after_network_miss",
                    outcome="target_matched_seed_retry",
                    actual_ip=seed_identity.ip,
                    actual_mac=seed_identity.mac_display or seed_identity.mac,
                    actual_speaker_model=seed_identity.speaker_model,
                    actual_speaker_name=seed_identity.speaker_name or "<unnamed>",
                    duration_ms=int((time.monotonic() - scan_started) * 1000),
                )
                return seed_identity.with_match("target_mac")

    candidates = list(candidates_by_ip.values())
    if not candidates:
        _scan_log(
            log,
            "END",
            action=action,
            reason=reason,
            outcome="no_supported_device",
            target_mac=normalized_known_mac or "<empty>",
            seed_ip=seed_ip or "<empty>",
            reachable_hosts_count=total_reachable_hosts,
            identified_kef_count=total_identified_kef,
            duration_ms=int((time.monotonic() - scan_started) * 1000),
        )
        return None

    # Every MAC-matching candidate already returned above, so whatever is left
    # does not match the target and is only useful as a diagnostic summary.
    summary = ", ".join(_candidate_summary(candidate) for candidate in candidates)
    _scan_log(
        log,
        "END",
        action=action,
        reason=reason,
        outcome="target_not_matched",
        count=len(candidates),
        target_mac=normalized_known_mac or "<empty>",
        reachable_hosts_count=total_reachable_hosts,
        identified_kef_count=total_identified_kef,
        duration_ms=int((time.monotonic() - scan_started) * 1000),
        candidates=summary,
    )
    return None


def discover_ip_by_mac(target_mac: str, seed_ip: Optional[str], _config: AppConfig, log) -> Optional[str]:
    action = "ARP_CACHE_LOOKUP"
    reason = "target_recovery"
    normalized_target = normalize_mac(target_mac)
    if not normalized_target:
        return None

    arp_map = read_arp_table(log)
    for ip, mac in arp_map.items():
        if mac == normalized_target:
            _scan_log(
                log,
                "END",
                action=action,
                reason=reason,
                outcome="target_matched",
                target_mac=normalized_target,
                actual_ip=ip,
            )
            return ip

    seed_arp_mac = arp_map.get(seed_ip, "") if seed_ip else ""
    _scan_log(
        log,
        "END",
        action=action,
        reason=reason,
        outcome="target_not_matched",
        target_mac=normalized_target,
        seed_ip=seed_ip or "<empty>",
        actual_mac=seed_arp_mac or "<empty>",
        note="arp_mac_may_differ_from_kef_primary_mac;falling_back_to_http_full_scan",
    )
    return None
