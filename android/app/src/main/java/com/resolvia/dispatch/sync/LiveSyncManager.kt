package com.resolvia.dispatch.sync

import android.content.Context
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.NetworkDiscovery
import com.resolvia.dispatch.data.PairingManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.FileInputStream
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class SyncState(
    val isSyncing: Boolean = false,
    val currentSegmentId: String? = null,
    val currentBytes: Long = 0L,
    val totalBytes: Long = 0L,
    val percent: Int = 0,
    val speedMbps: Double = 0.0,
    val message: String = "Idle",
    val error: String? = null,
    val lastSuccessSegmentId: String? = null,
    val connectedHost: String? = null,
    val isServerOnline: Boolean = false
)

class LiveSyncManager(private val context: Context) {

    private val db = AppDatabase.getDatabase(context)
    private val dao = db.recordingDao()
    private val pairingManager = PairingManager(context)
    val networkDiscovery = NetworkDiscovery(context, pairingManager)
    private val gson = Gson()

    private val client = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val _syncState = MutableStateFlow(SyncState())
    val syncState: StateFlow<SyncState> = _syncState.asStateFlow()

    /**
     * Checks if PC server is reachable, auto-discovering if necessary.
     */
    suspend fun probeServerStatus(): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val serverUrl = networkDiscovery.discoverAndConnect(timeoutMs = 1500L)
        if (serverUrl != null) {
            _syncState.value = _syncState.value.copy(
                connectedHost = serverUrl,
                isServerOnline = true
            )
            Pair(true, serverUrl)
        } else {
            _syncState.value = _syncState.value.copy(
                isServerOnline = false
            )
            Pair(false, pairingManager.lanHost)
        }
    }

    suspend fun syncNow(): Boolean = withContext(Dispatchers.IO) {
        if (_syncState.value.isSyncing) return@withContext true

        _syncState.value = _syncState.value.copy(
            isSyncing = true,
            error = null,
            message = "Auto-discovering PC on Wi-Fi..."
        )

        // 1. Auto-discover active server (UDP beacon, subnet sweep, or known candidates)
        val activeUrl = findReachableServer()
        if (activeUrl == null) {
            _syncState.value = _syncState.value.copy(
                isSyncing = false,
                isServerOnline = false,
                error = "Cannot find PC. Ensure PC is on same Wi-Fi with Dispatch running.",
                message = "PC unreachable"
            )
            return@withContext false
        }

        _syncState.value = _syncState.value.copy(
            connectedHost = activeUrl,
            isServerOnline = true
        )

        // Auto-fetch auth token if missing
        networkDiscovery.ensureAuthToken(activeUrl)

        val pendingItems = dao.getPendingOutboxItems()
        if (pendingItems.isEmpty()) {
            _syncState.value = _syncState.value.copy(
                isSyncing = false,
                message = "All clips up to date",
                percent = 100
            )
            return@withContext true
        }

        for (item in pendingItems) {
            val file = File(context.getExternalFilesDir(null), "recordings/${item.segmentId}.mp4")
            if (!file.exists()) {
                dao.deleteOutboxItem(item.segmentId)
                continue
            }

            val totalBytes = file.length()
            val segmentName = item.segmentId

            _syncState.value = _syncState.value.copy(
                isSyncing = true,
                currentSegmentId = segmentName,
                totalBytes = totalBytes,
                currentBytes = 0L,
                percent = 0,
                message = "Uploading $segmentName...",
                error = null
            )

            // Try resumable chunked upload first
            var success = uploadSegmentWithProgress(
                baseUrl = activeUrl,
                segmentId = item.segmentId,
                sessionId = item.sessionId,
                file = file
            )

            // Fallback: If resumable chunked encountered edge-case failure, attempt direct multipart upload
            if (!success) {
                _syncState.value = _syncState.value.copy(
                    message = "Retrying via direct upload for $segmentName..."
                )
                success = uploadSegmentDirect(
                    baseUrl = activeUrl,
                    segmentId = item.segmentId,
                    sessionId = item.sessionId,
                    file = file
                )
            }

            if (!success) {
                _syncState.value = _syncState.value.copy(
                    isSyncing = false,
                    error = "Failed uploading $segmentName. Tap to retry.",
                    message = "Sync interrupted"
                )
                return@withContext false
            }

            // Cleanup local file ONLY after confirmed cryptographic proof-of-receipt from PC
            val sha256 = calculateSha256(file)
            val isVerified = verifyWithServer(activeUrl, item.segmentId, sha256, totalBytes)
            if (isVerified) {
                file.delete()
                dao.updateSegmentStatus(item.segmentId, "UPLOADED_TO_PC")
                dao.deleteOutboxItem(item.segmentId)
            } else {
                android.util.Log.w("LiveSyncManager", "Proof-of-receipt check failed for ${item.segmentId}. Preserving local file.")
                _syncState.value = _syncState.value.copy(
                    isSyncing = false,
                    error = "Proof-of-receipt verification failed for $segmentName. Preserving local file.",
                    message = "Verification failed"
                )
                return@withContext false
            }

            _syncState.value = _syncState.value.copy(
                lastSuccessSegmentId = segmentName,
                percent = 100,
                message = "Uploaded $segmentName"
            )
        }

        _syncState.value = _syncState.value.copy(
            isSyncing = false,
            message = "Sync complete. All clips on PC dashboard.",
            percent = 100
        )
        true
    }

    private suspend fun findReachableServer(): String? {
        // Step 1: Active auto-discovery (UDP broadcast + Subnet sweep + known lanHost)
        val discovered = networkDiscovery.discoverAndConnect(timeoutMs = 2000L)
        if (discovered != null) return discovered

        // Step 2: Probe candidate endpoints
        val endpoints = pairingManager.getCandidateEndpoints()
        for (url in endpoints) {
            if (networkDiscovery.pingEndpoint(url)) {
                networkDiscovery.ensureAuthToken(url)
                pairingManager.lanHost = url
                return url
            }
        }
        return null
    }

    private fun uploadSegmentWithProgress(
        baseUrl: String,
        segmentId: String,
        sessionId: String,
        file: File
    ): Boolean {
        val totalBytes = file.length()
        val sha256 = calculateSha256(file)

        // 1. Query remote offset
        val initJson = JsonObject().apply {
            addProperty("session_id", sessionId)
            addProperty("segment_id", segmentId)
            addProperty("filename", file.name)
            addProperty("file_size_bytes", totalBytes)
            addProperty("sha256_hash", sha256)
            addProperty("auth_token", pairingManager.authToken)
        }

        var remoteOffset = 0L
        try {
            val initReq = Request.Builder()
                .url("$baseUrl/api/sync/upload/init")
                .post(initJson.toString().toRequestBody("application/json".toMediaType()))
                .build()

            client.newCall(initReq).execute().use { resp ->
                if (resp.code == 401) {
                    networkDiscovery.ensureAuthToken(baseUrl)
                    return false
                }
                if (!resp.isSuccessful) return false
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                if (body.get("status")?.asString == "already_completed") {
                    return true
                }
                remoteOffset = body.get("remote_offset")?.asLong ?: 0L
            }
        } catch (_: Exception) {
            return false
        }

        // 2. Stream chunked bytes with accurate progress tracking
        val chunkSize = 512 * 1024 // 512 KB slices for high throughput
        val buffer = ByteArray(chunkSize)
        var lastTime = System.currentTimeMillis()
        var bytesSinceLastTime = 0L

        try {
            RandomAccessFile(file, "r").use { raf ->
                raf.seek(remoteOffset)

                while (remoteOffset < totalBytes) {
                    val bytesToRead = minOf(chunkSize.toLong(), totalBytes - remoteOffset).toInt()
                    raf.readFully(buffer, 0, bytesToRead)

                    val chunkReq = Request.Builder()
                        .url("$baseUrl/api/sync/upload/chunk")
                        .patch(buffer.copyOf(bytesToRead).toRequestBody("application/octet-stream".toMediaType()))
                        .addHeader("x-segment-id", segmentId)
                        .addHeader("x-upload-offset", remoteOffset.toString())
                        .addHeader("x-file-size", totalBytes.toString())
                        .addHeader("x-sha256", sha256)
                        .addHeader("x-session-id", sessionId)
                        .addHeader("x-auth-token", pairingManager.authToken)
                        .build()

                    client.newCall(chunkReq).execute().use { chunkResp ->
                        if (chunkResp.code == 401) {
                            networkDiscovery.ensureAuthToken(baseUrl)
                            return false
                        }
                        if (chunkResp.code == 409) {
                            android.util.Log.w("LiveSyncManager", "Offset mismatch (409) on $segmentId at offset $remoteOffset. Re-fetching server offset.")
                            var resumed = false
                            try {
                                val reInitReq = Request.Builder()
                                    .url("$baseUrl/api/sync/upload/init")
                                    .post(initJson.toString().toRequestBody("application/json".toMediaType()))
                                    .build()
                                client.newCall(reInitReq).execute().use { reResp ->
                                    if (reResp.isSuccessful) {
                                        val reBody = gson.fromJson(reResp.body?.string(), JsonObject::class.java)
                                        if (reBody.get("status")?.asString == "already_completed") {
                                            return true
                                        }
                                        val serverOffset = reBody.get("remote_offset")?.asLong ?: 0L
                                        remoteOffset = serverOffset
                                        raf.seek(remoteOffset)
                                        resumed = true
                                    }
                                }
                            } catch (_: Exception) {}
                            if (!resumed) return false
                            return@use
                        }
                        if (!chunkResp.isSuccessful) return false
                        val chunkBody = gson.fromJson(chunkResp.body?.string(), JsonObject::class.java)
                        remoteOffset = chunkBody.get("remote_offset")?.asLong ?: (remoteOffset + bytesToRead)

                        bytesSinceLastTime += bytesToRead
                        val now = System.currentTimeMillis()
                        val elapsed = now - lastTime
                        var speedMbps = 0.0
                        if (elapsed >= 500) {
                            speedMbps = (bytesSinceLastTime * 8.0) / (elapsed / 1000.0) / 1_000_000.0
                            lastTime = now
                            bytesSinceLastTime = 0L
                        }

                        val pct = ((remoteOffset.toDouble() / totalBytes.toDouble()) * 100).toInt().coerceIn(0, 100)
                        _syncState.value = _syncState.value.copy(
                            currentBytes = remoteOffset,
                            percent = pct,
                            speedMbps = if (speedMbps > 0) speedMbps else _syncState.value.speedMbps,
                            message = "Uploading: ${pct}% (${formatBytes(remoteOffset)} / ${formatBytes(totalBytes)})"
                        )

                        if (chunkBody.get("status")?.asString == "completed" && chunkBody.get("verified")?.asBoolean == true) {
                            return true
                        }
                    }
                }
            }
        } catch (_: Exception) {
            return false
        }

        return true
    }

    /**
     * Direct multipart streaming fallback upload.
     */
    private fun uploadSegmentDirect(
        baseUrl: String,
        segmentId: String,
        sessionId: String,
        file: File
    ): Boolean {
        try {
            val mediaType = "video/mp4".toMediaType()
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("segment_id", segmentId)
                .addFormDataPart("session_id", sessionId)
                .addFormDataPart("auth_token", pairingManager.authToken)
                .addFormDataPart("file", file.name, file.asRequestBody(mediaType))
                .build()

            val req = Request.Builder()
                .url("$baseUrl/api/sync/upload/direct")
                .addHeader("x-auth-token", pairingManager.authToken)
                .post(requestBody)
                .build()

            client.newCall(req).execute().use { resp ->
                if (resp.code == 401) {
                    networkDiscovery.ensureAuthToken(baseUrl)
                    return false
                }
                return resp.isSuccessful
            }
        } catch (_: Exception) {
            return false
        }
    }

    private fun verifyWithServer(baseUrl: String, segmentId: String, sha256: String, fileSize: Long): Boolean {
        val url = "$baseUrl/api/sync/verify-chunk?segment_id=$segmentId&sha256=$sha256&file_size=$fileSize"
        val req = Request.Builder()
            .url(url)
            .addHeader("x-auth-token", pairingManager.authToken)
            .get()
            .build()
        return try {
            client.newCall(req).execute().use { resp ->
                if (resp.code == 401) {
                    networkDiscovery.ensureAuthToken(baseUrl)
                }
                if (!resp.isSuccessful) return false
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                body?.get("verified")?.asBoolean == true
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun calculateSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { fis ->
            val buffer = ByteArray(16384)
            var bytesRead: Int
            while (fis.read(buffer).also { bytesRead = it } != -1) {
                digest.update(buffer, 0, bytesRead)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun formatBytes(bytes: Long): String {
        if (bytes < 1024 * 1024) return "${bytes / 1024} KB"
        return "%.1f MB".format(bytes / (1024.0 * 1024.0))
    }
}
