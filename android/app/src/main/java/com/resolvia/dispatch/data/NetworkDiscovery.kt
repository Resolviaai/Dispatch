package com.resolvia.dispatch.data

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Autonomous Zero-Configuration Network Discovery for Dispatch.
 * Discovers the Dispatch PC server across local Wi-Fi via:
 * 1. Immediate probe of last-known lanHost
 * 2. UDP Broadcast Beacon query (port 8765)
 * 3. Concurrent local subnet sweep (/api/sync/ping across /24 subnet)
 * 4. Automatic token reconciliation & pairing persistence
 */
class NetworkDiscovery(
    private val context: Context,
    private val pairingManager: PairingManager
) {
    private val tag = "NetworkDiscovery"
    private val gson = Gson()

    private val fastProbeClient = OkHttpClient.Builder()
        .connectTimeout(350, TimeUnit.MILLISECONDS)
        .readTimeout(500, TimeUnit.MILLISECONDS)
        .build()

    /**
     * Finds and connects to the active Dispatch PC server.
     * Updates PairingManager automatically upon discovery.
     */
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

                // Wait for whichever discovers the server first
                val discovered = selectFirstResult(listOf(udpJob, sweepJob))
                discovered
            }

            if (!result.isNullOrBlank()) {
                val validUrl = result.trimEnd('/')
                pairingManager.lanHost = validUrl
                ensureAuthToken(validUrl)
                Log.i(tag, "Successfully locked onto discovered server: $validUrl")
                return@withContext validUrl
            }
        } catch (e: Exception) {
            Log.w(tag, "Discovery race encountered error: ${e.message}")
        }

        // 3. Fallback to Tailscale if configured
        val tsHost = pairingManager.tailscaleHost.trimEnd('/')
        if (tsHost.isNotBlank() && pingEndpoint(tsHost)) {
            ensureAuthToken(tsHost)
            return@withContext tsHost
        }

        null
    }

    private suspend fun selectFirstResult(deferredList: List<Deferred<String?>>): String? {
        val found = CompletableDeferred<String?>()
        val completedCount = java.util.concurrent.atomic.AtomicInteger(0)

        deferredList.forEach { deferred ->
            CoroutineScope(Dispatchers.IO).launch {
                val res = deferred.await()
                if (!res.isNullOrBlank()) {
                    found.complete(res)
                } else {
                    if (completedCount.incrementAndGet() >= deferredList.size) {
                        found.complete(null)
                    }
                }
            }
        }

        return found.await()
    }

    /**
     * Broadcasts UDP 'DISPATCH_DISCOVER' to port 8765 and waits for JSON response.
     */
    private fun discoverViaUdpBroadcast(): String? {
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket()
            socket.broadcast = true
            socket.soTimeout = 800

            val message = "DISPATCH_DISCOVER".toByteArray()
            val broadcastAddr = InetAddress.getByName("255.255.255.255")
            val packet = DatagramPacket(message, message.size, broadcastAddr, 8765)
            socket.send(packet)

            // Also send to local subnet broadcast if available
            getLocalSubnetBroadcast()?.let { subnetBcast ->
                val subnetPacket = DatagramPacket(message, message.size, subnetBcast, 8765)
                socket.send(subnetPacket)
            }

            val buf = ByteArray(2048)
            val recvPacket = DatagramPacket(buf, buf.size)
            socket.receive(recvPacket)

            val respStr = String(recvPacket.data, 0, recvPacket.length)
            val json = gson.fromJson(respStr, JsonObject::class.java)
            val lanUrl = if (json.has("lan_url") && !json.get("lan_url").isJsonNull) json.get("lan_url").asString else null
            val token = if (json.has("auth_token") && !json.get("auth_token").isJsonNull) json.get("auth_token").asString else null

            if (!lanUrl.isNullOrBlank()) {
                if (!token.isNullOrBlank()) {
                    pairingManager.authToken = token
                }
                Log.i(tag, "UDP discovery success: $lanUrl")
                return lanUrl
            }
        } catch (e: Exception) {
            Log.d(tag, "UDP discovery timeout or error: ${e.message}")
        } finally {
            socket?.close()
        }
        return null
    }

    /**
     * Sweeps the local Wi-Fi /24 subnet for the Dispatch server port 8000 concurrently.
     */
    private suspend fun sweepSubnet(): String? = coroutineScope {
        val localIp = getDeviceIpAddress() ?: return@coroutineScope null
        val prefix = localIp.substringBeforeLast(".") + "."

        // Prioritized candidates: Common DHCP pools (.100-.115, then .1-.30)
        val candidateHosts = mutableListOf<String>()
        val priorityOctets = listOf(102, 101, 100, 103, 104, 105, 106, 107, 108, 109, 110, 2, 3, 4, 5, 10, 20, 50, 150)
        for (oct in priorityOctets) {
            candidateHosts.add("http://$prefix$oct:8000")
        }

        val foundResult = CompletableDeferred<String?>()
        val foundFlag = AtomicBoolean(false)

        val jobs = candidateHosts.map { targetUrl ->
            launch(Dispatchers.IO) {
                if (foundFlag.get()) return@launch
                if (pingEndpoint(targetUrl)) {
                    if (foundFlag.compareAndSet(false, true)) {
                        Log.i(tag, "Subnet sweep found server at $targetUrl")
                        foundResult.complete(targetUrl)
                    }
                }
            }
        }

        // Wait up to 1.2s for candidate scan
        withTimeoutOrNull(1200L) {
            foundResult.await()
        }
    }

    /**
     * Lightweight health check of target server endpoint.
     */
    fun pingEndpoint(baseUrl: String): Boolean {
        return try {
            val req = Request.Builder()
                .url("${baseUrl.trimEnd('/')}/api/sync/ping")
                .get()
                .build()

            fastProbeClient.newCall(req).execute().use { resp ->
                resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    /**
     * Retrieves auth token from server if missing or expired.
     */
    fun ensureAuthToken(baseUrl: String): Boolean {
        val currentToken = pairingManager.authToken
        val currentPin = pairingManager.pairingPin
        // Only query if we already have an auth token or pairing PIN to authenticate; skip unauthenticated calls if unpaired
        if (currentToken.isBlank() && currentPin.isBlank()) {
            Log.d(tag, "ensureAuthToken: device not paired yet, skipping unauthenticated call")
            return false
        }

        return try {
            val reqBuilder = Request.Builder()
                .url("${baseUrl.trimEnd('/')}/api/sync/pairing/config")
                .get()

            if (currentPin.isNotBlank()) {
                reqBuilder.header("x-pairing-pin", currentPin)
            }
            if (currentToken.isNotBlank()) {
                reqBuilder.header("x-auth-token", currentToken)
            }

            val req = reqBuilder.build()

            fastProbeClient.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) {
                    val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                    val token = if (body.has("auth_token") && !body.get("auth_token").isJsonNull) body.get("auth_token").asString else ""
                    val pin = if (body.has("pairing_pin") && !body.get("pairing_pin").isJsonNull) body.get("pairing_pin").asString else ""
                    val ytToken = if (body.has("yt_token") && !body.get("yt_token").isJsonNull) body.get("yt_token").asString else ""
                    val ytRefresh = if (body.has("yt_refresh") && !body.get("yt_refresh").isJsonNull) body.get("yt_refresh").asString else ""
                    val ytCid = if (body.has("yt_client_id") && !body.get("yt_client_id").isJsonNull) body.get("yt_client_id").asString else ""
                    val ytCsec = if (body.has("yt_client_secret") && !body.get("yt_client_secret").isJsonNull) body.get("yt_client_secret").asString else ""

                    if (token.isNotBlank()) pairingManager.authToken = token
                    if (pin.isNotBlank()) pairingManager.pairingPin = pin
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

    private fun getDeviceIpAddress(): String? {
        try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val ipInt = wifiManager?.connectionInfo?.ipAddress ?: 0
            if (ipInt != 0) {
                return String.format(
                    Locale.US,
                    "%d.%d.%d.%d",
                    ipInt and 0xff,
                    ipInt shr 8 and 0xff,
                    ipInt shr 16 and 0xff,
                    ipInt shr 24 and 0xff
                )
            }

            val interfaces = NetworkInterface.getNetworkInterfaces()
            for (intf in interfaces) {
                for (addr in intf.inetAddresses) {
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        val host = addr.hostAddress ?: ""
                        if (!host.startsWith("127.")) return host
                    }
                }
            }
        } catch (_: Exception) {}
        return null
    }

    private fun getLocalSubnetBroadcast(): InetAddress? {
        try {
            val ip = getDeviceIpAddress() ?: return null
            val parts = ip.split(".")
            if (parts.size == 4) {
                return InetAddress.getByName("${parts[0]}.${parts[1]}.${parts[2]}.255")
            }
        } catch (_: Exception) {}
        return null
    }
}
