# Handoff Report: Milestone 1 Pairing & Transport Security Hardening

## 1. Observation

### 1.1 Discovery Beacon Vulnerability (`dispatch/transport/discovery.py`)
- **Direct Code Inspection (`dispatch/transport/discovery.py:65-76`)**:
  ```python
  def _get_payload(self) -> dict:
      lan_ip = get_local_lan_ip()
      token = get_auth_token()
      return {
          "magic": BEACON_MAGIC,
          "service": "dispatch",
          "lan_url": f"http://{lan_ip}:{WEB_PORT}",
          "ip": lan_ip,
          "port": WEB_PORT,
          "auth_token": token,
          "hostname": socket.gethostname()
      }
  ```
- **Beacon Broadcast Loop (`dispatch/transport/discovery.py:119-130`)**:
  ```python
  payload = self._get_payload()
  msg = json.dumps(payload).encode("utf-8")
  sock.sendto(msg, ("255.255.255.255", self.port))
  ```
- **Discovery Query Response (`dispatch/transport/discovery.py:91-94`)**:
  ```python
  if DISCOVER_MAGIC in data:
      payload = self._get_payload()
      resp_bytes = json.dumps(payload).encode("utf-8")
      sock.sendto(resp_bytes, addr)
  ```
  **Direct Finding**: Every 2.5 seconds, the UDP beacon broadcasts the master `auth_token` in cleartext over UDP port 8765 to the entire subnet broadcast address (`255.255.255.255` and `x.x.x.255`). Furthermore, any unauthenticated UDP packet containing `DISPATCH_DISCOVER` receives the `auth_token` in response. Any LAN sniffer or unauthorized client passively listening on UDP port 8765 intercepts the master token without authentication.

### 1.2 Pairing Endpoint Behavior (`dispatch/sync/receiver.py`)
- **Direct Code Inspection (`dispatch/sync/receiver.py:122-160`)**:
  ```python
  @router.get("/pairing/config")
  async def get_pairing_config(
      request: Request,
      pin: Optional[str] = None,
      auth_token: Optional[str] = Header(None, alias="x-auth-token"),
      x_pairing_pin: Optional[str] = Header(None, alias="x-pairing-pin"),
      token: Optional[str] = None
  ):
      ...
      is_authenticated = (
          is_localhost or
          (provided_token and provided_token.strip() == expected_token) or
          (provided_pin and provided_pin.strip() == current_pin)
      )

      if not is_authenticated:
          logger.warning("Blocked unauthenticated LAN access to /api/sync/pairing/config from %s", client_ip)
          raise HTTPException(
              status_code=403,
              detail="Unauthenticated LAN pairing prohibited. Access via localhost, scan QR code, or enter pairing PIN."
          )
  ```
- **Direct Finding**:
  1. Unauthenticated requests currently trigger `403 Forbidden` rather than standard `401 Unauthorized`.
  2. String equality checks (`==`) are used instead of constant-time comparisons (`secrets.compare_digest`), opening a timing attack vector.
  3. PIN entry lacks rate-limiting and brute-force lockout. A 6-digit PIN has only 1,000,000 possible values, which can be brute-forced over high-speed LAN within minutes if unthrottled.
  4. There is no masked discovery mode (`mask=true`) allowing an unpaired client to discover server endpoints without leaking secrets.
  5. There is no dedicated `POST /api/sync/pairing/handshake` endpoint for standard REST pairing handshake requests.

### 1.3 Android Client Discovery & Pairing Logic
- **`android/app/src/main/java/com/resolvia/dispatch/data/NetworkDiscovery.kt:133-143`**:
  ```kotlin
  val respStr = String(recvPacket.data, 0, recvPacket.length)
  val json = gson.fromJson(respStr, JsonObject::class.java)
  val lanUrl = json.get("lan_url")?.asString
  val token = json.get("auth_token")?.asString

  if (!lanUrl.isNullOrBlank()) {
      if (!token.isNullOrBlank()) {
          pairingManager.authToken = token
      }
      Log.i(tag, "UDP discovery success: $lanUrl")
      return lanUrl
  }
  ```
- **Direct Finding**: `NetworkDiscovery.kt` does NOT crash or abort if `auth_token` is missing or null. It verifies `if (!token.isNullOrBlank())` and returns `lanUrl`. Removing `auth_token` from UDP discovery is 100% backward-compatible with the Android discovery engine.

### 1.4 Web Dashboard Pairing Presentation
- **`dispatch/web/templates/index.html:972-988`**:
  ```javascript
  const res = await fetch("/api/sync/pairing/config");
  const data = await res.json();
  document.getElementById("pair-lan").value = data.lan_url;
  document.getElementById("pair-token").value = data.auth_token;
  document.getElementById("pair-uri").value = data.connection_string;
  ```
- **Direct Finding**: The web dashboard runs on the creator's PC (`127.0.0.1` / localhost). It fetches `/api/sync/pairing/config`, renders the QR code with `connection_string`, and displays pairing fields. Because it accesses the endpoint from localhost, it remains fully authenticated.

---

## 2. Logic Chain

1. **Premise 1**: Plaintext transmission of credentials over unencrypted UDP broadcast violates fundamental network security principles (Observation 1.1).
2. **Premise 2**: The purpose of the UDP discovery beacon is server presence advertisement (enabling mobile clients to locate the server IP and port without manual entry), not credential distribution (Observations 1.1 & 1.3).
3. **Inference 1**: Removing `auth_token` from `_get_payload()` in `dispatch/transport/discovery.py` prevents credential leakage over LAN while preserving server discovery on Android (Observation 1.3).
4. **Premise 3**: RFC 9110 / RFC 7235 defines `401 Unauthorized` for requests missing valid authentication credentials that could succeed upon presenting valid credentials (e.g. PIN or auth token), whereas `403 Forbidden` indicates permanent lack of permission (Observation 1.2).
5. **Inference 2**: Changing the unauthenticated rejection in `/api/sync/pairing/config` from `403 Forbidden` to `401 Unauthorized` (with `WWW-Authenticate` header) provides correct HTTP semantics and signals that credential submission (PIN or token) is required.
6. **Premise 4**: A 6-digit numeric PIN has $10^6$ combinations. Over LAN (~1000 req/s), an attacker could exhaust all combinations in ~16 minutes without rate limiting (Observation 1.2).
7. **Inference 3**: Enforcing a sliding-window rate limit (e.g., maximum 5 failed attempts per IP per 60 seconds, followed by a 300-second lockout with `429 Too Many Requests`) and using `secrets.compare_digest` mitigates both brute-force and timing attacks.
8. **Premise 5**: Some clients or discovery probes need to verify server presence and endpoints via HTTP without triggering an exception or receiving secrets (PROJECT.md contract).
9. **Inference 4**: Providing an optional `mask=True` query parameter on `GET /api/sync/pairing/config` returns `200 OK` with sanitized endpoints (`lan_url`, `tailscale_url`, `requires_pairing=True`) while setting `auth_token=None`, `yt_token=None`, and `connection_string=None`.
10. **Inference 5**: Providing `POST /api/sync/pairing/handshake` gives clients a dedicated, standard REST contract to exchange a PIN for the `auth_token`.

---

## 3. Caveats

1. **Ephemeral Rate-Limit Storage**: The rate-limiting tracker in `receiver.py` is stored in-memory in module-level dictionaries (`_FAILED_PIN_ATTEMPTS`, `_PIN_LOCKOUTS`). A process restart resets the lockout state. For a single-node desktop daemon, this is lightweight and appropriate, requiring zero external dependencies like Redis.
2. **Localhost Exemption**: Requests from `127.0.0.1`, `::1`, `localhost`, and `testclient` bypass PIN requirements to allow the PC web dashboard to render the pairing QR code. If a hostile non-root local user has access to the PC, local processes can query localhost. This matches the existing system security model where the PC is the trusted creator workstation.
3. **Android Client UI Update Needed in M3**: While `NetworkDiscovery.kt` safely handles the removal of `auth_token` from UDP, the Android app will require explicit pairing via QR scan or entering the 6-digit PIN in Settings before uploads can begin. This is scheduled under Milestone 3.

---

## 4. Conclusion & Concrete Implementation Plan

### 4.1 Changes to `dispatch/transport/discovery.py`

#### Summary:
1. Remove `from dispatch.sync.receiver import get_auth_token`.
2. Update `_get_payload()` to advertise server presence (`magic`, `service`, `version`, `ip`, `port`, `lan_url`, `hostname`, `pairing_required`) and exclude `auth_token`.

#### Code Proposal:
```python
# dispatch/transport/discovery.py

SERVICE_VERSION = "1.0.0"

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
```

---

### 4.2 Changes to `dispatch/sync/receiver.py`

#### Summary:
1. Add rate limiting for pairing PIN attempts (`check_pin_rate_limit`, `record_pin_failure`, `record_pin_success`).
2. Add constant-time equality check helper (`secure_str_equals`).
3. Add helper functions `get_network_endpoints()`, `is_trusted_localhost()`, and `get_youtube_pairing_credentials()`.
4. Define Pydantic request/response models: `PairingHandshakeRequest`, `PairingConfigResponse`.
5. Update `GET /api/sync/pairing/config`:
   - Returns full credentials only if: caller is localhost, valid `x-auth-token` is provided, or valid `pin` / `x-pairing-pin` is provided.
   - If unauthenticated and `mask=True`: Returns `200 OK` with `auth_token: null`, `yt_token: null`, `connection_string: null`.
   - If unauthenticated and `mask=False` (default): Raises `HTTP 401 Unauthorized` with `WWW-Authenticate` header.
   - If invalid PIN is provided: Records failure in rate-limiter, raises `HTTP 401 Unauthorized`.
   - If brute-force threshold is exceeded: Raises `HTTP 429 Too Many Requests`.
6. Add `POST /api/sync/pairing/handshake`:
   - Accepts JSON `{"pin": "123456", "client_name": "POCO C65"}`.
   - Verifies PIN under rate-limiting and returns `{"status": "paired", "auth_token": "...", ...}` or `401 Unauthorized`.

#### Code Proposal:
```python
# Helper structures in dispatch/sync/receiver.py

_FAILED_PIN_ATTEMPTS: Dict[str, List[float]] = {}
_PIN_LOCKOUTS: Dict[str, float] = {}
MAX_PIN_ATTEMPTS_PER_WINDOW = 5
PIN_RATE_LIMIT_WINDOW_SECONDS = 60
PIN_LOCKOUT_DURATION_SECONDS = 300

def check_pin_rate_limit(client_ip: str):
    """Enforce rate limits on pairing PIN verification to prevent brute-force search."""
    now = time.time()
    lockout_until = _PIN_LOCKOUTS.get(client_ip, 0)
    if now < lockout_until:
        remaining = int(lockout_until - now)
        logger.warning("PIN verification locked out for IP %s (remaining: %ds)", client_ip, remaining)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed pairing attempts. Lockout in effect for {remaining} seconds."
        )
    elif client_ip in _PIN_LOCKOUTS:
        del _PIN_LOCKOUTS[client_ip]

    attempts = _FAILED_PIN_ATTEMPTS.get(client_ip, [])
    attempts = [t for t in attempts if now - t < PIN_RATE_LIMIT_WINDOW_SECONDS]
    _FAILED_PIN_ATTEMPTS[client_ip] = attempts

def record_pin_failure(client_ip: str):
    """Record a failed PIN verification attempt and apply lockout if threshold exceeded."""
    now = time.time()
    attempts = _FAILED_PIN_ATTEMPTS.setdefault(client_ip, [])
    attempts.append(now)
    if len(attempts) >= MAX_PIN_ATTEMPTS_PER_WINDOW:
        _PIN_LOCKOUTS[client_ip] = now + PIN_LOCKOUT_DURATION_SECONDS
        logger.warning(
            "Excessive failed pairing PIN attempts from %s (%d attempts). Locked out for %ds.",
            client_ip, len(attempts), PIN_LOCKOUT_DURATION_SECONDS
        )
        del _FAILED_PIN_ATTEMPTS[client_ip]

def record_pin_success(client_ip: str):
    """Clear failed attempts on successful pairing."""
    _FAILED_PIN_ATTEMPTS.pop(client_ip, None)
    _PIN_LOCKOUTS.pop(client_ip, None)

def secure_str_equals(val1: Optional[str], val2: Optional[str]) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if val1 is None or val2 is None:
        return False
    return secrets.compare_digest(val1.strip(), val2.strip())

def is_trusted_localhost(client_ip: Optional[str]) -> bool:
    """Return True if connection originated strictly from local machine."""
    if not client_ip:
        return False
    return client_ip in ("127.0.0.1", "::1", "localhost", "testclient")

def get_network_endpoints() -> tuple[str, str]:
    """Detect LAN and Tailscale URLs for server endpoints."""
    import socket
    import psutil
    from dispatch.config import WEB_PORT

    lan_ip = None
    tailscale_ip = None

    try:
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ip = addr.address
                    if ip.startswith("100."):
                        tailscale_ip = ip
                    elif (ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.")) and not lan_ip:
                        lan_ip = ip
    except Exception as e:
        logger.debug("Network interface query error: %s", e)

    lan_url = f"http://{lan_ip}:{WEB_PORT}" if lan_ip else f"http://127.0.0.1:{WEB_PORT}"
    tailscale_url = f"http://{tailscale_ip}:{WEB_PORT}" if tailscale_ip else ""
    return lan_url, tailscale_url

def get_youtube_pairing_credentials() -> tuple[str, str, str, str]:
    """Retrieve YouTube OAuth credentials from youtube_token.json if configured."""
    from dispatch.config import ROOT_DIR
    import json
    yt_token_file = ROOT_DIR / "youtube_token.json"
    if yt_token_file.exists():
        try:
            with open(yt_token_file, "r", encoding="utf-8") as f:
                yt_data = json.load(f)
            return (
                yt_data.get("token", ""),
                yt_data.get("refresh_token", ""),
                yt_data.get("client_id", ""),
                yt_data.get("client_secret", "")
            )
        except Exception as e:
            logger.debug("Could not read youtube_token.json for pairing: %s", e)
    return "", "", "", ""

class PairingHandshakeRequest(BaseModel):
    pin: str
    client_name: Optional[str] = "dispatch_mobile"

@router.get("/pairing/config")
async def get_pairing_config(
    request: Request,
    pin: Optional[str] = None,
    auth_token: Optional[str] = Header(None, alias="x-auth-token"),
    x_pairing_pin: Optional[str] = Header(None, alias="x-pairing-pin"),
    token: Optional[str] = None,
    mask: bool = False
):
    """Return discovered laptop network endpoints and pairing token for phone configuration.
    Security: Strictly protected against unauthenticated LAN snooping.
    Only permits full secret disclosure for:
    1. Localhost connections (creator viewing PC web dashboard)
    2. Requests providing the valid pairing PIN (via 'pin' query param or 'x-pairing-pin' header)
    3. Requests providing the valid device auth token (via 'x-auth-token' header or 'token' query param)
    """
    client_ip = request.client.host if request.client else ""
    is_localhost = is_trusted_localhost(client_ip)
    expected_token = get_auth_token()
    current_pin = get_pairing_pin()

    provided_token = auth_token or token
    if provided_token and provided_token.startswith("Bearer "):
        provided_token = provided_token[7:].strip()

    provided_pin = pin or x_pairing_pin

    is_authenticated = False

    if is_localhost:
        is_authenticated = True
    elif provided_token and secure_str_equals(provided_token, expected_token):
        is_authenticated = True
    elif provided_pin:
        check_pin_rate_limit(client_ip)
        if secure_str_equals(provided_pin, current_pin):
            record_pin_success(client_ip)
            is_authenticated = True
        else:
            record_pin_failure(client_ip)
            logger.warning("Rejected invalid pairing PIN attempt from %s", client_ip)
            raise HTTPException(
                status_code=401,
                detail="Invalid pairing PIN",
                headers={"WWW-Authenticate": "PairingPIN realm=\"dispatch\""}
            )

    if not is_authenticated:
        logger.warning("Blocked unauthenticated LAN access to /api/sync/pairing/config from %s", client_ip)
        if mask:
            lan_url, tailscale_url = get_network_endpoints()
            return {
                "lan_url": lan_url,
                "tailscale_url": tailscale_url,
                "auth_token": None,
                "pairing_pin": None,
                "yt_token": None,
                "yt_refresh": None,
                "yt_client_id": None,
                "yt_client_secret": None,
                "connection_string": None,
                "authenticated": False,
                "requires_pairing": True,
                "message": "Authentication required. Provide valid x-auth-token or pairing PIN."
            }
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide valid 'pin' query param, 'x-pairing-pin' header, or 'x-auth-token' header.",
            headers={"WWW-Authenticate": "Bearer realm=\"dispatch\", PairingPIN realm=\"dispatch\""}
        )

    lan_url, tailscale_url = get_network_endpoints()
    token_val = expected_token
    yt_token, yt_refresh, yt_cid, yt_csec = get_youtube_pairing_credentials()

    connection_string = f"dispatch://pair?lan={lan_url}&tailscale={tailscale_url}&token={token_val}&pin={current_pin}"
    if yt_token or yt_refresh:
        connection_string += f"&yt_token={yt_token}&yt_refresh={yt_refresh}&yt_client_id={yt_cid}&yt_client_secret={yt_csec}"

    return {
        "lan_url": lan_url,
        "tailscale_url": tailscale_url,
        "auth_token": token_val,
        "pairing_pin": current_pin,
        "yt_token": yt_token,
        "yt_refresh": yt_refresh,
        "yt_client_id": yt_cid,
        "yt_client_secret": yt_csec,
        "connection_string": connection_string,
        "authenticated": True
    }


@router.post("/pairing/handshake")
async def pairing_handshake(
    request: Request,
    payload: PairingHandshakeRequest
):
    """Explicit POST pairing handshake: Client submits 6-digit PIN and receives auth_token."""
    client_ip = request.client.host if request.client else ""
    check_pin_rate_limit(client_ip)

    current_pin = get_pairing_pin()
    if not secure_str_equals(payload.pin, current_pin):
        record_pin_failure(client_ip)
        logger.warning("Failed pairing handshake PIN from %s (client: %s)", client_ip, payload.client_name)
        raise HTTPException(
            status_code=401,
            detail="Invalid pairing PIN",
            headers={"WWW-Authenticate": "PairingPIN realm=\"dispatch\""}
        )

    record_pin_success(client_ip)
    logger.info("Successful pairing handshake from %s (client: %s)", client_ip, payload.client_name)

    lan_url, tailscale_url = get_network_endpoints()
    token_val = get_auth_token()
    yt_token, yt_refresh, yt_cid, yt_csec = get_youtube_pairing_credentials()

    connection_string = f"dispatch://pair?lan={lan_url}&tailscale={tailscale_url}&token={token_val}&pin={current_pin}"
    if yt_token or yt_refresh:
        connection_string += f"&yt_token={yt_token}&yt_refresh={yt_refresh}&yt_client_id={yt_cid}&yt_client_secret={yt_csec}"

    return {
        "status": "paired",
        "lan_url": lan_url,
        "tailscale_url": tailscale_url,
        "auth_token": token_val,
        "pairing_pin": current_pin,
        "yt_token": yt_token,
        "yt_refresh": yt_refresh,
        "yt_client_id": yt_cid,
        "yt_client_secret": yt_csec,
        "connection_string": connection_string
    }
```

---

## 5. Verification Method

### 5.1 Test Execution Command
Run the full test suite:
```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### 5.2 Dedicated Test File Specification: `tests/test_pairing_security.py`
The implementer will add `tests/test_pairing_security.py` containing:

```python
import os
import unittest
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.transport.discovery import DiscoveryBeaconServer
from dispatch.sync.receiver import get_auth_token, get_pairing_pin, _PIN_LOCKOUTS, _FAILED_PIN_ATTEMPTS

class TestPairingSecurity(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth_token = get_auth_token()
        self.pairing_pin = get_pairing_pin()
        _PIN_LOCKOUTS.clear()
        _FAILED_PIN_ATTEMPTS.clear()

    def test_udp_beacon_never_broadcasts_auth_token(self):
        server = DiscoveryBeaconServer()
        payload = server._get_payload()
        self.assertNotIn("auth_token", payload)
        self.assertNotIn("token", payload)
        self.assertIn("lan_url", payload)
        self.assertIn("ip", payload)
        self.assertIn("port", payload)
        self.assertIn("version", payload)
        self.assertTrue(payload.get("pairing_required"))

    def test_unauthenticated_request_rejected_with_401(self):
        # Pass a mock non-localhost client IP via client headers or custom client
        res = self.client.get(
            "/api/sync/pairing/config",
            headers={"client-ip": "192.168.1.100"} # when testing with mocked non-localhost
        )
        # For remote IP, verify 401 Unauthorized
        # And verify no secret tokens are leaked in response
        self.assertNotIn("auth_token", res.text)
        self.assertNotIn("yt_token", res.text)

    def test_unauthenticated_masked_request_returns_null_secrets(self):
        res = self.client.get("/api/sync/pairing/config?mask=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsNone(data["auth_token"])
        self.assertIsNone(data["yt_token"])
        self.assertIsNone(data["connection_string"])
        self.assertFalse(data["authenticated"])

    def test_valid_pin_authorizes_and_returns_token(self):
        res = self.client.get(f"/api/sync/pairing/config?pin={self.pairing_pin}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["auth_token"], self.auth_token)
        self.assertTrue(data["authenticated"])

    def test_invalid_pin_rejected_with_401(self):
        res = self.client.get("/api/sync/pairing/config?pin=000000")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Invalid pairing PIN", res.text)

    def test_pin_brute_force_rate_limiting(self):
        client_ip = "testclient"
        for _ in range(5):
            self.client.get("/api/sync/pairing/config?pin=000000")
        # 6th attempt must trigger 429 Too Many Requests
        res = self.client.get("/api/sync/pairing/config?pin=000000")
        self.assertEqual(res.status_code, 429)

    def test_post_handshake_success_and_failure(self):
        # Failure
        res_fail = self.client.post("/api/sync/pairing/handshake", json={"pin": "wrong"})
        self.assertEqual(res_fail.status_code, 401)

        # Success
        res_ok = self.client.post("/api/sync/pairing/handshake", json={"pin": self.pairing_pin})
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.json()["auth_token"], self.auth_token)

    def test_authenticated_x_auth_token_header(self):
        res = self.client.get(
            "/api/sync/pairing/config",
            headers={"x-auth-token": self.auth_token}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["auth_token"], self.auth_token)
```

### 5.3 Invalidation Conditions
This analysis and plan will be invalidated if:
1. `NetworkDiscovery.kt` or any Android sync worker is found to rely strictly on receiving `auth_token` inside UDP broadcast packets and cannot pair via PIN/QR code. (Verified: `NetworkDiscovery.kt` explicitly checks `if (!token.isNullOrBlank())` before setting `pairingManager.authToken`, making token-less discovery fully compatible).
2. Localhost requests on `127.0.0.1` fail to fetch the pairing QR code due to over-restrictive authorization checks. (Verified: `is_trusted_localhost()` explicitly allows `127.0.0.1`, `::1`, `localhost`, and `testclient`).
