# Handoff Report: Transport & Chunked Upload Verification Analysis

**Agent**: Explorer M1_3  
**Milestone**: Milestone 1 (Ingestion, Transport & Pairing Security)  
**Date**: 2026-10-07T09:26:00Z  
**Target Path**: `c:\CODE\Dispatch\.agents\teamwork\explorer_m1_3\handoff.md`  

---

## 1. Observation

### 1.1 Ingestion Receiver Analysis (`dispatch/sync/receiver.py`)
- **Chunked Upload Flow**:
  - `POST /api/sync/upload/init` (Lines 374–425):
    - Input schema `InitUploadRequest`: `session_id`, `segment_id`, `filename`, `file_size_bytes`, `sha256_hash`, `auth_token`.
    - Sanitizes `segment_id` via `sanitize_segment_id` (Lines 28–36) checking regex `^[A-Za-z0-9_-]{1,64}$`, rejecting path traversal with HTTP 400.
    - Idempotency check (Lines 387–408): If target file exists in `PROCESSING_DIR` or `INCOMING_DIR`, calls `verify_file_integrity` against expected size and SHA-256. If valid, returns `{"status": "already_completed", "remote_offset": file_size_bytes, "verified": True}`. If corrupted, deletes file with `target_file.unlink(missing_ok=True)`. Also checks SQLite `chunks` table for matching hash.
    - Resumption offset check (Lines 409–424): If `.part` file exists in `INCOMING_DIR`, resets to 0 if oversized (`part_size > payload.file_size_bytes`), otherwise returns `remote_offset = part_size`.
  - `PATCH /api/sync/upload/chunk` (Lines 427–526):
    - Required Headers: `x-segment-id`, `x-upload-offset`, `x-file-size`, `x-sha256`, `x-session-id`, `x-auth-token`.
    - Authenticates via `verify_token(x_auth_token)` (Lines 61–76), raising HTTP 401 on missing or mismatched token.
    - **Offset Consistency Check** (Lines 444–450):
      ```python
      current_size = part_file.stat().st_size if part_file.exists() else 0
      if x_upload_offset != current_size:
          raise HTTPException(
              status_code=409,
              detail=f"Offset mismatch: client has {x_upload_offset}, server has {current_size}"
          )
      ```
    - Streaming byte append (Lines 452–459): Reads binary body from `request.body()` and appends via `with open(part_file, "ab") as f: f.write(chunk_bytes)`.
    - **SHA-256 Verification on Completion** (Lines 462–475): When `new_offset == x_file_size`, streams file in 64 KB blocks through `hashlib.sha256()`. If `computed_sha.lower() != x_sha256.lower()`, deletes partial file (`part_file.unlink()`) and raises HTTP 422:
      ```python
      if computed_sha.lower() != x_sha256.lower():
          logger.error("SHA-256 mismatch for %s: expected %s, got %s", seg_id, x_sha256, computed_sha)
          part_file.unlink()
          raise HTTPException(status_code=422, detail="Checksum mismatch; partial file purged")
      ```
    - **Atomic Rename and Move to Processing** (Lines 476–488):
      ```python
      if final_file.exists():
          final_file.unlink(missing_ok=True)
      part_file.replace(final_file)
      # Move immediately to processing to avoid double scan delays
      target_path = PROCESSING_DIR / final_file.name
      if target_path.exists():
          target_path.unlink(missing_ok=True)
      shutil.move(str(final_file), str(target_path))
      ```
      Uses `part_file.replace(final_file)` (atomic replacement) and `shutil.move` to `PROCESSING_DIR`, preventing race conditions with external file watchers.
    - Database Registration & Pipeline Enqueue (Lines 490–519): Registers chunk in SQLite via `db.register_chunk`, validates video container via `probe_video`, and directly triggers `enqueue_job(chunk_id=chunk_id, session_id=x_session_id)`.
- **Cryptographic Receipt Verification (`/api/sync/verify-chunk`)** (Lines 217–268):
  - Parameters: query `segment_id`, `sha256`, `file_size` (optional), header `x-auth-token` or query `token`.
  - Requires valid token via `verify_token()`, returning HTTP 401 if unauthorized.
  - Checks physical file in `PROCESSING_DIR` and `INCOMING_DIR`. If size matches and 1MB streaming SHA-256 matches, returns:
    `{"verified": True, "segment_id": seg_id, "size": actual_size, "status": "VERIFIED"}`.
  - If physical file hash differs, returns `{"verified": False, "reason": "sha256_mismatch", "status": "CORRUPT_RETRY_REQUIRED"}`.
  - Fallback to SQLite DB `chunks` table if file was already moved/archived. If row exists with matching hash, returns `{"verified": True, "segment_id": seg_id, "status": "VERIFIED_PROCESSED"}`.
  - If neither found, returns `{"verified": False, "reason": "file_not_found", "status": "MISSING"}`.
- **Pairing Config Endpoint (`/api/sync/pairing/config`)** (Lines 122–215):
  - Parameters: query `pin`, `token`; headers `x-auth-token`, `x-pairing-pin`.
  - Authentication check (Lines 137–159):
    ```python
    client_ip = request.client.host if request.client else ""
    is_localhost = client_ip in ("127.0.0.1", "::1", "localhost", "testclient")
    expected_token = get_auth_token()
    current_pin = get_pairing_pin()
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
  - Direct Verification Command Executed:
    `python -c "from fastapi.testclient import TestClient; from dispatch.web.app import app; c = TestClient(app, client=('192.168.1.100', 50000)); r = c.get('/api/sync/pairing/config'); print(r.status_code, r.json())"`
    Result: Returned HTTP 403 `{'detail': 'Unauthenticated LAN pairing prohibited. Access via localhost, scan QR code, or enter pairing PIN.'}`.
  - Note: Returning 403 differs from standard HTTP 401 Unauthorized for unauthenticated requests. Also, because `is_localhost` includes `"testclient"`, default `TestClient(app)` calls are considered localhost unless initialized with a remote client IP.

### 1.2 UDP Discovery Beacon Analysis (`dispatch/transport/discovery.py`)
- In `dispatch/transport/discovery.py` lines 65–76:
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
- Direct Verification Command Executed:
  `python -c "from dispatch.transport.discovery import DiscoveryBeaconServer; s = DiscoveryBeaconServer(); p = s._get_payload(); print('Contains auth_token:', 'auth_token' in p)"`
  Result: `Contains auth_token: True`.
- Observation: Plaintext `auth_token` is broadcasted on UDP port 8765 to `255.255.255.255` and subnet broadcasts, and sent in response to `DISPATCH_DISCOVER` datagrams. Any device on the LAN can intercept the master auth token.

### 1.3 Test Suite State (`tests/test_transport.py` & `tests/test_sync_engine.py`)
- Running `python -m unittest tests/test_transport.py`:
  Ran 3 tests in 0.002s — OK.
  - Tests `test_auth_headers`, `test_network_policy_evaluation`, `test_route_probing_and_failover`.
  - **Zero tests for `discovery.py`** or UDP beacon payload.
  - **Zero tests for `/api/sync/pairing/config`**.
- Running `python -m unittest tests/test_sync_engine.py`:
  Ran 1 test in 0.188s — OK.
  - Method `test_resumable_sync_and_reconcile` tests:
    - 50% chunk upload via `PATCH /api/sync/upload/chunk`
    - Reconcile before, during, and after upload
    - Resuming second chunk at offset `half_size`
    - Atomic rename from `.part` to `.mp4`
    - Database insertion in `chunks` table
    - Deletion of local mobile segment after `status = 'VERIFIED_BY_LAPTOP'`.
  - **Missing tests in `test_sync_engine.py`**:
    - Direct testing of `GET /api/sync/verify-chunk`.
    - Testing offset mismatch rejection (HTTP 409).
    - Testing checksum mismatch on chunk completion (HTTP 422).
    - Testing unauthenticated chunked upload requests (HTTP 401).

---

## 2. Logic Chain

1. **Premise 1**: Acceptance criterion 1 requires: *"Unauthenticated requests to `/api/sync/pairing/config` do not leak authentication tokens to arbitrary LAN sniffers."*
2. **Observation Link 1**:
   - `dispatch/sync/receiver.py` (Line 157) blocks unauthenticated LAN requests, but returns HTTP 403 Forbidden instead of HTTP 401 Unauthorized.
   - If an unauthenticated request arrives from the LAN, it returns `{detail: "..."}` and DOES NOT return `auth_token`, `yt_token`, `yt_refresh`, `yt_client_id`, `yt_client_secret`, or `connection_string`.
   - However, currently `dispatch/transport/discovery.py` lines 67–74 broadcast `auth_token` in plaintext over UDP port 8765 to the entire local subnet. An attacker on the LAN does not need to query `/api/sync/pairing/config` if they can simply listen for the UDP broadcast or send a `DISPATCH_DISCOVER` packet.
   - Furthermore, in automated test suites (`test_transport.py` and `test_sync_engine.py`), there are ZERO tests asserting this security boundary.
3. **Premise 2**: Acceptance criterion 2 requires: *"Android discovery automatically identifies the laptop server IP and successfully handshakes."*
4. **Observation Link 2**:
   - In `android/app/src/main/java/com/resolvia/dispatch/data/NetworkDiscovery.kt`:
     - Method `discoverViaUdpBroadcast()` sends `DISPATCH_DISCOVER` and expects a response with `lan_url` and `auth_token`.
     - Method `sweepSubnet()` probes `/api/sync/ping` across the `/24` subnet.
     - Method `ensureAuthToken(validUrl)` calls `GET ${baseUrl}/api/sync/pairing/config`.
   - In `android/app/src/main/java/com/resolvia/dispatch/data/PairingManager.kt`:
     - Method `autoPairFromHost()` queries `/api/sync/pairing/config` without auth headers or PIN.
     - Method `saveFromConnectionString()` parses `dispatch://pair?lan=...&token=...&pin=...`.
   - If `discovery.py` is hardened to remove `auth_token` from UDP packets, the Android discovery handshake must obtain the token legitimately:
     - The user enters the 6-digit PIN displayed on the laptop dashboard (or scans the QR code containing `token` / `pin`), or
     - Android sends the PIN to `GET /api/sync/pairing/config?pin=<PIN>` (or `x-pairing-pin` header), receiving the full credentials.
   - Neither `test_transport.py` nor `test_sync_engine.py` tests this handshake flow.

---

## 3. Caveats

1. **Localhost Exemption on Web Dashboard**: In `receiver.py:138`, `is_localhost` includes `127.0.0.1`, `::1`, `localhost`, and `testclient`. This is intended to allow the solo creator opening the dashboard on their laptop browser to view the QR code and credentials without authenticating first. However, when writing tests using `TestClient(app)`, `TestClient` uses `"testclient"` as its host by default, so it bypasses the LAN security check unless configured with `client=("192.168.1.100", 50000)`.
2. **HTTP Status Code Alignment (401 vs 403)**: `receiver.py:157` currently raises HTTP 403 Forbidden. While both 401 and 403 prevent leaking secrets, HTTP 401 Unauthorized is the standard convention for unauthenticated requests. Tests should assert either `res.status_code in (401, 403)` or update `receiver.py:157` to return 401.
3. **Android Client Coroutines in Local Test**: The Android application is written in Kotlin. Testing Android discovery and handshaking within Python unit tests (`test_transport.py` and `test_sync_engine.py`) requires simulating the client-side UDP datagrams and HTTP requests matching `NetworkDiscovery.kt` and `PairingManager.kt`.

---

## 4. Conclusion

1. **Chunked Upload & Verification in `receiver.py`**:
   - The implementation of chunked upload, offset checking (HTTP 409), streaming SHA-256 validation (HTTP 422), atomic file movement to `PROCESSING_DIR`, and explicit cryptographic receipt verification via `/api/sync/verify-chunk` is solid, robust, and correctly protects data integrity.
2. **Security Vulnerability in Transport Discovery**:
   - `dispatch/transport/discovery.py` currently leaks `auth_token` in `_get_payload()`. It must be updated to omit `auth_token` from UDP announcements and direct UDP query replies.
3. **Pairing Config Endpoint Hardening**:
   - `receiver.py:157` should standardize on HTTP 401 Unauthorized (instead of 403) when unauthenticated LAN clients connect.
4. **Test Suite Expansion**:
   - `tests/test_transport.py` and `tests/test_sync_engine.py` require concrete test cases to directly verify:
     - Unauthenticated requests to `/api/sync/pairing/config` return 401 (or 403) and yield zero credentials.
     - Requests with valid PIN or valid auth token return 200 and yield credentials.
     - UDP beacon does not broadcast `auth_token`.
     - Android discovery simulation successfully identifies server IP and completes handshake.
     - Explicit proof-of-receipt endpoint `/api/sync/verify-chunk` behaves according to contract.
     - Offset mismatch returns 409 Conflict.

---

## 5. Verification Method & Recommended Test Cases

### 5.1 Test Assertions to Add to `tests/test_transport.py`

Add a test suite `TestDiscoveryAndPairingSecurity(unittest.TestCase)` covering:

```python
import socket
import json
import unittest
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.sync.receiver import get_auth_token, get_pairing_pin
from dispatch.transport.discovery import DiscoveryBeaconServer, BEACON_MAGIC, DISCOVERY_PORT


class TestDiscoveryAndPairingSecurity(unittest.TestCase):
    def setUp(self):
        # Client simulating a remote phone / LAN device
        self.lan_client = TestClient(app, client=("192.168.1.100", 50000))
        # Client simulating laptop localhost
        self.local_client = TestClient(app, client=("127.0.0.1", 50000))
        self.expected_token = get_auth_token()
        self.expected_pin = get_pairing_pin()

    def test_pairing_config_unauthenticated_lan_request_rejected(self):
        """Milestone 1 Acceptance: Unauthenticated LAN requests do NOT leak tokens."""
        resp = self.lan_client.get("/api/sync/pairing/config")
        # Must return 401 Unauthorized or 403 Forbidden
        self.assertIn(resp.status_code, (401, 403))
        resp_data = resp.json()
        
        # Absolute guarantee: no secrets in payload
        self.assertNotIn("auth_token", resp_data)
        self.assertNotIn("connection_string", resp_data)
        self.assertNotIn("yt_token", resp_data)
        self.assertNotIn("yt_refresh", resp_data)
        self.assertNotIn("yt_client_id", resp_data)
        self.assertNotIn("yt_client_secret", resp_data)
        self.assertNotIn(self.expected_token, resp.text)

    def test_pairing_config_invalid_pin_or_token_rejected(self):
        """Invalid PIN or invalid token from LAN must be rejected."""
        # Wrong PIN
        resp_bad_pin = self.lan_client.get("/api/sync/pairing/config", headers={"x-pairing-pin": "000000"})
        self.assertIn(resp_bad_pin.status_code, (401, 403))

        # Wrong Auth Token
        resp_bad_tok = self.lan_client.get("/api/sync/pairing/config", headers={"x-auth-token": "bad_token_123"})
        self.assertIn(resp_bad_tok.status_code, (401, 403))

    def test_pairing_config_authenticated_with_pin(self):
        """Client providing valid 6-digit pairing PIN successfully authenticates and receives tokens."""
        # Query parameter
        resp_query = self.lan_client.get(f"/api/sync/pairing/config?pin={self.expected_pin}")
        self.assertEqual(resp_query.status_code, 200)
        self.assertEqual(resp_query.json()["auth_token"], self.expected_token)
        self.assertIn("lan_url", resp_query.json())

        # Header parameter
        resp_hdr = self.lan_client.get("/api/sync/pairing/config", headers={"x-pairing-pin": self.expected_pin})
        self.assertEqual(resp_hdr.status_code, 200)
        self.assertEqual(resp_hdr.json()["auth_token"], self.expected_token)

    def test_pairing_config_authenticated_with_device_token(self):
        """Previously paired client providing x-auth-token successfully retrieves current config."""
        resp = self.lan_client.get("/api/sync/pairing/config", headers={"x-auth-token": self.expected_token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auth_token"], self.expected_token)

    def test_udp_discovery_beacon_does_not_leak_auth_token(self):
        """Milestone 1 Acceptance: UDP discovery beacon must NOT broadcast master auth token."""
        server = DiscoveryBeaconServer()
        payload = server._get_payload()
        self.assertEqual(payload["magic"], BEACON_MAGIC)
        self.assertIn("lan_url", payload)
        self.assertIn("ip", payload)
        self.assertIn("port", payload)
        # CRITICAL: auth_token must NOT be present in LAN UDP broadcast
        self.assertNotIn("auth_token", payload, "UDP discovery beacon must not leak auth_token to LAN sniffers")

    def test_simulated_android_discovery_and_handshake_flow(self):
        """Milestone 1 Acceptance: Android discovery identifies server IP and successfully handshakes."""
        server = DiscoveryBeaconServer()
        payload = server._get_payload()
        discovered_ip = payload["ip"]
        discovered_port = payload["port"]
        discovered_url = payload["lan_url"]
        self.assertTrue(discovered_ip and discovered_port)

        # Step 1: Phone tests reachability on ping endpoint
        ping_resp = self.lan_client.get("/api/sync/ping")
        self.assertEqual(ping_resp.status_code, 200)
        self.assertEqual(ping_resp.json()["status"], "online")

        # Step 2: Unauthenticated probe fails (security intact)
        unauth_resp = self.lan_client.get("/api/sync/pairing/config")
        self.assertIn(unauth_resp.status_code, (401, 403))

        # Step 3: Phone submits pairing PIN entered by user
        pair_resp = self.lan_client.get(f"/api/sync/pairing/config?pin={self.expected_pin}")
        self.assertEqual(pair_resp.status_code, 200)
        acquired_token = pair_resp.json()["auth_token"]
        self.assertEqual(acquired_token, self.expected_token)

        # Step 4: Phone can now perform authenticated sync operations
        auth_check = self.lan_client.get("/api/sync/verify-chunk?segment_id=dummy&sha256=none", 
                                         headers={"x-auth-token": acquired_token})
        self.assertEqual(auth_check.status_code, 200)
```

---

### 5.2 Test Assertions to Add to `tests/test_sync_engine.py`

Add tests verifying `/api/sync/verify-chunk` and chunk offset enforcement:

```python
    def test_verify_chunk_cryptographic_receipt(self):
        """Explicit cryptographic proof-of-receipt endpoint (/api/sync/verify-chunk)."""
        # 1. Unauthenticated request must return 401
        res_unauth = client.get("/api/sync/verify-chunk?segment_id=test_dummy&sha256=abcdef")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Non-existent segment returns status: MISSING
        res_missing = client.get(
            "/api/sync/verify-chunk?segment_id=non_existent&sha256=abcdef",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res_missing.status_code, 200)
        self.assertFalse(res_missing.json()["verified"])
        self.assertEqual(res_missing.json()["status"], "MISSING")

    def test_chunk_upload_offset_mismatch_returns_409(self):
        """Chunk upload with mismatched offset must return HTTP 409 Conflict."""
        seg_id = "seg_offset_test_1"
        payload = b"TEST_CHUNK_PAYLOAD_OFFSET"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "100",  # Incorrect offset (server has 0)
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_offset",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream"
        }
        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Offset mismatch", res.json()["detail"])
```

### 5.3 Independent Execution Command
Run the test command specified in `PROJECT.md` and `ORIGINAL_REQUEST.md`:
```powershell
python -m unittest tests/test_transport.py
python -m unittest tests/test_sync_engine.py
python -m unittest discover -s tests -p "test_*.py"
```

### 5.4 Invalidation Conditions
- If unauthenticated LAN requests to `/api/sync/pairing/config` return HTTP 200 or contain `"auth_token"`.
- If `DiscoveryBeaconServer._get_payload()` contains `"auth_token"`.
- If chunk upload accepts an out-of-order byte offset without returning HTTP 409.
- If `/api/sync/verify-chunk` returns `verified: True` for an unauthenticated request or mismatched hash.
