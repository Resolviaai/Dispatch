package com.resolvia.dispatch

import android.Manifest
import java.io.File
import java.io.FileInputStream
import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import org.json.JSONObject
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import com.resolvia.dispatch.bridge.DispatchNativeBridge
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.RecordingScreenDimmingState
import com.resolvia.dispatch.recorder.RecordingTouchAction
import com.resolvia.dispatch.recorder.SegmenterEngine

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var cameraCaptureManager: CameraCaptureManager
    private lateinit var database: AppDatabase
    private lateinit var pairingManager: PairingManager
    private lateinit var webView: WebView
    private lateinit var previewView: PreviewView
    private lateinit var bridge: DispatchNativeBridge

    private val screenDimmingState = RecordingScreenDimmingState()
    private val screenDimmingHandler = Handler(Looper.getMainLooper())
    private var originalScreenBrightness: Float? = null
    private val screenDimmingRunnable = Runnable {
        if (!isFinishing && screenDimmingState.onInactivityTimeout()) {
            applyRecordingScreenDimmed(true)
        }
    }

    companion object {
        private const val RECORDING_IDLE_DIM_DELAY_MS = 150_000L
        private const val RECORDING_DIM_BRIGHTNESS = 0.01f
    }

    private val permissionsToRequest = buildList {
        add(Manifest.permission.CAMERA)
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }.toTypedArray()

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { perms ->
        val cam = perms[Manifest.permission.CAMERA] == true
        val mic = perms[Manifest.permission.RECORD_AUDIO] == true
        if (!cam) {
            Toast.makeText(this, "Camera permission is required to preview and record.", Toast.LENGTH_LONG).show()
            if (::bridge.isInitialized) {
                bridge.notifyCameraStatus("permission_required", "Camera permission required")
            }
        } else {
            if (!mic) {
                Toast.makeText(this, "Microphone permission denied. Capturing video without audio.", Toast.LENGTH_LONG).show()
                cameraCaptureManager.setAudioEnabled(false)
            } else {
                cameraCaptureManager.setAudioEnabled(true)
            }
            initCameraLifecycle()
        }
    }

    fun initCameraLifecycle() {
        if (::bridge.isInitialized) {
            bridge.notifyCameraStatus("initializing", null)
        }
        previewView.post {
            try {
                cameraCaptureManager.initializeCamera(
                    lifecycleOwner = this@MainActivity,
                    previewView = previewView,
                    onReady = {
                        android.util.Log.i("MainActivity", "CameraX 1080p VideoCapture + Preview bound successfully.")
                        if (::bridge.isInitialized) {
                            bridge.notifyCameraStatus("ready", null)
                        }
                    },
                    onError = { err ->
                        android.util.Log.e("MainActivity", "Error binding camera lifecycle: $err")
                        if (::bridge.isInitialized) {
                            bridge.notifyCameraStatus("error", err)
                        }
                        Toast.makeText(this@MainActivity, "Camera error: $err", Toast.LENGTH_LONG).show()
                    }
                )
            } catch (e: Exception) {
                android.util.Log.e("MainActivity", "Error binding camera lifecycle: ${e.message}", e)
                if (::bridge.isInitialized) {
                    bridge.notifyCameraStatus("error", e.message)
                }
                Toast.makeText(this@MainActivity, "Camera error: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    fun setRecordingScreenDimmingEnabled(enabled: Boolean) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            updateRecordingScreenDimming(enabled)
        } else {
            runOnUiThread { updateRecordingScreenDimming(enabled) }
        }
    }

    private fun updateRecordingScreenDimming(enabled: Boolean) {
        val changed = screenDimmingState.recordingEnabled != enabled
        screenDimmingState.setRecordingEnabled(enabled)
        if (!enabled) {
            screenDimmingHandler.removeCallbacks(screenDimmingRunnable)
            applyRecordingScreenDimmed(false)
        } else if (changed) {
            scheduleRecordingScreenDim()
        }
    }

    private fun scheduleRecordingScreenDim() {
        screenDimmingHandler.removeCallbacks(screenDimmingRunnable)
        if (screenDimmingState.shouldRunInactivityTimer) {
            screenDimmingHandler.postDelayed(screenDimmingRunnable, RECORDING_IDLE_DIM_DELAY_MS)
        }
    }

    private fun applyRecordingScreenDimmed(dimmed: Boolean) {
        if (dimmed) {
            if (originalScreenBrightness == null) {
                originalScreenBrightness = window.attributes.screenBrightness
            }
            val attributes = window.attributes
            if (attributes.screenBrightness != RECORDING_DIM_BRIGHTNESS) {
                attributes.screenBrightness = RECORDING_DIM_BRIGHTNESS
                window.attributes = attributes
            }
            return
        }

        val originalBrightness = originalScreenBrightness ?: return
        val attributes = window.attributes
        attributes.screenBrightness = originalBrightness
        window.attributes = attributes
        originalScreenBrightness = null
    }

    private fun toRecordingTouchAction(action: Int): RecordingTouchAction = when (action) {
        MotionEvent.ACTION_DOWN -> RecordingTouchAction.DOWN
        MotionEvent.ACTION_POINTER_DOWN -> RecordingTouchAction.POINTER_DOWN
        MotionEvent.ACTION_MOVE -> RecordingTouchAction.MOVE
        MotionEvent.ACTION_UP -> RecordingTouchAction.UP
        MotionEvent.ACTION_CANCEL -> RecordingTouchAction.CANCEL
        else -> RecordingTouchAction.OTHER
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        val outcome = screenDimmingState.onTouch(toRecordingTouchAction(event.actionMasked))
        if (outcome.wokeScreen) {
            applyRecordingScreenDimmed(false)
            scheduleRecordingScreenDim()
        }
        if (outcome.userActivity) {
            scheduleRecordingScreenDim()
        }
        if (outcome.consume) return true
        return super.dispatchTouchEvent(event)
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Keep screen on during creator studio workflow
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        database = AppDatabase.getDatabase(this)
        segmenterEngine = SegmenterEngine(this, database)
        cameraCaptureManager = CameraCaptureManager(this)
        pairingManager = PairingManager(this)

        // Serve recorded media over loopback HTTP (real range semantics) for this
        // activity's lifetime; the bridge builds media URLs from it.
        com.resolvia.dispatch.media.RecordingHttpServer.ensureStarted(this)

        // Initialize native camera PreviewView (placed underneath WebView)
        // PERFORMANCE mode uses hardware SurfaceView directly via hardware composer, eliminating TextureView copy lag during 1080p recording
        previewView = PreviewView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            implementationMode = PreviewView.ImplementationMode.PERFORMANCE
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }

        // Set up WebViewAssetLoader to safely serve assets over http://appassets.androidplatform.net
        // Using HTTP scheme eliminates Chromium mixed-content auto-upgrade so local LAN HTTP video streaming works seamlessly.
        // Recorded media is served by RecordingHttpServer over real HTTP on loopback.
        // The WebView only loads the app shell from its asset domain, so there is a
        // single media transport rather than an interception path that behaves
        // differently across devices and file sizes.
        val assetLoader = WebViewAssetLoader.Builder()
            .setDomain("appassets.androidplatform.net")
            .setHttpAllowed(true)
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        // Configure full-bleed WebView with transparent background
        webView = WebView(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            setLayerType(View.LAYER_TYPE_NONE, null)
            scrollBarStyle = View.SCROLLBARS_INSIDE_OVERLAY
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false

            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                allowFileAccessFromFileURLs = false
                allowUniversalAccessFromFileURLs = false
                // Fail-closed mixed content: an https document must never pull
                // script or media over plaintext http. The in-app shell origin
                // is http://appassets.androidplatform.net (no https page, so no
                // mixed content is possible and LAN http media still plays);
                // externally-served https pages are the protected surface here.
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                mediaPlaybackRequiresUserGesture = false
                loadWithOverviewMode = true
                useWideViewPort = true
                cacheMode = WebSettings.LOAD_DEFAULT
            }

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    val url = request.url
                    val scheme = url.scheme?.lowercase() ?: ""
                    val host = url.host?.lowercase() ?: ""
                    // Only web schemes can be allowlisted or externalized;
                    // blob:/about:/content: keep the WebView's default handling.
                    if (scheme != "http" && scheme != "https") return false
                    val allowlisted = host == "appassets.androidplatform.net"
                    // The JS bridge is attached to this WebView process-wide. A remote
                    // origin (including the cloud worker) must never inherit it.
                    if (!request.isForMainFrame && !allowlisted) {
                        android.util.Log.w("DispatchWebView", "Blocked remote subframe $url")
                        return true
                    }
                    if (allowlisted) return false
                    android.util.Log.w(
                        "DispatchWebView",
                        "Blocked in-app navigation to $url; opening externally"
                    )
                    val external = Intent(Intent.ACTION_VIEW, url).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    return try {
                        startActivity(external)
                        true
                    } catch (e: Exception) {
                        // Fail closed: without an external handler the foreign
                        // URL must not silently load inside the app WebView.
                        android.util.Log.e(
                            "DispatchWebView",
                            "External intent failed for $url (${e.message}); navigation suppressed"
                        )
                        true
                    }
                }

                override fun shouldInterceptRequest(
                    view: WebView,
                    request: WebResourceRequest
                ): WebResourceResponse? {
                    // Only the app shell is intercepted. Recording media is served by
                    // RecordingHttpServer over loopback HTTP so the media stack gets
                    // real 206/416 range semantics.
                    return assetLoader.shouldInterceptRequest(request.url)
                }

                override fun onReceivedError(
                    view: WebView,
                    request: WebResourceRequest,
                    error: WebResourceError
                ) {
                    super.onReceivedError(view, request, error)
                    android.util.Log.e("DispatchWebView", "Resource error on ${request.url}: ${error.description}")
                }
            }

            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    // 1) Origin gate: the local app shell owns the camera and
                    // microphone; any foreign origin gets no device grants.
                    val originHost = request.origin?.host
                    val originAllowed = originHost == "appassets.androidplatform.net"
                    // 2) Resource gate: only camera / microphone capture may be
                    // granted; geolocation or any other sensitive resource is
                    // refused outright.
                    val wanted = request.resources.filter { resource ->
                        resource == PermissionRequest.RESOURCE_VIDEO_CAPTURE ||
                            resource == PermissionRequest.RESOURCE_AUDIO_CAPTURE
                    }
                    if (!originAllowed || wanted.isEmpty()) {
                        request.deny()
                        return
                    }
                    // 3) Permission gate: the WebView surface can only hand out
                    // what the host app itself holds at the OS level.
                    val camGranted = ContextCompat.checkSelfPermission(
                        this@MainActivity, Manifest.permission.CAMERA
                    ) == PackageManager.PERMISSION_GRANTED
                    val micGranted = ContextCompat.checkSelfPermission(
                        this@MainActivity, Manifest.permission.RECORD_AUDIO
                    ) == PackageManager.PERMISSION_GRANTED
                    val grantable = mutableListOf<String>()
                    for (resource in wanted) {
                        if (resource == PermissionRequest.RESOURCE_VIDEO_CAPTURE && camGranted) {
                            grantable.add(resource)
                        }
                        if (resource == PermissionRequest.RESOURCE_AUDIO_CAPTURE && micGranted) {
                            grantable.add(resource)
                        }
                    }
                    if (grantable.isEmpty()) {
                        request.deny()
                    } else {
                        request.grant(grantable.toTypedArray())
                    }
                }

                override fun onConsoleMessage(consoleMessage: ConsoleMessage): Boolean {
                    android.util.Log.d(
                        "DispatchWebConsole",
                        "[${consoleMessage.messageLevel()}] ${consoleMessage.message()} (at ${consoleMessage.sourceId()}:${consoleMessage.lineNumber()})"
                    )
                    return true
                }
            }
        }

        // Attach native bridge
        bridge = DispatchNativeBridge(
            activity = this,
            webView = webView,
            segmenterEngine = segmenterEngine,
            cameraCaptureManager = cameraCaptureManager,
            database = database,
            pairingManager = pairingManager
        )
        webView.addJavascriptInterface(bridge, "DispatchBridge")

        // Handle hardware and gesture back navigation cleanly
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        // Load canonical React application packaged in assets
        webView.loadUrl("http://appassets.androidplatform.net/assets/web/index.html")

        // Root container: PreviewView underneath, WebView on top
        val rootLayout = FrameLayout(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            setBackgroundColor(Color.parseColor("#161616"))
            addView(previewView)
            addView(webView)
        }

        setContentView(rootLayout)

        // Check required permissions once views are attached to window
        checkAndRequestPermissions()

        // Handle incoming deep link intent (e.g. cold start via OAuth callback)
        handleIncomingIntent(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIncomingIntent(intent)
    }

    private fun handleIncomingIntent(intent: Intent?) {
        val uri: Uri = intent?.data ?: return
        android.util.Log.i("MainActivity", "Handling deep link intent URI: $uri")
        val scheme = uri.scheme?.lowercase() ?: ""
        val host = uri.host?.lowercase() ?: ""

        val isDispatchScheme = scheme == "dispatch" && host == "oauth"
        val isHttpsScheme = scheme == "https" &&
            (host == "resolviaai.github.io" || host == "dispatch-cloud.resolviaai.workers.dev") &&
            (uri.path?.contains("/oauth") == true)

        if (isDispatchScheme || isHttpsScheme) {
            handleOAuthCallback(uri)
        }
    }

    private fun handleOAuthCallback(uri: Uri) {
        try {
            val path = uri.path ?: ""
            val provider = uri.getQueryParameter("provider")
                ?: uri.getQueryParameter("service")
                ?: when {
                    path.contains("google") || path.contains("youtube") -> "youtube"
                    path.contains("instagram") -> "instagram"
                    path.contains("facebook") -> "facebook"
                    path.contains("linkedin") -> "linkedin"
                    path.contains("twitter") || path.contains("x") -> "twitter"
                    else -> ""
                }
            val code = uri.getQueryParameter("code") ?: ""
            val state = uri.getQueryParameter("state") ?: ""
            val status = uri.getQueryParameter("status")
                ?: if (code.isNotBlank() || uri.getQueryParameter("access_token") != null) "success"
                else if (uri.getQueryParameter("error") != null) "error"
                else "callback"
            val error = uri.getQueryParameter("error")
                ?: uri.getQueryParameter("error_description")
                ?: ""
            val accessToken = uri.getQueryParameter("access_token") ?: ""
            val refreshToken = uri.getQueryParameter("refresh_token") ?: ""
            val tokenType = uri.getQueryParameter("token_type") ?: ""
            val accountId = uri.getQueryParameter("account_id") ?: ""
            val accountName = uri.getQueryParameter("account_name") ?: ""
            val message = uri.getQueryParameter("message") ?: ""

            val payload = JSONObject().apply {
                put("provider", provider)
                put("status", status)
                if (code.isNotBlank()) put("code", code)
                if (state.isNotBlank()) put("state", state)
                if (error.isNotBlank()) put("error", error)
                if (message.isNotBlank()) put("message", message)
                if (accessToken.isNotBlank()) put("access_token", accessToken)
                if (refreshToken.isNotBlank()) put("refresh_token", refreshToken)
                if (tokenType.isNotBlank()) put("token_type", tokenType)
                if (accountId.isNotBlank()) put("account_id", accountId)
                if (accountName.isNotBlank()) put("account_name", accountName)
                put("raw_url", uri.toString())
            }

            android.util.Log.i("MainActivity", "Dispatching OAuth callback to bridge: $payload")
            if (::bridge.isInitialized) {
                bridge.dispatchAuthCallback(payload.toString())
            } else {
                webView.post {
                    if (::bridge.isInitialized) {
                        bridge.dispatchAuthCallback(payload.toString())
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("MainActivity", "Failed to handle OAuth callback URI: ${e.message}", e)
        }
    }

    override fun onResume() {
        super.onResume()
        screenDimmingState.onActivityResumed()
        if (::segmenterEngine.isInitialized && (segmenterEngine.isSessionActive || cameraCaptureManager.isRecording)) {
            updateRecordingScreenDimming(true)
        }
        scheduleRecordingScreenDim()

        // Re-confirm camera preview lifecycle when app returns from background (e.g. WhatsApp share)
        if (::cameraCaptureManager.isInitialized && ::previewView.isInitialized) {
            if (!cameraCaptureManager.isRecording && !segmenterEngine.isSessionActive) {
                initCameraLifecycle()
            }
        }
    }

    override fun onPause() {
        screenDimmingHandler.removeCallbacks(screenDimmingRunnable)
        screenDimmingState.onActivityPaused()
        applyRecordingScreenDimmed(false)
        super.onPause()
    }

    private fun checkAndRequestPermissions() {
        val camGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

        if (!camGranted) {
            if (::bridge.isInitialized) {
                bridge.notifyCameraStatus("permission_required", "Camera permission required")
            }
            permissionLauncher.launch(permissionsToRequest)
        } else {
            if (!micGranted) {
                cameraCaptureManager.setAudioEnabled(false)
            }
            initCameraLifecycle()
        }
    }

    override fun onDestroy() {
        screenDimmingHandler.removeCallbacks(screenDimmingRunnable)
        screenDimmingState.setRecordingEnabled(false)
        applyRecordingScreenDimmed(false)
        super.onDestroy()
        cameraCaptureManager.destroy()
        if (::webView.isInitialized) {
            webView.destroy()
        }
        // Only tear the media server down when the activity is really going away,
        // so a configuration change does not invalidate URLs the player is using.
        if (isFinishing) {
            com.resolvia.dispatch.media.RecordingHttpServer.stop()
        }
    }

}
