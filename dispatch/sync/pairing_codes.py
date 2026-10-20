"""Short-lived, single-use pairing authorization for YouTube credential transfer."""
import hashlib
import secrets
import threading
import time
from collections import defaultdict
from typing import DefaultDict, List

PAIRING_CODE_TTL_SECONDS = 300
PAIRING_ATTEMPT_WINDOW_SECONDS = 60
PAIRING_MAX_ATTEMPTS = 5
PAIRING_LOCKOUT_SECONDS = 300

_lock = threading.Lock()
_active_code_digest: str = ""
_active_code_expires_at = 0.0
_failed_attempts: DefaultDict[str, List[float]] = defaultdict(list)
_lockouts: dict[str, float] = {}


class PairingCodeError(Exception):
    """Pairing authorization failure with a safe HTTP status and message."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_pairing_code() -> tuple[str, int]:
    """Replace any outstanding code with a new random 4-digit single-use code."""
    global _active_code_digest, _active_code_expires_at
    code = f"{secrets.randbelow(10_000):04d}"
    with _lock:
        _active_code_digest = _digest(code)
        _active_code_expires_at = time.monotonic() + PAIRING_CODE_TTL_SECONDS
    return code, PAIRING_CODE_TTL_SECONDS


def consume_pairing_code(code: str, client_ip: str) -> None:
    """Validate and consume the active code, rate-limiting invalid attempts per IP."""
    global _active_code_digest, _active_code_expires_at
    now = time.monotonic()
    address = client_ip or "unknown"
    with _lock:
        lockout_until = _lockouts.get(address, 0.0)
        if now < lockout_until:
            remaining = max(1, int(lockout_until - now))
            raise PairingCodeError(429, f"Too many incorrect pairing attempts. Retry in {remaining} seconds.")
        attempts = [
            timestamp for timestamp in _failed_attempts.get(address, [])
            if now - timestamp < PAIRING_ATTEMPT_WINDOW_SECONDS
        ]
        _failed_attempts[address] = attempts

        valid_shape = len(code) == 4 and code.isdigit()
        matches = valid_shape and secrets.compare_digest(_digest(code), _active_code_digest)
        not_expired = now < _active_code_expires_at
        if not (matches and not_expired and _active_code_digest):
            attempts.append(now)
            _failed_attempts[address] = attempts
            if len(attempts) >= PAIRING_MAX_ATTEMPTS:
                _lockouts[address] = now + PAIRING_LOCKOUT_SECONDS
                _failed_attempts.pop(address, None)
            if _active_code_digest and not not_expired:
                _active_code_digest = ""
                _active_code_expires_at = 0.0
            raise PairingCodeError(401, "Pairing code is invalid, expired, or already used. Create a new code on the PC.")

        _active_code_digest = ""
        _active_code_expires_at = 0.0
        _failed_attempts.pop(address, None)
        _lockouts.pop(address, None)


def reset_pairing_codes_for_tests() -> None:
    """Reset in-memory pairing authorization state for isolated tests."""
    global _active_code_digest, _active_code_expires_at
    with _lock:
        _active_code_digest = ""
        _active_code_expires_at = 0.0
        _failed_attempts.clear()
        _lockouts.clear()
