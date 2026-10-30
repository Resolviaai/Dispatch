# Handoff Report: Android Discovery and Pairing Client Architecture

**Author**: Explorer M1_2  
**Milestone**: Milestone 1 (Ingestion, Transport & Pairing Security)  
**Date**: 2026-10-07T09:25:00Z  
**Target Path**: `c:\CODE\Dispatch\.agents\teamwork\explorer_m1_2\handoff.md`

---

## 1. Observation

### 1.1 Existing Android Discovery & Pairing Client Code

#### A. `android/app/src/main/java/com/resolvia/dispatch/data/NetworkDiscovery.kt`
1. **Discovery Entry Point (`discoverAndConnect`)**:
   - Lines 44–85:
     ```kotlin
     suspend fun discoverAndConnect(timeoutMs: Long = 2000L): String? = withContext(Dispatchers.IO) {
         // 1. Probe known configured lanHost
         val knownHost = pairingManager.lanHost.trimEnd('/')
         if (knownHost.isNotBlank()) {
             if (pingEndpoint(knownHost)) {
                 Log.i(tag, "Configured host is online: $knownHost")
                 ensureAuthToken(knownHost)
                 return@withContext knownHost
             }
         }
         // 2. Race UDP broadcast discovery against fast subnet sweep in parallel
         try {
             val result = withTimeoutOrNull(timeoutMs) {
                 val udpJob = async { discoverViaUdpBroadcast() }
                 val sweepJob = async { sweepSubnet() }
                 val discovered = selectFirstResult(listOf(udpJob, sweepJob))
                 discovered
             }
             if (!result.isNullOrBlank()) {
                 val validUrl = result.trimEnd('/')
                 pairingManager.lanHost = validUrl
                 ensureAuthToken(validUrl)
                 return@withContext validUrl
             }
         } catch (e: Exception) { ... }
         // 3. Fallback to Tailscale if configured
         val tsHost = pairingManager.tailscaleHost.trimEnd('/')
         if (tsHost.isNotBlank() && pingEndpoint(tsHost)) {
             ensureAuthToken(tsHost)
             return@withContext tsHost
         }
         null
     }
     ```
2. **UDP Discovery Channel (`discoverViaUdpBroadcast`)**:
   - Lines 110–150:
     - Sends `DISPATCH_DISCOVER` (17 bytes) to `255.255.255.255:8765` and `getLocalSubnetBroadcast():8765`.
     - Listens with `soTimeout = 800`.
     - Lines 133–143:
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
3. **Subnet Sweep Channel (`sweepSubnet`)**:
   - Lines 155–185:
     - Resolves device IP via `WifiManager.connectionInfo.ipAddress` or `NetworkInterface.getNetworkInterfaces()`.
     - Derives subnet prefix (e.g. `192.168.0.`).
     - Tests 19 prioritized octets concurrently:
       `val priorityOctets = listOf(102, 101, 100, 103, 104, 105, 106, 107, 108, 109, 110, 2, 3, 4, 5, 10, 20, 50, 150)`
     - Queries `http://$prefix$oct:8000/api/sync/ping` via `fastProbeClient` (connect timeout 350ms, read timeout 500ms).
     - Resolves the first responsive host within 1200ms using `CompletableDeferred` and `AtomicBoolean`.
4. **Token Fetching (`ensureAuthToken`)**:
   - Lines 208–236:
     ```kotlin
     fun ensureAuthToken(baseUrl: String): Boolean {
         return try {
             val req = Request.Builder()
                 .url("${baseUrl.trimEnd('/')}/api/sync/pairing/config")
                 .get()
                 .build()
             fastProbeClient.newCall(req).execute().use { resp ->
                 if (resp.isSuccessful) {
                     val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                     val token = body.get("auth_token")?.asString ?: ""
                     val ytToken = body.get("yt_token")?.asString ?: ""
                     val ytRefresh = body.get("yt_refresh")?.asString ?: ""
                     val ytCid = body.get("yt_client_id")?.asString ?: ""
                     val ytCsec = body.get("yt_client_secret")?.asString ?: ""
                     if (token.isNotBlank()) pairingManager.authToken = token
                     ...
                     return true
                 }
                 false
             }
         } catch (_: Exception) { false }
     }
     ```
     *Critical Flaw*: Sends NO authentication headers or pairing PIN. Expects an unauthenticated GET request to return the master secret token and YouTube OAuth credentials.

#### B. `android/app/src/main/java/com/resolvia/dispatch/data/PairingManager.kt`
- Lines 13–57: Stores credentials in `SharedPreferences` (`dispatch_pairing`): `lan_host`, `tailscale_host`, `auth_token`, `yt_access_token`, `yt_refresh_token`, `yt_client_id`, `yt_client_secret`.
- Lines 64–86:
  ```kotlin
  fun saveFromConnectionString(uriString: String): Boolean {
      // Parses URI: dispatch://pair?lan=...&tailscale=...&token=...&yt_token=...&yt_refresh=...&yt_client_id=...&yt_client_secret=...
      val uri = Uri.parse(uriString.trim())
      ...
  }
  ```
- Lines 92–151 (`autoPairFromHost`):
  Calls unauthenticated `$targetBase/api/sync/pairing/config`. If the server returns HTTP 401/403, returns `Server returned HTTP ${resp.code}` and aborts.

#### C. `android/app/src/main/java/com/resolvia/dispatch/sync/ResumableSyncWorker.kt`
- Lines 30–42:
  ```kotlin
  if (pairingManager.authToken.isBlank()) {
      val configReq = Request.Builder().url("$activeUrl/api/sync/pairing/config").build()
      client.newCall(configReq).execute().use { resp ->
          if (resp.isSuccessful) {
              val token = body.get("auth_token")?.asString ?: ""
              if (token.isNotBlank()) pairingManager.authToken = token
          }
      }
  }
  ```
  *Flaw*: Directly queries `/api/sync/pairing/config` unauthenticated when token is blank.

#### D. `android/app/src/main/java/com/resolvia/dispatch/sync/LiveSyncManager.kt`
- Lines 103, 200, 236, 277, 341, 361: Calls `networkDiscovery.ensureAuthToken(activeUrl)` on discovery or when receiving HTTP 401 from upload endpoints.

#### E. `android/app/src/main/java/com/resolvia/dispatch/ui/screens/SettingsScreen.kt`
- Lines 70–190: Renders PC Connection card. Shows connected host, "Test connection" button (`liveSyncManager.networkDiscovery.pingEndpoint(hostInput)`), and "Reconnect" button (`liveSyncManager.networkDiscovery.discoverAndConnect(timeoutMs = 2500L)`).
- Does NOT currently provide a field to enter a 6-digit PIN, pairing token, or paste a pairing URI.

#### F. PC Backend Endpoints (`dispatch/sync/receiver.py` & `dispatch/transport/discovery.py`)
- `dispatch/transport/discovery.py` lines 65–76:
  `DiscoveryBeaconServer._get_payload()` broadcasts `auth_token` in plaintext over UDP broadcast to `255.255.255.255:8765` and local subnet broadcast `X.X.X.255:8765`.
- `dispatch/sync/receiver.py` lines 122–160:
  `get_pairing_config()` accepts `pin: Optional[str]`, `auth_token: Optional[str]`, `x_pairing_pin: Optional[str]`, and checks `is_localhost`.
  If unauthenticated LAN client calls it, it throws HTTP 403 (or 401).
- `dispatch/web/templates/index.html` lines 270–330 & 968–990:
  The PC web dashboard renders a "Pair Phone With Laptop" modal with:
  1. Live QR code encoding `dispatch://pair?lan=...&token=...&pin=...`
  2. Device Auth Secret Token
  3. Local LAN Endpoint
  4. 1-Click Pairing String

---

## 2. Logic Chain

1. **Vulnerability in Current Flow**:
   - PC UDP beacon (`discovery.py:74`) broadcasts `auth_token` in cleartext on port 8765.
   - Any device on the same LAN can sniff UDP port 8765 or call `GET /api/sync/pairing/config` to harvest the master token and YouTube OAuth secrets.
   - This directly fails Acceptance Criterion: *"Unauthenticated requests to `/api/sync/pairing/config` do not leak authentication tokens to arbitrary LAN sniffers."*

2. **Consequence of Backend Hardening on Android**:
   - Explorer M1_1 is securing the backend:
     - UDP beacon will advertise ONLY server presence (`lan_url`, `ip`, `port`, `hostname`), omitting `auth_token`.
     - `/api/sync/pairing/config` will reject unauthenticated LAN requests with HTTP 401 Unauthorized (unless valid `x-pairing-pin` or `x-auth-token` header is provided, or request originates from localhost).
   - If Android is not updated:
     - `discoverViaUdpBroadcast()` will parse `token = null` and leave `pairingManager.authToken` blank.
     - `sweepSubnet()` will find the server IP on port 8000 via `/api/sync/ping`, but calling `ensureAuthToken()` will make an unauthenticated request to `/api/sync/pairing/config`, which returns 401 Unauthorized.
     - `ResumableSyncWorker.kt:32` will fail with 401.
     - All subsequent chunked uploads will fail because `pairingManager.authToken` is empty.
     - The Android app becomes completely unable to sync.

3. **Separation of Discovery vs. Authentication**:
   - **Discovery** answers: *"Where is the PC server on the local network?"*
     - UDP probe on port 8765 and /24 subnet sweep on port 8000 locate the server IP (`http://192.168.0.101:8000`).
     - This requires ZERO secret exchange. The ping endpoint (`/api/sync/ping`) and UDP presence packet (`DISPATCH_ANNOUNCE`) are unauthenticated presence beacons.
   - **Authentication / Pairing** answers: *"Is this phone authorized to upload and retrieve tokens?"*
     - Handshake occurs once during initial onboarding using a shared physical secret (either the 6-digit pairing PIN shown on the creator's PC monitor, or scanning the QR code from the PC web dashboard).
     - Once paired, the 256-bit cryptographically secure device auth token is persisted in Android `SharedPreferences`.

4. **Production Pairing Architecture**:
   - **First-Time Pairing (Creator Setup)**:
     - Method 1 (Direct PIN Entry in App):
       - App discovers server IP automatically (`http://192.168.0.101:8000`).
       - If `pairingManager.authToken` is blank, SettingsScreen displays:
         `PC Discovered at 192.168.0.101:8000 (Unpaired)`
         `Enter 6-Digit PIN from PC Dashboard: [ ______ ] [Pair]`
       - User types the PIN shown on the PC screen (e.g. `583921`) and taps `[Pair]`.
       - App calls `GET $discoveredUrl/api/sync/pairing/config` with header `x-pairing-pin: 583921`.
       - Server returns 200 OK with `auth_token` and YouTube credentials.
       - App saves `authToken` to `PairingManager`.
     - Method 2 (QR Code / Deep Link Scan):
       - User clicks "Pair Phone (QR)" on the PC dashboard.
       - User scans the QR code using phone camera / Google Lens.
       - Deep link `dispatch://pair?lan=...&token=...` opens `MainActivity`.
       - `MainActivity` calls `pairingManager.saveFromConnectionString(uriString)`.
     - Method 3 (Manual Token / Connection String Paste):
       - SettingsScreen includes a field to paste the 1-click pairing URI or token.
   - **Subsequent Automatic Sessions (Zero User Friction)**:
     - On any subsequent run (or when PC IP changes on DHCP):
     - Android runs `networkDiscovery.discoverAndConnect()`.
     - UDP probe or /24 subnet sweep locates the PC's new IP in < 1.2s.
     - Android calls `ensureAuthToken(discoveredUrl)` passing header `x-auth-token: pairingManager.authToken`.
     - Server validates the existing token and returns 200 OK.
     - Android updates `lanHost` and refreshes YouTube tokens.
     - No manual intervention or re-entering PIN is required!

---

## 3. Caveats

1. **Subnet Sweep Scope**: The subnet sweep currently probes 19 priority octets. In large or non-standard subnets where the PC is assigned an IP outside these 19 octets (e.g. `.77`), discovery relies on the UDP broadcast beacon (port 8765). If a Wi-Fi router disables multicast/broadcast AND uses a non-standard DHCP pool, the user can manually enter the PC IP in SettingsScreen.
2. **ZXing / MLKit Dependency**: The Android app does not currently include heavy barcode scanner libraries (ZXing or Google MLKit) in `build.gradle.kts`. Rather than bloating APK size with 30MB+ scanner models, handling the standard Android deep link `dispatch://pair` via `AndroidManifest.xml` intent-filter allows any camera app (built-in POCO C65 camera, Google Lens, or system QR scanner) to open Dispatch with 1 tap, alongside direct 6-digit PIN entry in `SettingsScreen`.
3. **No Caveats** regarding protocol compatibility: The proposed PIN and header format (`x-pairing-pin` and `x-auth-token`) matches `dispatch/sync/receiver.py` existing design.

---

## 4. Conclusion & Concrete Design Recommendations

### 4.1 Required Changes in Android Client

#### 1. `PairingManager.kt` (`android/app/src/main/java/com/resolvia/dispatch/data/PairingManager.kt`)
Add dedicated method `pairWithPin`:
```kotlin
fun pairWithPin(baseUrl: String, pin: String, onResult: (Boolean, String) -> Unit) {
    val trimmed = baseUrl.trim().trimEnd('/')
    val targetBase = when {
        trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
        trimmed.contains(":") -> "http://$trimmed"
        else -> "http://$trimmed:8000"
    }

    Thread {
        try {
            val client = okhttp3.OkHttpClient.Builder()
                .connectTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                .build()

            val req = okhttp3.Request.Builder()
                .url("$targetBase/api/sync/pairing/config")
                .addHeader("x-pairing-pin", pin.trim())
                .get()
                .build()

            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    val code = resp.code
                    val msg = if (code == 401 || code == 403) "Invalid pairing PIN" else "HTTP $code"
                    onResult(false, msg)
                    return@use
                }
                val jsonStr = resp.body?.string() ?: ""
                val json = com.google.gson.JsonParser.parseString(jsonStr).asJsonObject

                val lanUrl = json.get("lan_url")?.asString ?: targetBase
                val tailscaleUrl = json.get("tailscale_url")?.asString ?: ""
                val token = json.get("auth_token")?.asString ?: ""
                val connStr = json.get("connection_string")?.asString ?: ""
                val ytToken = json.get("yt_token")?.asString ?: ""
                val ytRefresh = json.get("yt_refresh")?.asString ?: ""
                val ytCid = json.get("yt_client_id")?.asString ?: ""
                val ytCsec = json.get("yt_client_secret")?.asString ?: ""

                lanHost = lanUrl
                if (tailscaleUrl.isNotBlank()) tailscaleHost = tailscaleUrl
                if (token.isNotBlank()) authToken = token
                if (ytToken.isNotBlank()) youtubeAccessToken = ytToken
                if (ytRefresh.isNotBlank()) youtubeRefreshToken = ytRefresh
                if (ytCid.isNotBlank()) youtubeClientId = ytCid
                if (ytCsec.isNotBlank()) youtubeClientSecret = ytCsec

                onResult(true, "Successfully paired with $lanUrl")
            }
        } catch (e: Exception) {
            onResult(false, "Connection error: ${e.message}")
        }
    }.start()
}
```

#### 2. `NetworkDiscovery.kt` (`android/app/src/main/java/com/resolvia/dispatch/data/NetworkDiscovery.kt`)
Update `discoverViaUdpBroadcast()` and `ensureAuthToken()`:
1. In `discoverViaUdpBroadcast()`: Do not require `token` to consider discovery successful. If `token` is absent or null in UDP response, still return `lanUrl`.
2. In `ensureAuthToken(baseUrl: String)`:
```kotlin
fun ensureAuthToken(baseUrl: String): Boolean {
    // Only query if we already have an auth token or PIN to authenticate
    val currentToken = pairingManager.authToken
    if (currentToken.isBlank()) {
        Log.d(tag, "ensureAuthToken: device not paired yet, skipping unauthenticated call")
        return false
    }

    return try {
        val req = Request.Builder()
            .url("${baseUrl.trimEnd('/')}/api/sync/pairing/config")
            .addHeader("x-auth-token", currentToken)
            .get()
            .build()

        fastProbeClient.newCall(req).execute().use { resp ->
            if (resp.isSuccessful) {
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                val token = body.get("auth_token")?.asString ?: ""
                val ytToken = body.get("yt_token")?.asString ?: ""
                val ytRefresh = body.get("yt_refresh")?.asString ?: ""
                val ytCid = body.get("yt_client_id")?.asString ?: ""
                val ytCsec = body.get("yt_client_secret")?.asString ?: ""

                if (token.isNotBlank()) pairingManager.authToken = token
                if (ytToken.isNotBlank()) pairingManager.youtubeAccessToken = ytToken
                if (ytRefresh.isNotBlank()) pairingManager.youtubeRefreshToken = ytRefresh
                if (ytCid.isNotBlank()) pairingManager.youtubeClientId = ytCid
                if (ytCsec.isNotBlank()) pairingManager.youtubeClientSecret = ytCsec
                return true
            }
            false
        }
    } catch (_: Exception) {
        false
    }
}
```

#### 3. `ResumableSyncWorker.kt` (`android/app/src/main/java/com/resolvia/dispatch/sync/ResumableSyncWorker.kt`)
Remove the insecure unauthenticated call to `/api/sync/pairing/config` on lines 30–42.
If `pairingManager.authToken.isBlank()`, return `Result.retry()` until paired.

#### 4. `SettingsScreen.kt` (`android/app/src/main/java/com/resolvia/dispatch/ui/screens/SettingsScreen.kt`)
Add PIN Pairing section in the PC Connection card:
- When `!pairingManager.isPaired`:
  - Show input field `pinInput` (6 numeric characters).
  - Show `[Pair with PIN]` button that calls `pairingManager.pairWithPin(hostInput, pinInput)`.
  - Show `[Paste Pairing URI]` button that reads clipboard and calls `pairingManager.saveFromConnectionString(...)`.
- When `pairingManager.isPaired`:
  - Display green "Paired & Connected" badge.
  - Option to "Unpair / Reset credentials".

#### 5. `AndroidManifest.xml` & `MainActivity.kt`
Add intent-filter in `AndroidManifest.xml` under `MainActivity`:
```xml
<intent-filter>
    <action android:name="android.intent.action.VIEW" />
    <category android:name="android.intent.category.DEFAULT" />
    <category android:name="android.intent.category.BROWSABLE" />
    <data android:scheme="dispatch" android:host="pair" />
</intent-filter>
```
In `MainActivity.kt`, handle incoming intent:
```kotlin
override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    handlePairingIntent(intent)
}

private fun handlePairingIntent(intent: Intent?) {
    val data = intent?.dataString ?: return
    if (data.startsWith("dispatch://pair")) {
        if (pairingManager.saveFromConnectionString(data)) {
            Toast.makeText(this, "Paired successfully from QR / Link!", Toast.LENGTH_SHORT).show()
        }
    }
}
```

---

## 5. Verification Method

### 5.1 Verification Commands & Invalidation Conditions

1. **Verification of Backend Security & No LAN Secret Leakage**:
   - Command:
     ```powershell
     python -c "import requests; r = requests.get('http://127.0.0.1:8000/api/sync/pairing/config'); print('Localhost:', r.status_code)"
     python -c "import requests; r = requests.get('http://192.168.0.101:8000/api/sync/pairing/config'); print('LAN unauth:', r.status_code)"
     ```
   - Invalidation condition: If `LAN unauth` returns HTTP 200 with `auth_token`, security invariant is broken. Must return HTTP 401 or 403.

2. **Verification of UDP Beacon Absence of Auth Token**:
   - Send `DISPATCH_DISCOVER` via UDP to 127.0.0.1:8765:
     ```python
     import socket, json
     s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
     s.sendto(b"DISPATCH_DISCOVER", ("127.0.0.1", 8765))
     data, _ = s.recvfrom(2048)
     payload = json.loads(data.decode())
     assert "auth_token" not in payload or payload["auth_token"] == "", "Token leaked in UDP beacon!"
     ```
   - Invalidation condition: `auth_token` key is non-empty in UDP packet.

3. **Verification of PIN Pairing Handshake**:
   - Fetch PIN on server: `python -c "from dispatch import db; print(db.get_setting('pairing_pin'))"`
   - Test PIN pairing from remote LAN client:
     ```powershell
     python -c "import requests; r = requests.get('http://<ip>:8000/api/sync/pairing/config', headers={'x-pairing-pin': '<PIN>'}); print(r.status_code, 'token:', 'auth_token' in r.json())"
     ```
   - Expect HTTP 200 and `"auth_token" in r.json() == True`.

4. **Android Build Verification**:
   - Command:
     ```powershell
     cd c:\CODE\Dispatch\android; .\gradlew.bat assembleDebug --warning-mode all
     ```
   - Expected Result: `BUILD SUCCESSFUL` with 0 errors.

5. **Test Suite Verification**:
   - Command:
     ```powershell
     python -m unittest discover -s tests -p "test_*.py"
     ```
   - Expected Result: 0 failures, 0 errors.
