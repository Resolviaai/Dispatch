## 2026-10-07T09:25:51Z
You are Worker M1 (Implementation Agent for Milestone 1: Ingestion, Transport & Pairing Security).
Your working directory is: c:\CODE\Dispatch\.agents\teamwork\worker_m1_1

You MUST read:
- c:\CODE\Dispatch\.agents\teamwork\ORIGINAL_REQUEST.md
- c:\CODE\Dispatch\PROJECT.md
- c:\CODE\Dispatch\.agents\teamwork\explorer_m1_1\handoff.md
- c:\CODE\Dispatch\.agents\teamwork\explorer_m1_2\handoff.md
- c:\CODE\Dispatch\.agents\teamwork\explorer_m1_3\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your implementation tasks:
1. `dispatch/transport/discovery.py`:
   - Strip master `auth_token` from UDP discovery beacon payload (`_get_payload()`).
   - Advertise server presence (`magic`, `service`, `version`, `ip`, `port`, `lan_url`, `hostname`, `pairing_required=True`).
   - Ensure `DISPATCH_DISCOVER` query responses do NOT send secret tokens.
2. `dispatch/sync/receiver.py`:
   - Harden `GET /api/sync/pairing/config`:
     - Reject unauthenticated LAN requests with HTTP 401 Unauthorized (`WWW-Authenticate` header) and zero secret leakage.
     - Allow localhost exemption for local web dashboard (`127.0.0.1`, `::1`, `localhost`, `testclient`).
     - Authenticate via `x-auth-token` header or `pin`/`x-pairing-pin`.
     - Support `mask=true` returning 200 OK with null secrets for safe network probing.
     - Implement brute-force protection (max 5 failed PIN attempts per IP per 60s window, 300s lockout returning HTTP 429 Too Many Requests).
     - Use `secrets.compare_digest` for constant-time comparisons.
   - Add `POST /api/sync/pairing/handshake` accepting JSON `{"pin": "...", "client_name": "..."}` and returning tokens upon valid PIN.
3. Android Client (`android/app/src/main/java/com/resolvia/dispatch/`):
   - In `data/NetworkDiscovery.kt`:
     - Allow UDP discovery to succeed when `auth_token` is omitted/null from the UDP packet.
     - Update `ensureAuthToken()` to supply `x-auth-token` header if token is already present, or skip unauthenticated calls if unpaired.
   - In `data/PairingManager.kt`:
     - Add `pairWithPin(baseUrl, pin, onResult)` method sending `x-pairing-pin`.
   - In `sync/ResumableSyncWorker.kt`:
     - Remove unauthenticated call to `/api/sync/pairing/config`.
4. Tests (`tests/test_transport.py` & `tests/test_sync_engine.py`):
   - Add comprehensive tests for pairing security: unauthenticated LAN returns 401 without tokens, valid PIN returns token, invalid PIN returns 401, brute-force returns 429, UDP beacon omits token, Android discovery flow.
   - Add tests for `GET /api/sync/verify-chunk` and offset mismatch (409 Conflict).
5. Build & Test Verification:
   - Run tests: `python -m unittest tests/test_transport.py` and `python -m unittest tests/test_sync_engine.py`.
   - Ensure all tests pass.

## 2026-10-07T09:40:26Z
**Context**: Milestone 1 Implementation Monitoring
**Content**: Status check on Milestone 1 implementation. What files have been modified, and what steps remain before tests and handoff?
**Action**: Please report current status and update progress.md.
