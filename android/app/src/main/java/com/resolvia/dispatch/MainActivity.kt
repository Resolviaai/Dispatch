package com.resolvia.dispatch

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
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
        lifecycleScope.launch {
            try {
                val ok = cameraCaptureManager.bindLifecycle(this@MainActivity)
                if (!ok) {
                    android.util.Log.e("MainActivity", "Failed to bind CameraX lifecycle")
                    Toast.makeText(this@MainActivity, "Camera hardware initialization failed", Toast.LENGTH_LONG).show()
                } else {
                    android.util.Log.i("MainActivity", "CameraX 1080p VideoCapture bound to MainActivity.")
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

        // Check required permissions on startup
        checkAndRequestPermissions()

        // Configure hardware-accelerated full-bleed WebView
        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#161616")) // Layer 0: bg-studio anchor
            scrollBarStyle = View.SCROLLBARS_INSIDE_OVERLAY
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false

            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                databaseEnabled = true
                allowFileAccess = true
                allowContentAccess = true
                mediaPlaybackRequiresUserGesture = false
                loadWithOverviewMode = true
                useWideViewPort = true
                cacheMode = WebSettings.LOAD_DEFAULT
            }

            webViewClient = object : WebViewClient() {}
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    request.grant(request.resources)
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
        webView.loadUrl("file:///android_asset/web/index.html")

        setContentView(webView)
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
