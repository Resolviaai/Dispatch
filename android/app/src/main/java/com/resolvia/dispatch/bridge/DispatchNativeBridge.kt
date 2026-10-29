package com.resolvia.dispatch.bridge

import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import androidx.core.content.FileProvider
import android.widget.Toast
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.content.Context
import android.os.Vibrator
import android.os.VibratorManager
import android.os.VibrationEffect
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import androidx.room.withTransaction
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.data.SegmentEntity
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
    init {
        segmenterEngine.currentUserId = pairingManager.currentUserId
    }
    private val gson = Gson()
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    private companion object {
        /** Mirrors YouTubeDirectUploadWorker's staleUploadLeaseMs: an UPLOADING
         *  lease older than this belongs to a dead worker run and is requeueable. */
        private const val STALE_UPLOAD_LEASE_MS = 10L * 60_000L
    }

    @JavascriptInterface
    fun isNativeAndroid(): Boolean = true

    @JavascriptInterface
    fun getDeviceModel(): String = "${Build.MANUFACTURER} ${Build.MODEL}"

    @JavascriptInterface
    fun getAppVersionInfo(): String {
        return try {
            val pm = activity.packageManager
            val pInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getPackageInfo(activity.packageName, PackageManager.PackageInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                pm.getPackageInfo(activity.packageName, 0)
            }
            val vCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                pInfo.longVersionCode.toInt()
            } else {
                @Suppress("DEPRECATION")
                pInfo.versionCode
            }
            val obj = JsonObject().apply {
                addProperty("packageName", activity.packageName)
                addProperty("versionName", pInfo.versionName ?: "unknown")
                addProperty("versionCode", vCode)
            }
            obj.toString()
        } catch (e: Exception) {
            // Honest fallback: never report a stale baked-in version. This build
            // does not generate BuildConfig (no versionName/code symbols), so
            // unknown placeholders are returned and the update gate defers to
            // the release manifest's versionCode instead.
            JsonObject().apply {
                addProperty("packageName", activity.packageName)
                addProperty("versionName", "unknown")
                addProperty("versionCode", -1)
            }.toString()
        }
    }

    @JavascriptInterface
    fun openDownloadUrl(url: String): Boolean {
        val cleanUrl = url.trim()
        if (!cleanUrl.startsWith("https://github.com/Resolviaai/Dispatch/releases/") &&
            !cleanUrl.startsWith("https://resolviaai.github.io/Dispatch/")) {
            return false
        }
        return try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(cleanUrl)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            activity.startActivity(intent)
            true
        } catch (e: Exception) {
            android.util.Log.e("DispatchBridge", "openDownloadUrl failed: ${e.message}")
            false
        }
    }

    @JavascriptInterface
    fun setCurrentUserId(userId: String?): Boolean {
        val clean = userId?.trim()?.takeIf { it.isNotBlank() }
        pairingManager.currentUserId = clean
        segmenterEngine.currentUserId = clean
        android.util.Log.i("DispatchBridge", "Active native user_id set to: $clean")
        return true
    }

    @JavascriptInterface
    fun getCurrentUserId(): String? = pairingManager.currentUserId

    @JavascriptInterface
    fun clearLegacyRecordings(): Boolean {
        return try {
            runBlocking(Dispatchers.IO) {
                database.recordingDao().deleteLegacyUnownedSegments()
                database.recordingDao().deleteLegacyUnownedSessions()
                true
            }
        } catch (e: Exception) {
            android.util.Log.e("DispatchBridge", "Failed to clear legacy recordings: ${e.message}")
            false
        }
    }

    @JavascriptInterface
    fun getPcHost(): String {
        val host = pairingManager.pcHost
        return if (host.isNotBlank()) host else ""
    }

    /**
     * Splits a user-supplied PC address into resolved scheme and bare host.
     * An explicit "https://" prefix is preserved (never stripped or downgraded
     * to plaintext); bare and "http://" addresses resolve to the legacy LAN
     * plaintext transport, with an explicit http:// input flagged in the log.
     */
    private fun splitPcHostAddress(input: String): Pair<String, String> {
        val trimmed = input.trim().trimEnd('/')
        return when {
            trimmed.startsWith("https://", ignoreCase = true) ->
                "https" to trimmed.substring("https://".length)
            trimmed.startsWith("http://", ignoreCase = true) -> {
                android.util.Log.w(
                    "DispatchBridge",
                    "PC address given as plaintext http:// (downgrade); prefer an https base URL"
                )
                "http" to trimmed.substring("http://".length)
            }
            else -> "http" to trimmed
        }
    }

    @JavascriptInterface
    fun setPcHost(host: String): Boolean {
        val (scheme, bareHost) = splitPcHostAddress(host)
        if (bareHost.isNotBlank()) {
            // An explicit https:// base URL is stored verbatim (never stripped or
            // downgraded); bare hosts keep the legacy bare-address storage shape.
            val stored = if (scheme == "https") "https://$bareHost" else bareHost
            pairingManager.pcHost = stored
            android.util.Log.i("DispatchBridge", "Native PC host updated to $stored")
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
            if (segmenterEngine.isSessionActive || cameraCaptureManager.isRecording) {
                sendCameraEvent("recording_start_failed", JsonObject().apply {
                    addProperty("code", "already_recording")
                    addProperty("message", "A recording session is already active")
                })
                return@runOnUiThread
            }
            sendCameraEvent("recording_starting", JsonObject())
            activity.lifecycleScope.launch {
                try {
                    val sessionId = segmenterEngine.startSession(
                        cameraManager = cameraCaptureManager,
                        notes = "Dispatch Studio Android Capture"
                    )
                    (activity as? com.resolvia.dispatch.MainActivity)
                        ?.setRecordingScreenDimmingEnabled(cameraCaptureManager.isRecording)
                    sendCameraEvent("recording_started", JsonObject().apply {
                        addProperty("sessionId", sessionId)
                    })
                } catch (e: Exception) {
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(false)
                    android.util.Log.e("DispatchBridge", "startRecording failed: ${e.message}", e)
                    val code = when (e) {
                        is com.resolvia.dispatch.recorder.RecordingStorageException -> "storage_unavailable"
                        is SecurityException -> "permission_denied"
                        else -> "capture_start_failed"
                    }
                    sendCameraEvent("recording_start_failed", JsonObject().apply {
                        addProperty("code", code)
                        addProperty("message", e.message ?: "Recording could not start")
                    })
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
                    sendCameraEvent("recording_stopping", JsonObject())
                    segmenterEngine.stopSession(cameraManager = cameraCaptureManager)
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(false)
                    segmenterEngine.triggerYouTubeUpload()

                    sendCameraEvent("recording_stopped", JsonObject())

                } catch (e: Exception) {
                    (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(
                        segmenterEngine.isSessionActive || cameraCaptureManager.isRecording
                    )
                    sendCameraEvent("recording_stop_failed", JsonObject().apply {
                        addProperty("error", e.message ?: "Recording could not be finalized")
                    })
                    android.util.Log.e("DispatchBridge", "stopRecording failed: ${e.message}", e)
                }
            }
        }
    }

    @JavascriptInterface
    fun isRecording(): Boolean {
        val recording = cameraCaptureManager.isRecording
        (activity as? com.resolvia.dispatch.MainActivity)?.setRecordingScreenDimmingEnabled(recording)
        return recording
    }

    @JavascriptInterface
    fun getRecordingState(): String {
        if (cameraCaptureManager.isRecording) {
            return if (cameraCaptureManager.isRecordingPaused) "paused" else "recording"
        }
        if (segmenterEngine.isSessionActive) return "starting"
        return "idle"
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
        if (segmenterEngine.isSessionActive || cameraCaptureManager.isRecording) {
            android.util.Log.w("DispatchBridge", "Cannot switch camera while recording is active")
            notifyCameraStatus("error", "Cannot switch camera while recording is active")
            return
        }
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
                view.isHapticFeedbackEnabled = true
                val flag = android.view.HapticFeedbackConstants.FLAG_IGNORE_VIEW_SETTING or
                        android.view.HapticFeedbackConstants.FLAG_IGNORE_GLOBAL_SETTING

                val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    val vm = activity.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
                    vm?.defaultVibrator
                } else {
                    @Suppress("DEPRECATION")
                    activity.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
                }

                when (type) {
                    "recording_start" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.CONFIRM, flag)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP, flag)
                        }
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createOneShot(35, VibrationEffect.DEFAULT_AMPLITUDE))
                        } else {
                            @Suppress("DEPRECATION")
                            vibrator?.vibrate(35)
                        }
                    }
                    "recording_stop" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.REJECT, flag)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS, flag)
                        }
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 20, 40, 25), -1))
                        } else {
                            @Suppress("DEPRECATION")
                            vibrator?.vibrate(longArrayOf(0, 20, 40, 25), -1)
                        }
                    }
                    "zoom_tick" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.SEGMENT_TICK, flag)
                        } else {
                            view.performHapticFeedback(android.view.HapticFeedbackConstants.CLOCK_TICK, flag)
                        }
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            vibrator?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
                        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createOneShot(8, 80))
                        }
                    }
                    "lens_switch", "toggle" -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP, flag)
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            vibrator?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_CLICK))
                        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createOneShot(15, VibrationEffect.DEFAULT_AMPLITUDE))
                        }
                    }
                    "lock" -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS, flag)
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            vibrator?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_HEAVY_CLICK))
                        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
                        }
                    }
                    else -> {
                        view.performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP, flag)
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            vibrator?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
                        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                            vibrator?.vibrate(VibrationEffect.createOneShot(10, VibrationEffect.DEFAULT_AMPLITUDE))
                        }
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
                val currentUid = pairingManager.currentUserId
                val segments = if (!currentUid.isNullOrBlank()) {
                    database.recordingDao().getSegmentsForUser(currentUid)
                } else {
                    database.recordingDao().getAllSegmentsList()
                }
com.resolvia.dispatch.media.RecordingHttpServer.ensureStarted(activity)
                val jsonArr = JsonArray()
                for (s in segments) {
                    val localFile = java.io.File(s.filepath)
                    val fileExists = localFile.exists() && localFile.length() > 0L
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
                        addProperty("userId", s.userId)
                        addProperty("fileExists", fileExists)
                        addProperty("videoUrl", if (fileExists) com.resolvia.dispatch.media.RecordingHttpServer.mediaUrl(s.filename).orEmpty() else "")
                        // Generated on demand so segments recorded before posters
                        // existed still get a thumbnail instead of a permanent gray tile.
                        val poster = com.resolvia.dispatch.recorder.RecordingPosterWriter.ensurePoster(localFile)
                        addProperty(
                            "thumbnailUrl",
                            poster?.let { com.resolvia.dispatch.media.RecordingHttpServer.mediaUrl(it.name) }.orEmpty()
                        )
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
    fun deleteSession(sessionId: String): Boolean {
        return try {
            runBlocking(Dispatchers.IO) {
                // Never delete while capture/finalization for THIS session is active.
                if ((segmenterEngine.isSessionActive && segmenterEngine.currentSessionId == sessionId) ||
                    (cameraCaptureManager.isRecording && segmenterEngine.currentSessionId == sessionId) ||
                    (com.resolvia.dispatch.recorder.RecordingForegroundService.activeSessionId == sessionId)
                ) {
                    android.util.Log.w("DispatchBridge", "Cannot delete session $sessionId while active recording or finalization is in progress")
                    return@runBlocking false
                }

                // Query ALL segments for this session without the 50-item limit
                val segments = database.recordingDao().getSegmentsBySessionId(sessionId)
                val staleLeaseBeforeMs = System.currentTimeMillis() - STALE_UPLOAD_LEASE_MS

                // Freeze queued/failed uploads first so the worker cannot claim them
                // after we start deleting files. A live UPLOADING lease still blocks
                // deletion; the transaction rolls back if any such segment exists.
                if (segments.isNotEmpty()) {
                    try {
                        database.withTransaction {
                            val marked = database.recordingDao()
                                .markSessionSegmentsDeleting(sessionId, staleLeaseBeforeMs)
                            if (marked != segments.size) {
                                throw IllegalStateException("an upload is still active")
                            }
                        }
                    } catch (e: Exception) {
                        android.util.Log.w("DispatchBridge", "Refused to delete session $sessionId: ${e.message}")
                        return@runBlocking false
                    }
                }

                var allFilesDeleted = true
                for (s in segments) {
                    try {
                        val f = java.io.File(s.filepath)
                        if (f.exists()) {
                            val deleted = f.delete()
                            if (!deleted) {
                                android.util.Log.e("DispatchBridge", "Failed to delete file on disk: ${s.filepath}")
                                allFilesDeleted = false
                            }
                        }
                        val tmp = java.io.File(s.filepath.replace(".mp4", ".tmp"))
                        if (tmp.exists()) {
                            tmp.delete()
                        }
                    } catch (e: Exception) {
                        android.util.Log.e("DispatchBridge", "Error deleting file ${s.filepath}: ${e.message}")
                        allFilesDeleted = false
                    }
                }

                if (!allFilesDeleted) {
                    // Put the original upload states back rather than leaving rows frozen.
                    database.withTransaction {
                        for (s in segments) {
                            database.recordingDao().updateSegmentStatus(s.segmentId, s.status)
                        }
                    }
                    android.util.Log.w("DispatchBridge", "Session $sessionId deletion incomplete: some files could not be removed")
                    return@runBlocking false
                }

                database.withTransaction {
                    for (s in segments) {
                        database.recordingDao().deleteSegment(s.segmentId)
                        database.recordingDao().deleteOutboxItem(s.segmentId)
                    }
                    database.recordingDao().deleteSegmentsBySessionId(sessionId)
                    database.recordingDao().deleteSession(sessionId)
                }
                true
            }
        } catch (e: Exception) {
            android.util.Log.e("DispatchBridge", "deleteSession failed: ${e.message}")
            false
        }
    }

@JavascriptInterface
    fun openNativePreview(idOrSessionId: String): Boolean {
        return try {
            val file = runBlocking(Dispatchers.IO) {
                // 1. Try finding segment by segmentId
                var seg = database.recordingDao().getSegmentById(idOrSessionId)
                if (seg == null) {
                    // 2. Try finding first segment by sessionId
                    val segs = database.recordingDao().getSegmentsBySessionId(idOrSessionId)
                    seg = segs.firstOrNull()
                }
                if (seg != null) {
                    val f = java.io.File(seg.filepath)
                    if (f.exists() && f.length() > 0L) return@runBlocking f
                }
                // 3. Fallback: check external and internal recordings directories directly
                val extRec = java.io.File(activity.getExternalFilesDir(null) ?: activity.filesDir, "recordings")
                val intRec = java.io.File(activity.filesDir, "recordings")
                val candidates = listOf(
                    java.io.File(extRec, "$idOrSessionId.mp4"),
                    java.io.File(extRec, idOrSessionId),
                    java.io.File(intRec, "$idOrSessionId.mp4"),
                    java.io.File(intRec, idOrSessionId)
                )
                candidates.firstOrNull { it.exists() && it.length() > 0L }
            } ?: run {
                activity.runOnUiThread {
                    Toast.makeText(activity, "Video file not found on device storage", Toast.LENGTH_SHORT).show()
                }
                return false
            }

            activity.runOnUiThread {
                try {
                    val uri = FileProvider.getUriForFile(
                        activity,
                        "${activity.packageName}.fileprovider",
                        file
                    )
                    val intent = Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(uri, "video/mp4")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    activity.startActivity(intent)
                } catch (e: Exception) {
                    android.util.Log.e("DispatchBridge", "Could not start native preview intent: ${e.message}")
                    Toast.makeText(activity, "Could not open video player: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
            true
        } catch (e: Exception) {
            android.util.Log.e("DispatchBridge", "openNativePreview failed: ${e.message}")
            false
        }
    }

    @JavascriptInterface
    fun getYouTubeStatus(): String {
        val directConfigured = pairingManager.isYouTubeConfigured
        val cloudSessionActive = pairingManager.cloudAccessToken.isNotBlank() &&
            pairingManager.rawVaultAccountId.isNotBlank()
        val connected = directConfigured || cloudSessionActive
        if (!connected) {
            return JsonObject().apply {
                addProperty("connected", false)
                addProperty("verified", false)
                addProperty("route", "none")
            }.toString()
        }

        // Report the route brand-new uploads actually take (UploadModePolicy
        // prefers the cloud vault session when it is active).
        val route = if (cloudSessionActive) "cloud" else "direct"

        // Cached channel identity comes from the legacy pairing's own
        // verification pass; the cloud route never verifies a channel locally.
        val cachedTitle = pairingManager.youtubeChannelTitle
        val cachedId = pairingManager.youtubeChannelId
        val isVerified = directConfigured && cachedTitle.isNotBlank() && cachedId.isNotBlank()

        val obj = JsonObject().apply {
            addProperty("connected", true)
            addProperty("verified", isVerified)
            addProperty("route", route)
            if (isVerified) {
                addProperty("channelTitle", cachedTitle)
                addProperty("channelId", cachedId)
                addProperty("account", cachedTitle)
            } else {
                addProperty("channelTitle", "Unverified Channel")
                addProperty(
                    "message",
                    if (route == "cloud") "Cloud vault route active; channel identity is not verified on this device."
                    else "Credentials present but channel unverified. Reconnect to refresh."
                )
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
            // Fail-closed: refuse to pair when credentials cannot be stored
            // confidentially, rather than pairing on ambiguous device state.
            if (!pairingManager.canStoreCredentials) {
                return JsonObject().apply {
                    addProperty("success", false)
                    addProperty("connected", false)
                    addProperty("error", "secure storage unavailable")
                    addProperty("message", "Secure storage is unavailable on this device. YouTube pairing is disabled until the device keystore works again.")
                }.toString()
            }
            if (pairingCode.isBlank()) {
                return JsonObject().apply {
                    addProperty("success", false)
                    addProperty("message", "Enter the one-time pairing code shown in PC Settings.")
                }.toString()
            }
            val (scheme, bareHost) = splitPcHostAddress(pcHost)
            // An explicit https base URL is preserved end-to-end; bare / http
            // input keeps the legacy plaintext LAN transport (warned in split).
            val storedHost = if (scheme == "https") "$scheme://$bareHost" else bareHost
            val url = "$scheme://$bareHost/api/youtube/credentials"
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
                    pairingManager.pcHost = storedHost

                    // Verify account against YouTube API directly
                    val freshToken = refreshAccessToken()
                    val channelInfo = if (freshToken != null) verifyYouTubeChannelRemote(freshToken) else null
                    if (channelInfo != null) {
                        pairingManager.youtubeChannelTitle = channelInfo.first
                        pairingManager.youtubeChannelId = channelInfo.second
                        pairingManager.pcHost = storedHost

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

    /**

     * Pushes the Supabase session JWT and the raw-vault YouTube account id from

     * the WebView into secure native storage so the direct YouTube upload

     * worker can request scoped resumable upload sessions from the Dispatch

     * cloud without the PC being online and without locally stored OAuth.

     *

     * Payload: {"access_token": "<jwt>", "account_id": "<youtube-vault-id>"}

     * An empty payload clears the cloud session (sign-out).

     */

    @JavascriptInterface

    fun pushCloudSession(payload: String?): Boolean {

        val raw = payload?.trim() ?: ""

        if (raw.isEmpty() || raw == "{}") {

            pairingManager.clearCloudAccessToken()

            pairingManager.rawVaultAccountId = ""

            return true

        }

        return try {

            val obj = gson.fromJson(raw, JsonObject::class.java)

            val token = obj.get("access_token")?.asString?.trim() ?: ""

            val accountId = obj.get("account_id")?.asString?.trim() ?: ""

            // Reject malformed inputs early: a corrupted token must not poison the

            // upload queue, and the vault id format must match the Worker contract.

            val jwtLike = token.count { it == '.' } == 2 && token.length > 40

            val uuidLike = Regex("^[0-9a-f-]{36}$", RegexOption.IGNORE_CASE).matches(accountId)

            if (!jwtLike || !uuidLike) {

                android.util.Log.w("DispatchBridge", "Ignoring malformed cloud session push")

                return false

            }

            // Fail closed: a push that cannot be persisted confidentially must
            // not report success and leave the worker on ambiguous state.
            if (!pairingManager.canStoreCloudSession) {

                android.util.Log.e("DispatchBridge", "Cloud session push rejected: secure cloud storage unavailable")

                return false

            }

            pairingManager.cloudAccessToken = token

            pairingManager.rawVaultAccountId = accountId

            android.util.Log.i("DispatchBridge", "Cloud upload session updated for vault account ${accountId.take(8)}...")

            activity.lifecycleScope.launch(Dispatchers.IO) {

                try {

                    val waiting = database.recordingDao().getPendingUploadSegments()

                    var requeued = 0

                    for (seg in waiting) {

                        if (seg.status == "WAITING_FOR_YOUTUBE") {

                            database.recordingDao().updateSegmentStatus(seg.segmentId, "QUEUED_FOR_UPLOAD")

                            requeued++

                        }

                    }

                    if (requeued > 0) {

                        android.util.Log.i("DispatchBridge", "Fresh cloud token requeued $requeued waiting segments")

                    }

                    segmenterEngine.triggerYouTubeUpload()

                } catch (e: Exception) {

                    android.util.Log.e("DispatchBridge", "Cloud session push requeue failed: ${e.message}")

                }

            }

            true

        } catch (e: Exception) {

            android.util.Log.w("DispatchBridge", "pushCloudSession parse failed: ${e.message}")

            false

        }

    }

    
@JavascriptInterface
    fun retryUpload(idOrSessionId: String) {
        activity.lifecycleScope.launch(Dispatchers.IO) {
            try {
                // Accepts either a segment id or a session id: the Library passes
                // session ids for phone recordings, so requeue every retryable
                // segment of the session when the direct segment lookup misses.
                val requeueable = setOf(
                    "UPLOADING",
                    "FAILED_RETRY",
                    "FAILED_YOUTUBE_PROCESSING",
                    "WAITING_FOR_YOUTUBE",
                    "QUEUED_FOR_UPLOAD"
                )
                val staleLeaseBeforeMs = System.currentTimeMillis() - STALE_UPLOAD_LEASE_MS
                val direct = database.recordingDao().getSegmentById(idOrSessionId)
                if (direct != null) {
                    if (direct.status in requeueable) {
                        requeueSegmentForRetry(direct, staleLeaseBeforeMs)
                    }
                } else {
                    val segments = database.recordingDao().getSegmentsBySessionId(idOrSessionId)
                    for (seg in segments) {
                        if (seg.status in requeueable) {
                            requeueSegmentForRetry(seg, staleLeaseBeforeMs)
                        }
                    }
                }
                segmenterEngine.triggerYouTubeUpload()
            } catch (e: Exception) {
                android.util.Log.e("DispatchBridge", "retryUpload failed: ${e.message}")
            }
        }
    }

    /**
     * Requeues one segment for a fresh upload attempt: status →
     * QUEUED_FOR_UPLOAD, stale youtubeVideoId cleared (a retry must never
     * launder a finished/partial upload into a second video row) and any
     * saved resumable session URL dropped so the next run starts a clean
     * session. Rows still claimed by a live worker run (fresh UPLOADING
     * lease) are skipped so this cannot race that run into a duplicate
     * upload of the same recording.
     */
    private suspend fun requeueSegmentForRetry(seg: SegmentEntity, staleLeaseBeforeMs: Long) {
        if (seg.status == "UPLOADING" &&
            seg.uploadLeaseAt != null && seg.uploadLeaseAt >= staleLeaseBeforeMs
        ) {
            android.util.Log.w(
                "DispatchBridge",
                "Segment ${seg.segmentId} is actively uploading (fresh claim); retry skipped"
            )
            return
        }
        database.recordingDao().deleteOutboxItem(seg.segmentId)
        database.recordingDao().updateSegmentStatusAndVideoId(seg.segmentId, "QUEUED_FOR_UPLOAD", null)
        pairingManager.clearUploadSessionUrl(seg.segmentId)
        database.recordingDao().updateOutboxOffset(seg.segmentId, 0L)
        database.recordingDao().insertOutbox(com.resolvia.dispatch.data.OutboxEntity(
            segmentId = seg.segmentId,
            sessionId = seg.sessionId,
            status = "QUEUED_FOR_UPLOAD",
            updatedAt = System.currentTimeMillis()
        ))
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
            val (scheme, bareHost) = splitPcHostAddress(pcHost)
            val url = "$scheme://$bareHost/api/status"
            val req = Request.Builder().url(url).build()
            httpClient.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) {
                    // Remember the resolved address; an explicit https base URL is
                    // preserved for the worker's PC-ack calls (same contract).
                    pairingManager.pcHost = if (scheme == "https") "$scheme://$bareHost" else bareHost
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
            }
        } catch (e: Exception) {
            JsonObject().apply {
                addProperty("success", false)
                addProperty("message", "Unreachable: ${e.message}")
            }.toString()
        }
    }
}
