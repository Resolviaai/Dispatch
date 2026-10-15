package com.resolvia.dispatch.bridge

import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.data.durationSeconds
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.SegmenterEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Dispatch Native Android Bridge linking the canonical React frontend
 * running in Android WebView to native CameraX, WorkManager, Room SQLite,
 * and background YouTube direct sync workers.
 */
class DispatchNativeBridge(
    private val activity: ComponentActivity,
    private val webView: WebView,
    private val segmenterEngine: SegmenterEngine,
    private val cameraCaptureManager: CameraCaptureManager,
    private val database: AppDatabase,
    private val pairingManager: PairingManager
) {
    private val gson = Gson()
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    @JavascriptInterface
    fun isNativeAndroid(): Boolean = true

    @JavascriptInterface
    fun getDeviceModel(): String = "${Build.MANUFACTURER} ${Build.MODEL}"

    @JavascriptInterface
    fun startRecording() {
        activity.runOnUiThread {
            activity.lifecycleScope.launch {
                try {
                    segmenterEngine.startSession(
                        cameraManager = cameraCaptureManager,
                        notes = "Dispatch Studio Android Capture"
                    )
                } catch (e: Exception) {
                    android.util.Log.e("DispatchBridge", "startRecording failed: ${e.message}", e)
                    android.widget.Toast.makeText(activity, "Recording failed: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    @JavascriptInterface
    fun stopRecording() {
        activity.runOnUiThread {
            activity.lifecycleScope.launch {
                try {
                    segmenterEngine.stopSession(cameraManager = cameraCaptureManager)
                    segmenterEngine.triggerYouTubeUpload()
                    cameraCaptureManager.activePreviewView?.let { pv ->
                        cameraCaptureManager.initializeCamera(activity, pv)
                    }
                } catch (e: Exception) {
                    android.util.Log.e("DispatchBridge", "stopRecording failed: ${e.message}", e)
                }
            }
        }
    }

    @JavascriptInterface
    fun isRecording(): Boolean {
        return segmenterEngine.isSessionActive || cameraCaptureManager.isRecording
    }

    @JavascriptInterface
    fun switchCamera() {
        activity.runOnUiThread {
            activity.lifecycleScope.launch {
                try {
                    cameraCaptureManager.toggleLensFacing()
                } catch (e: Exception) {
                    android.util.Log.w("DispatchBridge", "switchCamera failed: ${e.message}")
                }
            }
        }
    }

    @JavascriptInterface
    fun toggleTorch(): Boolean {
        return try {
            cameraCaptureManager.toggleTorch()
        } catch (e: Exception) {
            false
        }
    }

    @JavascriptInterface
    fun toggleMic(): Boolean {
        return try {
            cameraCaptureManager.toggleMic()
        } catch (e: Exception) {
            true
        }
    }

    @JavascriptInterface
    fun setZoom(ratio: Float): Float {
        return try {
            cameraCaptureManager.setZoomRatio(ratio)
            ratio
        } catch (e: Exception) {
            1.0f
        }
    }

    @JavascriptInterface
    fun getRecordings(): String {
        return try {
            runBlocking(Dispatchers.IO) {
                val segments = database.recordingDao().getAllSegmentsList()
                val jsonArr = JsonArray()
                for (s in segments) {
                    val obj = JsonObject().apply {
                        addProperty("segmentId", s.segmentId)
                        addProperty("sessionId", s.sessionId)
                        addProperty("sequenceNumber", s.sequenceNumber)
                        addProperty("filename", s.filename)
                        addProperty("filepath", s.filepath)
                        addProperty("fileSizeBytes", s.fileSizeBytes)
                        addProperty("sha256Hash", s.sha256Hash)
                        addProperty("status", s.status)
                        addProperty("createdAt", s.createdAt)
                        addProperty("finalizedAt", s.finalizedAt)
                        addProperty("durationSeconds", s.durationSeconds)
                        addProperty("youtubeVideoId", s.youtubeVideoId)
                    }
                    jsonArr.add(obj)
                }
                jsonArr.toString()
            }
        } catch (e: Exception) {
            android.util.Log.e("DispatchBridge", "getRecordings failed: ${e.message}")
            "[]"
        }
    }

    @JavascriptInterface
    fun getYouTubeStatus(): String {
        val configured = pairingManager.isYouTubeConfigured
        if (!configured) {
            return JsonObject().apply {
                addProperty("connected", false)
                addProperty("verified", false)
            }.toString()
        }

        // Return verified channel info if cached, or verify asynchronously
        val cachedTitle = pairingManager.youtubeChannelTitle
        val cachedId = pairingManager.youtubeChannelId
        val isVerified = cachedTitle.isNotBlank() && cachedId.isNotBlank()

        val obj = JsonObject().apply {
            addProperty("connected", isVerified)
            addProperty("verified", isVerified)
            if (isVerified) {
                addProperty("channelTitle", cachedTitle)
                addProperty("channelId", cachedId)
                addProperty("account", cachedTitle)
            } else {
                addProperty("channelTitle", "Unverified Channel")
                addProperty("message", "Credentials present but channel unverified. Reconnect to refresh.")
            }
        }
        return obj.toString()
    }

    private fun verifyYouTubeChannelRemote(accessToken: String): Pair<String, String>? {
        return try {
            val req = Request.Builder()
                .url("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true")
                .header("Authorization", "Bearer $accessToken")
                .get()
                .build()
            httpClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val bodyStr = resp.body?.string() ?: return null
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                val items = json.getAsJsonArray("items")
                if (items != null && items.size() > 0) {
                    val item = items.get(0).asJsonObject
                    val id = item.get("id")?.asString ?: ""
                    val title = item.getAsJsonObject("snippet")?.get("title")?.asString ?: "YouTube Channel"
                    if (id.isNotBlank()) Pair(title, id) else null
                } else null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun refreshAccessToken(): String? {
        val refreshToken = pairingManager.youtubeRefreshToken
        val clientId = pairingManager.youtubeClientId
        val clientSecret = pairingManager.youtubeClientSecret
        if (refreshToken.isBlank() || clientId.isBlank() || clientSecret.isBlank()) return null

        val formBody = okhttp3.FormBody.Builder()
            .add("client_id", clientId)
            .add("client_secret", clientSecret)
            .add("refresh_token", refreshToken)
            .add("grant_type", "refresh_token")
            .build()

        val req = Request.Builder()
            .url("https://oauth2.googleapis.com/token")
            .post(formBody)
            .build()

        return try {
            httpClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                val newAccessToken = body.get("access_token")?.asString
                if (!newAccessToken.isNullOrBlank()) {
                    pairingManager.youtubeAccessToken = newAccessToken
                    newAccessToken
                } else null
            }
        } catch (_: Exception) {
            null
        }
    }

    @JavascriptInterface
    fun connectYouTube(pcHost: String, pairingCode: String): String {
        return try {
            if (pairingCode.isBlank()) {
                return JsonObject().apply {
                    addProperty("success", false)
                    addProperty("message", "Enter the one-time pairing code shown in PC Settings.")
                }.toString()
            }
            val cleanHost = pcHost.trim().removePrefix("http://").removePrefix("https://").trimEnd('/')
            val url = "http://$cleanHost/api/youtube/credentials"
            val payload = JsonObject().apply { addProperty("pairing_code", pairingCode.trim()) }
            val body = payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
            val req = Request.Builder().url(url).post(body).build()
            httpClient.newCall(req).execute().use { resp ->
                val bodyText = resp.body?.string() ?: ""
                val response = try {
                    gson.fromJson(bodyText, JsonObject::class.java)
                } catch (_: Exception) {
                    JsonObject()
                }
                if (!resp.isSuccessful) {
                    val detail = response.get("detail")?.takeIf { it.isJsonPrimitive }?.asString
                    val message = when (resp.code) {
                        401 -> detail ?: "Pairing code is invalid or expired. Create a new one on the PC."
                        403 -> detail ?: "Create the pairing code from the PC dashboard."
                        409 -> detail ?: "YouTube is not configured on the PC. Sign in there first."
                        429 -> detail ?: "Too many attempts. Wait before trying again."
                        else -> detail ?: "PC pairing failed (HTTP ${resp.code})."
                    }
                    return JsonObject().apply {
                        addProperty("success", false)
                        addProperty("message", message)
                    }.toString()
                }

                val refreshToken = response.get("refresh_token")?.asString ?: ""
                val clientId = response.get("client_id")?.asString ?: ""
                val clientSecret = response.get("client_secret")?.asString ?: ""
                if (response.get("configured")?.asBoolean == true &&
                    refreshToken.isNotBlank() && clientId.isNotBlank() && clientSecret.isNotBlank()
                ) {
                    pairingManager.saveYouTubeCredentials(refreshToken, clientId, clientSecret)

                    // Verify account against YouTube API directly
                    val freshToken = refreshAccessToken()
                    val channelInfo = if (freshToken != null) verifyYouTubeChannelRemote(freshToken) else null
                    if (channelInfo != null) {
                        pairingManager.youtubeChannelTitle = channelInfo.first
                        pairingManager.youtubeChannelId = channelInfo.second

                        // Resume any waiting segments in Room DB
                        activity.lifecycleScope.launch(Dispatchers.IO) {
                            try {
                                val waiting = database.recordingDao().getPendingUploadSegments()
                                for (seg in waiting) {
                                    if (seg.status == "WAITING_FOR_YOUTUBE") {
                                        database.recordingDao().updateSegmentStatus(seg.segmentId, "QUEUED_FOR_UPLOAD")
                                    }
                                }
                                segmenterEngine.triggerYouTubeUpload()
                            } catch (_: Exception) {}
                        }

                        JsonObject().apply {
                            addProperty("success", true)
                            addProperty("channelTitle", channelInfo.first)
                            addProperty("message", "Connected to verified YouTube channel: ${channelInfo.first}")
                        }.toString()
                    } else {
                        JsonObject().apply {
                            addProperty("success", false)
                            addProperty("message", "Credentials received but YouTube account verification failed. Check Google OAuth permissions.")
                        }.toString()
                    }
                } else {
                    JsonObject().apply {
                        addProperty("success", false)
                        addProperty("message", "The PC did not return complete YouTube credentials. Sign in there and create a new code.")
                    }.toString()
                }
            }
        } catch (e: Exception) {
            JsonObject().apply {
                addProperty("success", false)
                addProperty("message", "Could not reach the PC. Check its address and that both devices use the same trusted network.")
            }.toString()
        }
    }

    @JavascriptInterface
    fun disconnectYouTube() {
        pairingManager.clearCredentials()
    }

    @JavascriptInterface
    fun retryUpload(segmentId: String) {
        activity.lifecycleScope.launch(Dispatchers.IO) {
            database.recordingDao().updateSegmentStatus(segmentId, "QUEUED_FOR_UPLOAD")
            segmenterEngine.triggerYouTubeUpload()
        }
    }

    @JavascriptInterface
    fun getWifiOnly(): Boolean = pairingManager.isWifiOnlyEnabled

    @JavascriptInterface
    fun setWifiOnly(enabled: Boolean) {
        pairingManager.isWifiOnlyEnabled = enabled
    }

    @JavascriptInterface
    fun getStorageInfo(): String {
        return try {
            val storagePath = activity.getExternalFilesDir(null)?.absolutePath ?: activity.filesDir.absolutePath
            val stat = StatFs(storagePath)
            val bytesAvailable = stat.availableBlocksLong * stat.blockSizeLong
            val bytesTotal = stat.blockCountLong * stat.blockSizeLong
            val freeGb = (bytesAvailable / (1024 * 1024 * 1024L)).toInt()
            val totalGb = (bytesTotal / (1024 * 1024 * 1024L)).toInt()
            val obj = JsonObject().apply {
                addProperty("freeGb", freeGb)
                addProperty("totalGb", totalGb)
            }
            obj.toString()
        } catch (e: Exception) {
            "{\"freeGb\": 48, \"totalGb\": 128}"
        }
    }

    @JavascriptInterface
    fun pingPc(pcHost: String): String {
        return try {
            val cleanHost = pcHost.trim().removePrefix("http://").removePrefix("https://").trimEnd('/')
            val url = "http://$cleanHost/api/status"
            val req = Request.Builder().url(url).build()
            val resp = httpClient.newCall(req).execute()
            if (resp.isSuccessful) {
                JsonObject().apply {
                    addProperty("success", true)
                    addProperty("message", "PC Server reachable (${resp.code})")
                }.toString()
            } else {
                JsonObject().apply {
                    addProperty("success", false)
                    addProperty("message", "PC returned HTTP ${resp.code}")
                }.toString()
            }
        } catch (e: Exception) {
            JsonObject().apply {
                addProperty("success", false)
                addProperty("message", "Unreachable: ${e.message}")
            }.toString()
        }
    }
}
