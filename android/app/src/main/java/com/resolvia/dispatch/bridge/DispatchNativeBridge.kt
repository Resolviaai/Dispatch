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
    fun getPcHost(): String {
        val host = pairingManager.pcHost
        return if (host.isNotBlank()) host else ""
    }

    @JavascriptInterface
    fun setPcHost(host: String): Boolean {
        val cleanHost = host.trim().removePrefix("http://").removePrefix("https://").trimEnd('/')
        if (cleanHost.isNotBlank()) {
            pairingManager.pcHost = cleanHost
            android.util.Log.i("DispatchBridge", "Native PC host updated to $cleanHost")
            return true
        }
        return false
    }

    /** Opens an https URL from the trusted Dispatch Worker in a Custom Tab.
     *  Restricted to the known Dispatch cloud host; JavaScript cannot open
     *  arbitrary pages through this. */
    @JavascriptInterface
    fun openCustomTab(url: String): Boolean {
        if (!url.startsWith("https://dispatch-cloud.resolviaai.workers.dev/")) return false
        return try {
            val builder = androidx.browser.customtabs.CustomTabsIntent.Builder()
                .setShowTitle(true)
            val customTabsIntent = builder.build()

            // Prefer Chrome so Google Accounts on the device are available in the OAuth account picker
            val pm = activity.packageManager
            val chromePackages = listOf(
                "com.android.chrome",
                "com.chrome.beta",
                "com.chrome.dev",
                "com.chrome.canary",
                "com.google.android.apps.chrome"
            )
            val selectedPackage = chromePackages.firstOrNull { pkg ->
                try {
                    pm.getPackageInfo(pkg, 0)
                    true
                } catch (_: Exception) {
                    false
                }
            }
            if (selectedPackage != null) {
                customTabsIntent.intent.setPackage(selectedPackage)
            }
            customTabsIntent.launchUrl(activity, android.net.Uri.parse(url))
            true
        } catch (e: Exception) {
            try {
                val browserIntent = android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))
                activity.startActivity(browserIntent)
                true
            } catch (_: Exception) {
                false
            }
        }
    }

    @JavascriptInterface
    fun getAuthToken(): String = pairingManager.pcAuthToken

    @JavascriptInterface
    fun startRecording() {
        activity.runOnUiThread {
            activity.lifecycleScope.launch {
                try {
                    segmenterEngine.startSession(
                        cameraManager = cameraCaptureManager,
                        notes = "Dispatch Studio Android Capture"
                    )
                    (activity as? com.resolvia.dispatch.MainActivity)
                        ?.setRecordingScreenDimmingEnabled(segmenterEngine.isSessionActive || cameraCaptureManager.isRecording)
                } catch (e: Exception) {
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(false)
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
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(false)
                    segmenterEngine.triggerYouTubeUpload()
                    cameraCaptureManager.activePreviewView?.let { pv ->
                        cameraCaptureManager.initializeCamera(activity, pv)
                    }
                } catch (e: Exception) {
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(
                        segmenterEngine.isSessionActive || cameraCaptureManager.isRecording
                    )
                    android.util.Log.e("DispatchBridge", "stopRecording failed: ${e.message}", e)
                }
            }
        }
    }

    @JavascriptInterface
    fun isRecording(): Boolean {
        val recording = segmenterEngine.isSessionActive || cameraCaptureManager.isRecording
        (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(recording)
        return recording
    }

    @JavascriptInterface
    fun isRecordingPaused(): Boolean = cameraCaptureManager.isRecordingPaused

    @JavascriptInterface
    fun pauseRecording() {
        activity.runOnUiThread {
            segmenterEngine.pauseSession(cameraCaptureManager)
        }
    }

    @JavascriptInterface
    fun resumeRecording() {
        activity.runOnUiThread {
            segmenterEngine.resumeSession(cameraCaptureManager)
        }
    }

    @JavascriptInterface
    fun switchCamera() {
        notifyCameraStatus("initializing", null)
        activity.runOnUiThread {
            activity.lifecycleScope.launch {
                try {
                    cameraCaptureManager.toggleLensFacing(
                        onReady = { notifyCameraStatus("ready", null) },
                        onError = { error -> notifyCameraStatus("error", error) }
                    )
                } catch (e: Exception) {
                    android.util.Log.w("DispatchBridge", "switchCamera failed: ${e.message}")
                    notifyCameraStatus("error", e.message)
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
        } catch (e: Exception) {
            1.0f
        }
    }

    @JavascriptInterface
    fun getZoomInfo(): String {
        val info = cameraCaptureManager.getZoomInfo()
        val obj = JsonObject().apply {
            addProperty("minZoom", info.minZoomRatio)
            addProperty("maxZoom", info.maxZoomRatio)
            addProperty("currentZoom", info.currentZoomRatio)
        }
        return obj.toString()
    }

    @JavascriptInterface
    fun performHaptic(type: String = "light"): Boolean {
        activity.runOnUiThread {
            try {
                val view = activity.window.decorView
                when (type) {
                    "recording_start" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.CONFIRM)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                        }
                    }
                    "recording_stop" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.REJECT)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
                        }
                    }
                    "zoom_tick" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.SEGMENT_TICK)
                        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.CONTEXT_CLICK)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK)
                        }
                    }
                    "lens_switch", "toggle" -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                    }
                    "lock" -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
                    }
                    else -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                    }
                }
            } catch (_: Exception) {}
        }
        return true
    }

    fun sendCameraEvent(eventType: String, data: JsonObject) {
        activity.runOnUiThread {
            val payload = data.toString()
            val js = "(function() { if (window.onDispatchCameraEvent) { window.onDispatchCameraEvent('$eventType', $payload); } else { window.dispatchEvent(new CustomEvent('dispatch-camera-event', { detail: { type: '$eventType', data: $payload } })); } })();"
            webView.evaluateJavascript(js, null)
        }
    }

    fun notifyCameraStatus(status: String, error: String? = null) {
        val obj = JsonObject().apply {
            addProperty("status", status)
            if (error != null) addProperty("error", error)
            addProperty("lensFacing", if (cameraCaptureManager.currentLensFacing == androidx.camera.core.CameraSelector.LENS_FACING_FRONT) "front" else "back")
            addProperty("isRecording", cameraCaptureManager.isRecording)
            addProperty("isRecordingPaused", cameraCaptureManager.isRecordingPaused)
            addProperty("isTorchEnabled", cameraCaptureManager.isTorchEnabled)
            addProperty("isAeAfLocked", cameraCaptureManager.isAeAfLocked)
            addProperty("isAeLocked", cameraCaptureManager.isAeLocked)
            addProperty("isAfLocked", cameraCaptureManager.isAfLocked)
            addProperty("isAeAfLockUncertain", cameraCaptureManager.isAeAfLockUncertain)
            addProperty("isAudioEnabled", cameraCaptureManager.isAudioEnabled)

            val exp = cameraCaptureManager.getExposureInfo()
            add("exposure", JsonObject().apply {
                addProperty("minIndex", exp.minIndex)
                addProperty("maxIndex", exp.maxIndex)
                addProperty("currentIndex", exp.currentIndex)
                addProperty("stepEv", exp.stepEv)
                addProperty("isSupported", exp.isSupported)
            })

            val zoom = cameraCaptureManager.getZoomInfo()
            add("zoom", JsonObject().apply {
                addProperty("minZoom", zoom.minZoomRatio)
                addProperty("maxZoom", zoom.maxZoomRatio)
                addProperty("currentZoom", zoom.currentZoomRatio)
            })
        }
        sendCameraEvent("camera_state", obj)
    }

    @JavascriptInterface
    fun retryCameraInit() {
        activity.runOnUiThread {
            if (activity is com.resolvia.dispatch.MainActivity) {
                activity.initCameraLifecycle()
            }
        }
    }

    @JavascriptInterface
    fun getCameraStatus(): String {
        val exp = cameraCaptureManager.getExposureInfo()
        val zoom = cameraCaptureManager.getZoomInfo()
        val obj = JsonObject().apply {
            addProperty("status", if (cameraCaptureManager.isInitialized) "ready" else "initializing")
            addProperty("isInitialized", cameraCaptureManager.isInitialized)
            addProperty("lensFacing", if (cameraCaptureManager.currentLensFacing == androidx.camera.core.CameraSelector.LENS_FACING_FRONT) "front" else "back")
            addProperty("isRecording", cameraCaptureManager.isRecording)
            addProperty("isRecordingPaused", cameraCaptureManager.isRecordingPaused)
            addProperty("isTorchEnabled", cameraCaptureManager.isTorchEnabled)
            addProperty("isAeAfLocked", cameraCaptureManager.isAeAfLocked)
            addProperty("isAeLocked", cameraCaptureManager.isAeLocked)
            addProperty("isAfLocked", cameraCaptureManager.isAfLocked)
            addProperty("isAeAfLockUncertain", cameraCaptureManager.isAeAfLockUncertain)
            addProperty("isAudioEnabled", cameraCaptureManager.isAudioEnabled)
            add("exposure", JsonObject().apply {
                addProperty("minIndex", exp.minIndex)
                addProperty("maxIndex", exp.maxIndex)
                addProperty("currentIndex", exp.currentIndex)
                addProperty("stepEv", exp.stepEv)
                addProperty("isSupported", exp.isSupported)
            })
            add("zoom", JsonObject().apply {
                addProperty("minZoom", zoom.minZoomRatio)
                addProperty("maxZoom", zoom.maxZoomRatio)
                addProperty("currentZoom", zoom.currentZoomRatio)
            })
        }
        return obj.toString()
    }

    @JavascriptInterface
    fun focusOnPoint(normalizedX: Float, normalizedY: Float, triggerId: Long) {
        activity.runOnUiThread {
            val pv = cameraCaptureManager.activePreviewView ?: run {
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", false)
                    addProperty("isLocked", false)
                    addProperty("error", "Preview not available")
                })
                return@runOnUiThread
            }
            val w = pv.width.toFloat()
            val h = pv.height.toFloat()
            if (w <= 0 || h <= 0) {
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", false)
                    addProperty("isLocked", false)
                    addProperty("error", "Preview dimensions not ready")
                })
                return@runOnUiThread
            }

            val px = normalizedX.coerceIn(0f, 1f) * w
            val py = normalizedY.coerceIn(0f, 1f) * h
            cameraCaptureManager.focusOnPoint(px, py, pv) { res ->
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", res.success)
                    addProperty("isLocked", false)
                    addProperty("hasAf", res.hasAf)
                    addProperty("hasAe", res.hasAe)
                    addProperty("lockStateUncertain", res.lockStateUncertain)
                    if (res.error != null) addProperty("error", res.error)
                })
            }
        }
    }

    @JavascriptInterface
    fun focusOnPoint(normalizedX: Float, normalizedY: Float) {
        focusOnPoint(normalizedX, normalizedY, System.currentTimeMillis())
    }

    @JavascriptInterface
    fun lockAeAfAtPoint(normalizedX: Float, normalizedY: Float, triggerId: Long): Boolean {
        activity.runOnUiThread {
            val pv = cameraCaptureManager.activePreviewView ?: run {
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", false)
                    addProperty("isLocked", false)
                    addProperty("error", "Preview not available")
                })
                return@runOnUiThread
            }
            val w = pv.width.toFloat()
            val h = pv.height.toFloat()
            if (w <= 0 || h <= 0) {
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", false)
                    addProperty("isLocked", false)
                    addProperty("error", "Preview dimensions not ready")
                })
                return@runOnUiThread
            }

            val px = normalizedX.coerceIn(0f, 1f) * w
            val py = normalizedY.coerceIn(0f, 1f) * h
            cameraCaptureManager.lockAeAfAtPoint(px, py, pv) { res ->
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", res.success)
                    addProperty("isLocked", res.isLocked)
                    addProperty("hasAf", res.hasAf)
                    addProperty("hasAe", res.hasAe)
                    addProperty("lockStateUncertain", res.lockStateUncertain)
                    if (res.error != null) addProperty("error", res.error)
                })
            }
        }
        return true
    }

    @JavascriptInterface
    fun lockAeAfAtPoint(normalizedX: Float, normalizedY: Float): Boolean {
        return lockAeAfAtPoint(normalizedX, normalizedY, System.currentTimeMillis())
    }

    @JavascriptInterface
    fun unlockAeAf() {
        activity.runOnUiThread {
            cameraCaptureManager.unlockAeAf { ok ->
                sendCameraEvent("unlock_result", JsonObject().apply {
                    addProperty("success", ok)
                })
            }
        }
    }

    /**
     * Single-tap contract on a locked camera: unlock first, then move focus and
     * metering to the tapped point once the unlock has been confirmed natively.
     * Calling focusOnPoint directly would be rejected by the lock guard.
     */
    @JavascriptInterface
    fun unlockAndFocus(normalizedX: Float, normalizedY: Float, triggerId: Long) {
        activity.runOnUiThread {
            val pv = cameraCaptureManager.activePreviewView
            if (pv == null || pv.width <= 0 || pv.height <= 0) {
                sendCameraEvent("focus_result", JsonObject().apply {
                    addProperty("triggerId", triggerId)
                    addProperty("success", false)
                    addProperty("isLocked", false)
                    addProperty("error", "Preview not available")
                })
                return@runOnUiThread
            }
            val px = normalizedX.coerceIn(0f, 1f) * pv.width
            val py = normalizedY.coerceIn(0f, 1f) * pv.height
            cameraCaptureManager.unlockAeAf {
                cameraCaptureManager.focusOnPoint(px, py, pv) { res ->
                    sendCameraEvent("focus_result", JsonObject().apply {
                        addProperty("triggerId", triggerId)
                        addProperty("success", res.success)
                        addProperty("isLocked", false)
                        addProperty("hasAf", res.hasAf)
                        addProperty("hasAe", res.hasAe)
                        addProperty("lockStateUncertain", res.lockStateUncertain)
                        if (res.error != null) addProperty("error", res.error)
                    })
                }
            }
        }
    }

    @JavascriptInterface
    fun setExposureIndex(index: Int): Int {
        activity.runOnUiThread {
            cameraCaptureManager.setExposureIndex(index) { applied, err ->
                if (applied != null) {
                    val exp = cameraCaptureManager.getExposureInfo()
                    sendCameraEvent("exposure_result", JsonObject().apply {
                        addProperty("success", true)
                        addProperty("index", applied)
                        addProperty("ev", applied * exp.stepEv)
                    })
                } else {
                    sendCameraEvent("exposure_result", JsonObject().apply {
                        addProperty("success", false)
                        if (err != null) addProperty("error", err)
                    })
                }
            }
        }
        return index
    }

    @JavascriptInterface
    fun getExposureInfo(): String {
        val info = cameraCaptureManager.getExposureInfo()
        val obj = JsonObject().apply {
            addProperty("minIndex", info.minIndex)
            addProperty("maxIndex", info.maxIndex)
            addProperty("currentIndex", info.currentIndex)
            addProperty("stepEv", info.stepEv)
            addProperty("isSupported", info.isSupported)
        }
        return obj.toString()
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
                val authToken = response.get("auth_token")?.asString ?: ""
                if (response.get("configured")?.asBoolean == true &&
                    refreshToken.isNotBlank() && clientId.isNotBlank() && clientSecret.isNotBlank()
                ) {
                    pairingManager.saveYouTubeCredentials(refreshToken, clientId, clientSecret)
                    if (authToken.isNotBlank()) {
                        pairingManager.pcAuthToken = authToken
                    }
                    pairingManager.pcHost = cleanHost

                    // Verify account against YouTube API directly
                    val freshToken = refreshAccessToken()
                    val channelInfo = if (freshToken != null) verifyYouTubeChannelRemote(freshToken) else null
                    if (channelInfo != null) {
                        pairingManager.youtubeChannelTitle = channelInfo.first
                        pairingManager.youtubeChannelId = channelInfo.second
                        pairingManager.pcHost = cleanHost

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
            val recordingDirectory = java.io.File(activity.getExternalFilesDir(null) ?: activity.filesDir, "recordings")
            if (!recordingDirectory.isDirectory && !recordingDirectory.mkdirs()) {
                throw IllegalStateException("Recording directory is unavailable")
            }
            val storagePath = recordingDirectory.absolutePath
            val stat = StatFs(storagePath)
            val bytesAvailable = stat.availableBlocksLong * stat.blockSizeLong
            val bytesTotal = stat.blockCountLong * stat.blockSizeLong
            val bytesPerGb = 1024.0 * 1024.0 * 1024.0
            val obj = JsonObject().apply {
                addProperty("available", true)
                addProperty("freeGb", bytesAvailable / bytesPerGb)
                addProperty("totalGb", bytesTotal / bytesPerGb)
                addProperty("freeBytes", bytesAvailable)
                addProperty("totalBytes", bytesTotal)
            }
            obj.toString()
        } catch (_: Exception) {
            JsonObject().apply {
                addProperty("available", false)
                addProperty("error", "Storage information is unavailable")
            }.toString()
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
                pairingManager.pcHost = cleanHost
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
