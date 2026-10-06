"""Transport Abstraction & Remote Connectivity Manager.
Enables seamless switching between local LAN Wi-Fi, Tailscale private network,
and custom remote endpoints with automatic latency probing, failover, and policy enforcement.
"""
import time
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
import requests

logger = logging.getLogger("dispatch.transport")


@dataclass
class EndpointCandidate:
    name: str              # "lan", "tailscale", "custom"
    url: str               # "http://192.168.1.15:8765" or "http://laptop.tailnet.ts.net:8765"
    priority: int = 1      # 1 (LAN, fastest direct path), 2 (Tailscale), 3 (Custom)
    is_reachable: bool = False
    latency_ms: float = 9999.0
    last_probed: float = 0.0


@dataclass
class NetworkPolicy:
    wifi_only: bool = True
    allow_cellular: bool = False
    pause_on_roaming: bool = True
    max_cellular_mb_per_session: float = 200.0


class TransportManager:
    """Manages multi-path connectivity between mobile phone and home laptop."""

    def __init__(
        self,
        auth_token: str,
        lan_url: Optional[str] = None,
        tailscale_url: Optional[str] = None,
        custom_url: Optional[str] = None,
        policy: Optional[NetworkPolicy] = None
    ):
        self.auth_token = auth_token
        self.policy = policy or NetworkPolicy()
        self.candidates: List[EndpointCandidate] = []

        if lan_url:
            self.candidates.append(EndpointCandidate(name="lan", url=lan_url.rstrip("/"), priority=1))
        if tailscale_url:
            self.candidates.append(EndpointCandidate(name="tailscale", url=tailscale_url.rstrip("/"), priority=2))
        if custom_url:
            self.candidates.append(EndpointCandidate(name="custom", url=custom_url.rstrip("/"), priority=3))

    def add_candidate(self, name: str, url: str, priority: int = 2):
        self.candidates.append(EndpointCandidate(name=name, url=url.rstrip("/"), priority=priority))

    def probe_single_endpoint(self, candidate: EndpointCandidate, timeout_seconds: float = 2.0) -> bool:
        """Pings a single endpoint and measures network roundtrip latency."""
        ping_url = f"{candidate.url}/api/sync/ping"
        start = time.perf_counter()
        try:
            resp = requests.get(ping_url, timeout=timeout_seconds)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if resp.status_code == 200 and resp.json().get("status") == "online":
                candidate.is_reachable = True
                candidate.latency_ms = round(elapsed_ms, 1)
                candidate.last_probed = time.time()
                logger.debug("Candidate %s (%s) ONLINE: %.1fms", candidate.name, candidate.url, elapsed_ms)
                return True
        except Exception as e:
            logger.debug("Candidate %s (%s) UNREACHABLE: %s", candidate.name, candidate.url, e)

        candidate.is_reachable = False
        candidate.latency_ms = 9999.0
        candidate.last_probed = time.time()
        return False

    def probe_all_endpoints(self, timeout_seconds: float = 2.0) -> List[EndpointCandidate]:
        """Probes all configured endpoint candidates and updates reachability."""
        for c in self.candidates:
            self.probe_single_endpoint(c, timeout_seconds=timeout_seconds)
        return self.candidates

    def select_best_endpoint(self, auto_probe: bool = True) -> Optional[EndpointCandidate]:
        """Selects the optimal reachable endpoint, prioritizing LAN over Tailscale, then lowest latency."""
        if auto_probe:
            self.probe_all_endpoints()

        reachable = [c for c in self.candidates if c.is_reachable]
        if not reachable:
            return None

        # Sort by priority first (1=LAN, 2=Tailscale), then by latency
        reachable.sort(key=lambda c: (c.priority, c.latency_ms))
        best = reachable[0]
        logger.info("Selected optimal route: %s (%s, %.1fms)", best.name, best.url, best.latency_ms)
        return best

    def evaluate_network_policy(
        self,
        network_type: str,  # "wifi", "cellular", "ethernet", "none"
        is_roaming: bool = False
    ) -> Tuple[bool, str]:
        """Evaluates whether current phone network conditions permit syncing under policy.
        
        Returns:
            (is_allowed: bool, reason: str)
        """
        net = network_type.lower()

        if net in ("none", "disconnected", "offline"):
            return False, "No active network interface on device"

        if is_roaming and self.policy.pause_on_roaming:
            return False, "Data sync paused while device is roaming"

        if net in ("wifi", "ethernet"):
            return True, f"High-speed unmetered connection ({net}) allowed"

        if net in ("cellular", "mobile", "4g", "5g"):
            if not self.policy.allow_cellular:
                return False, "Mobile cellular sync is disabled by policy (Wi-Fi only)"
            return True, "Cellular connection permitted under policy"

        return False, f"Unknown network interface type: {network_type}"

    def get_auth_headers(self) -> Dict[str, str]:
        """Constructs secure device authentication headers."""
        return {
            "X-Dispatch-Device-Token": self.auth_token,
            "X-Dispatch-Client": "DispatchMobile/1.0"
        }
