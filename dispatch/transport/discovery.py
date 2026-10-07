"""Autonomous UDP Zero-Configuration Beacon & Discovery Service for Dispatch.
Enables instant pairing between mobile phone and PC without manual IP configuration.

Functions:
- Responds to 'DISPATCH_DISCOVER' UDP requests with PC LAN URL and pairing credentials.
- Periodically broadcasts discovery beacons on local subnet (port 8765).
"""
import json
import time
import socket
import logging
import threading
from typing import Optional

from dispatch.config import WEB_PORT
from dispatch.sync.receiver import get_auth_token

logger = logging.getLogger("dispatch.discovery")

DISCOVERY_PORT = 8765
DISCOVER_MAGIC = b"DISPATCH_DISCOVER"
BEACON_MAGIC = "DISPATCH_ANNOUNCE"
SERVICE_VERSION = "1.0.0"


def get_local_lan_ip() -> str:
    """Detect the active LAN IPv4 address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't actually send data, just connects to determine outbound interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class DiscoveryBeaconServer:
    """UDP listener and broadcaster for zero-config phone-to-PC pairing."""

    def __init__(self, port: int = DISCOVERY_PORT):
        self.port = port
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._beacon_thread: Optional[threading.Thread] = None

    def start(self):
        """Start UDP discovery listener and background beacon broadcaster."""
        if self.running:
            return
        self.running = True

        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="UDP-Discovery-Listener")
        self._thread.start()

        self._beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True, name="UDP-Beacon-Broadcaster")
        self._beacon_thread.start()

        logger.info("Autonomous UDP Discovery Service active on port %d", self.port)

    def stop(self):
        """Stop UDP discovery."""
        self.running = False

    def _get_payload(self) -> dict:
        """Construct UDP discovery announcement payload.
        SECURITY REQUIREMENT: Must NEVER broadcast plaintext auth_token, PIN, or credentials.
        Advertises server presence so Android discovery can identify the server without intercepting secret tokens.
        """
        lan_ip = get_local_lan_ip()
        return {
            "magic": BEACON_MAGIC,
            "service": "dispatch",
            "version": SERVICE_VERSION,
            "lan_url": f"http://{lan_ip}:{WEB_PORT}",
            "ip": lan_ip,
            "port": WEB_PORT,
            "hostname": socket.gethostname(),
            "pairing_required": True,
        }

    def _listen_loop(self):
        """Listen for incoming DISPATCH_DISCOVER UDP datagrams and reply directly."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(1.0)

            while self.running:
                try:
                    data, addr = sock.recvfrom(1024)
                    if DISCOVER_MAGIC in data:
                        payload = self._get_payload()
                        resp_bytes = json.dumps(payload).encode("utf-8")
                        sock.sendto(resp_bytes, addr)
                        logger.info("Answered UDP discovery query from %s:%d -> %s", addr[0], addr[1], payload["lan_url"])
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.debug("UDP listen error: %s", e)
        except Exception as e:
            logger.error("Failed to bind UDP discovery listener on port %d: %s", self.port, e)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _beacon_loop(self):
        """Periodically broadcast UDP announcement beacon across LAN."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            while self.running:
                try:
                    payload = self._get_payload()
                    msg = json.dumps(payload).encode("utf-8")

                    # Broadcast to global broadcast
                    sock.sendto(msg, ("255.255.255.255", self.port))

                    # Also broadcast to subnet broadcast (e.g. 192.168.0.255)
                    lan_ip = payload.get("ip", "")
                    if "." in lan_ip:
                        parts = lan_ip.split(".")
                        if len(parts) == 4:
                            subnet_bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                            sock.sendto(msg, (subnet_bcast, self.port))
                except Exception as e:
                    logger.debug("UDP beacon send error: %s", e)

                # Sleep 2.5 seconds between announcement beacons
                for _ in range(25):
                    if not self.running:
                        break
                    time.sleep(0.1)
        except Exception as e:
            logger.error("UDP beacon broadcaster error: %s", e)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
