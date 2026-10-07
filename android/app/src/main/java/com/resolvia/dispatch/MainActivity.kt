package com.resolvia.dispatch

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.SegmenterEngine
import com.resolvia.dispatch.ui.components.DispatchBottomBar
import com.resolvia.dispatch.ui.components.NavigationTab
import com.resolvia.dispatch.ui.screens.RecordScreen
import com.resolvia.dispatch.ui.screens.SessionsScreen
import com.resolvia.dispatch.ui.screens.SettingsScreen
import com.resolvia.dispatch.ui.theme.CanvasBackground
import com.resolvia.dispatch.ui.theme.DispatchTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var cameraCaptureManager: CameraCaptureManager
    private lateinit var database: AppDatabase
    private lateinit var pairingManager: PairingManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Keep screen on during creator recording sessions (F-07 fix)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        database = AppDatabase.getDatabase(this)
        segmenterEngine = SegmenterEngine(this, database)
        cameraCaptureManager = CameraCaptureManager(this)
        pairingManager = PairingManager(this)

        setContent {
            DispatchApp(
                activity = this,
                segmenterEngine = segmenterEngine,
                cameraCaptureManager = cameraCaptureManager,
                database = database,
                pairingManager = pairingManager,
            )
        }
    }
}

@Composable
fun DispatchApp(
    activity: ComponentActivity,
    segmenterEngine: SegmenterEngine,
    cameraCaptureManager: CameraCaptureManager,
    database: AppDatabase,
    pairingManager: PairingManager,
) {
    val context = activity
    var currentTab by remember { mutableStateOf(NavigationTab.RECORD) }
    var isRecording by remember { mutableStateOf(false) }

    val recentSegments by database.recordingDao().getAllSegmentsFlow().collectAsState(initial = emptyList())

    // Required camera & mic permissions
    var hasCameraPermission by remember { mutableStateOf(false) }
    val permissionsToRequest = remember {
        val list = mutableListOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            list.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        list.toTypedArray()
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { perms ->
        val camGranted = perms[Manifest.permission.CAMERA] == true
        val micGranted = perms[Manifest.permission.RECORD_AUDIO] == true
        hasCameraPermission = camGranted && micGranted
        if (!hasCameraPermission) {
            Toast.makeText(context, "Camera & Mic permissions required for Dispatch.", Toast.LENGTH_LONG).show()
        }
    }

    LaunchedEffect(Unit) {
        val allGranted = permissionsToRequest.all {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }
        if (allGranted) {
            hasCameraPermission = true
        } else {
            permissionLauncher.launch(permissionsToRequest)
        }

    }

    DispatchTheme {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = CanvasBackground
        ) {
            Scaffold(
                containerColor = CanvasBackground,
                bottomBar = {
                    // Hide bottom bar during active recording for fullscreen viewfinder
                    if (!isRecording) {
                        DispatchBottomBar(
                            selectedTab = currentTab,
                            onTabSelected = { currentTab = it }
                        )
                    }
                }
            ) { paddingValues ->
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(if (isRecording) PaddingValues(0.dp) else paddingValues)
                ) {
                    when (currentTab) {
                        NavigationTab.RECORD -> {
                            RecordScreen(
                                isRecording = isRecording,
                                onStartRecording = {
                                    if (!hasCameraPermission) {
                                        permissionLauncher.launch(permissionsToRequest)
                                        return@RecordScreen
                                    }
                                    activity.lifecycleScope.launch {
                                        segmenterEngine.startSession(
                                            cameraManager = cameraCaptureManager,
                                            notes = "Dispatch Mobile Studio Capture"
                                        )
                                        isRecording = true
                                    }
                                },
                                onStopRecording = {
                                    activity.lifecycleScope.launch {
                                        segmenterEngine.stopSession(cameraManager = cameraCaptureManager)
                                        isRecording = false
                                        // Navigate to Sessions tab to see upload sync
                                        currentTab = NavigationTab.SESSIONS
                                        segmenterEngine.triggerYouTubeUpload()
                                    }
                                },
                                segmenterEngine = segmenterEngine,
                                cameraCaptureManager = cameraCaptureManager,
                                pairingManager = pairingManager,
                                recentSegments = recentSegments,
                                onNavigate = { currentTab = it }
                            )
                        }

                        NavigationTab.SESSIONS -> {
                            SessionsScreen(
                                recentSegments = recentSegments,
                                onSyncNow = {
                                    segmenterEngine.triggerYouTubeUpload()
                                },
                                onNavigate = { currentTab = it }
                            )
                        }

                        NavigationTab.CLIPS -> Box(Modifier.fillMaxSize(), contentAlignment = androidx.compose.ui.Alignment.Center) {
                            androidx.compose.material3.Text("Review clips on the Dispatch PC dashboard.", color = Color.White)
                        }

                        NavigationTab.SETTINGS -> {
                            SettingsScreen(
                                pairingManager = pairingManager,
                            )
                        }
                    }
                }
            }
        }
    }
}
