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
import okhttp3.Request
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
        val obj = JsonObject().apply {
            addProperty("connected", configured)
            if (configured) {
                addProperty("channelTitle", "YouTube Channel (Direct Ingest)")
                addProperty("account", "Dispatch Creator")
            }
        }
        return obj.toString()
    }

    @JavascriptInterface
    fun connectYouTube(pcHost: String): String {
        return try {
            val cleanHost = pcHost.trim().removePrefix("http://").removePrefix("https://").trimEnd('/')
            val url = "http://$cleanHost/api/youtube/credentials"
            val req = Request.Builder().url(url).build()
            val resp = httpClient.newCall(req).execute()
            if (resp.isSuccessful) {
                val body = resp.body?.string() ?: ""
                val creds = gson.fromJson(body, JsonObject::class.java)
                val refreshToken = creds.get("refresh_token")?.asString ?: ""
                val clientId = creds.get("client_id")?.asString ?: ""
                val clientSecret = creds.get("client_secret")?.asString ?: ""

                if (refreshToken.isNotBlank() && clientId.isNotBlank() && clientSecret.isNotBlank()) {
                    pairingManager.saveYouTubeCredentials(refreshToken, clientId, clientSecret)
                    val result = JsonObject().apply {
                        addProperty("success", true)
                        addProperty("message", "Paired successfully with PC YouTube credentials")
                    }
                    return result.toString()
                }
            }
            JsonObject().apply {
                addProperty("success", false)
                addProperty("message", "PC response HTTP ${resp.code}: YouTube not configured on PC")
            }.toString()
        } catch (e: Exception) {
            JsonObject().apply {
                addProperty("success", false)
                addProperty("message", "Failed to connect to PC at $pcHost: ${e.message}")
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
    fun getStorageInfo(): String {
        return try {
            val stat = StatFs(activity.filesDir.absolutePath)
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
