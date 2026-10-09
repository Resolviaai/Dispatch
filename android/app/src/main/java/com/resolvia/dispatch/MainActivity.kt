package com.resolvia.dispatch

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
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
import com.resolvia.dispatch.recorder.SegmenterEngine

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var cameraCaptureManager: CameraCaptureManager
    private lateinit var database: AppDatabase
    private lateinit var pairingManager: PairingManager
    private lateinit var webView: WebView
    private lateinit var previewView: PreviewView

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
        if (!cam || !mic) {
            Toast.makeText(this, "Camera & Mic permissions required for Dispatch.", Toast.LENGTH_LONG).show()
        } else {
            initCameraLifecycle()
        }
    }

    private fun initCameraLifecycle() {
        previewView.post {
            try {
                cameraCaptureManager.initializeCamera(
                    lifecycleOwner = this@MainActivity,
                    previewView = previewView
                ) {
                    android.util.Log.i("MainActivity", "CameraX 1080p VideoCapture + Preview bound successfully.")
                }
            } catch (e: Exception) {
                android.util.Log.e("MainActivity", "Error binding camera lifecycle: ${e.message}", e)
                Toast.makeText(this@MainActivity, "Camera error: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
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

        // Initialize native camera PreviewView (placed underneath WebView)
        previewView = PreviewView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }

        // Set up WebViewAssetLoader to safely serve assets over https://appassets.androidplatform.net
        // This is required for Chromium to execute Vite ES modules (<script type="module">) without file:// CORS blocks.
        val assetLoader = WebViewAssetLoader.Builder()
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
                databaseEnabled = true
                allowFileAccess = true
                allowContentAccess = true
                allowFileAccessFromFileURLs = true
                allowUniversalAccessFromFileURLs = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                mediaPlaybackRequiresUserGesture = false
                loadWithOverviewMode = true
                useWideViewPort = true
                cacheMode = WebSettings.LOAD_DEFAULT
            }

            webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(
                    view: WebView,
                    request: WebResourceRequest
                ): WebResourceResponse? {
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
                    request.grant(request.resources)
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
        val bridge = DispatchNativeBridge(
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
        webView.loadUrl("https://appassets.androidplatform.net/assets/web/index.html")

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
    }

    private fun checkAndRequestPermissions() {
        val allGranted = permissionsToRequest.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
        if (!allGranted) {
            permissionLauncher.launch(permissionsToRequest)
        } else {
            initCameraLifecycle()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::webView.isInitialized) {
            webView.destroy()
        }
    }
}
