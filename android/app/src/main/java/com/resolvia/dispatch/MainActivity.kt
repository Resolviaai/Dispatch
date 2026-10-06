package com.resolvia.dispatch

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.SegmenterEngine
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var cameraCaptureManager: CameraCaptureManager
    private lateinit var database: AppDatabase
    private lateinit var pairingManager: PairingManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
                pairingManager = pairingManager
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
    pairingManager: PairingManager
) {
    val context = LocalContext.current
    val pendingCount by database.recordingDao().getPendingOutboxCountFlow().collectAsState(initial = 0)
    var isRecording by remember { mutableStateOf(false) }
    var currentSession by remember { mutableStateOf<String?>(null) }
    var showPairingDialog by remember { mutableStateOf(false) }
    var hasCameraPermission by remember { mutableStateOf(false) }
    var previewViewRef by remember { mutableStateOf<PreviewView?>(null) }
    var isTorchOn by remember { mutableStateOf(false) }
    var isAeAfLocked by remember { mutableStateOf(false) }
    var currentLens by remember { mutableStateOf(CameraSelector.LENS_FACING_BACK) }
    var currentZoom by remember { mutableStateOf(1.0f) }

    // Required permissions
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
            Toast.makeText(context, "Camera and Microphone permissions are required for Dispatch.", Toast.LENGTH_LONG).show()
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

    DispatchMobileTheme {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = Color(0xFF070A0F)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(20.dp),
                verticalArrangement = Arrangement.SpaceBetween,
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                // Top Status & Navigation Bar
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(
                            text = "DISPATCH",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                            color = Color.White
                        )
                        Text(
                            text = "POCO C65 V1 AUTONOMOUS",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = Color(0xFF64748B)
                        )
                    }

                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        // Pairing Settings Button
                        Box(
                            modifier = Modifier
                                .background(Color(0xFF131A26), RoundedCornerShape(8.dp))
                                .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
                                .clickable { showPairingDialog = true }
                                .padding(horizontal = 10.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "PAIRING",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                color = Color(0xFF38BDF8)
                            )
                        }

                        // Outbox Pill
                        Box(
                            modifier = Modifier
                                .background(Color(0xFF131A26), RoundedCornerShape(8.dp))
                                .border(1.dp, Color(0x1AFFFFFF), RoundedCornerShape(8.dp))
                                .padding(horizontal = 10.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "OUTBOX: $pendingCount",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                color = if (pendingCount > 0) Color(0xFFF59E0B) else Color(0xFF94A3B8)
                            )
                        }
                    }
                }

                // Center Viewfinder / Hardware Preview Card
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .padding(vertical = 16.dp)
                        .clip(RoundedCornerShape(20.dp))
                        .background(Color(0xFF0B0F17))
                        .border(1.dp, Color(0x1AFFFFFF), RoundedCornerShape(20.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    if (hasCameraPermission) {
                        AndroidView(
                            factory = { ctx ->
                                PreviewView(ctx).apply {
                                    previewViewRef = this
                                    cameraCaptureManager.initializeCamera(activity, this)
                                    setOnTouchListener { v, event ->
                                        if (event.action == android.view.MotionEvent.ACTION_UP) {
                                            val factory = meteringPointFactory
                                            val point = factory.createPoint(event.x, event.y)
                                            cameraCaptureManager.focusOnPoint(point)
                                            v.performClick()
                                        }
                                        true
                                    }
                                }
                            },
                            modifier = Modifier.fillMaxSize()
                        )

                        // Top-Right Pro Camera Quick Controls
                        Column(
                            modifier = Modifier
                                .align(Alignment.TopEnd)
                                .padding(12.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                            horizontalAlignment = Alignment.End
                        ) {
                            // Flip Camera Lens
                            Box(
                                modifier = Modifier
                                    .background(Color(0xCC131A26), RoundedCornerShape(8.dp))
                                    .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
                                    .clickable {
                                        previewViewRef?.let { pv ->
                                            cameraCaptureManager.switchCamera(activity, pv) {
                                                currentLens = cameraCaptureManager.currentLensFacing
                                                isTorchOn = false
                                                isAeAfLocked = false
                                            }
                                        }
                                    }
                                    .padding(horizontal = 8.dp, vertical = 6.dp)
                            ) {
                                Text(
                                    text = if (currentLens == CameraSelector.LENS_FACING_BACK) "BACK" else "FRONT",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 10.sp,
                                    color = Color.White
                                )
                            }

                            // Torch (Back Camera Only)
                            if (currentLens == CameraSelector.LENS_FACING_BACK) {
                                Box(
                                    modifier = Modifier
                                        .background(if (isTorchOn) Color(0xCCF59E0B) else Color(0xCC131A26), RoundedCornerShape(8.dp))
                                        .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
                                        .clickable {
                                            isTorchOn = cameraCaptureManager.toggleTorch()
                                        }
                                        .padding(horizontal = 8.dp, vertical = 6.dp)
                                ) {
                                    Text(
                                        text = if (isTorchOn) "TORCH ON" else "TORCH",
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 10.sp,
                                        color = if (isTorchOn) Color.Black else Color.White
                                    )
                                }
                            }

                            // AE / AF Lock Toggle
                            Box(
                                modifier = Modifier
                                    .background(if (isAeAfLocked) Color(0xCCF59E0B) else Color(0xCC131A26), RoundedCornerShape(8.dp))
                                    .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
                                    .clickable {
                                        isAeAfLocked = cameraCaptureManager.toggleAeAfLock()
                                    }
                                    .padding(horizontal = 8.dp, vertical = 6.dp)
                            ) {
                                Text(
                                    text = if (isAeAfLocked) "AE/AF LOCKED" else "LOCK",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 10.sp,
                                    color = if (isAeAfLocked) Color.Black else Color.White
                                )
                            }

                            // Zoom Toggle (1x / 2x)
                            Box(
                                modifier = Modifier
                                    .background(Color(0xCC131A26), RoundedCornerShape(8.dp))
                                    .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
                                    .clickable {
                                        currentZoom = if (currentZoom == 1.0f) 2.0f else 1.0f
                                        cameraCaptureManager.setZoom(currentZoom)
                                    }
                                    .padding(horizontal = 8.dp, vertical = 6.dp)
                            ) {
                                Text(
                                    text = "${currentZoom.toInt()}x",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 10.sp,
                                    color = Color.White
                                )
                            }
                        }

                        // Bottom Viewfinder Info Bar (Quality & Mic Status)
                        Row(
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .fillMaxWidth()
                                .padding(12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(
                                modifier = Modifier
                                    .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 8.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "1080p FHD STUDIO",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFF38BDF8)
                                )
                            }

                            Box(
                                modifier = Modifier
                                    .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 8.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "MIC: AUTO (EXT DETECT)",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFF10B981)
                                )
                            }
                        }
                    } else {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                            modifier = Modifier.padding(24.dp)
                        ) {
                            Text(
                                text = "CAMERA PERMISSION REQUIRED",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 12.sp,
                                color = Color(0xFFEF4444)
                            )
                            Spacer(modifier = Modifier.height(12.dp))
                            Button(
                                onClick = { permissionLauncher.launch(permissionsToRequest) },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2563EB))
                            ) {
                                Text("GRANT ACCESS", fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                            }
                        }
                    }

                    // Recording Overlay Badge
                    Box(
                        modifier = Modifier
                            .align(Alignment.TopStart)
                            .padding(12.dp)
                            .background(
                                if (isRecording) Color(0xCCEF4444) else Color(0x99000000),
                                RoundedCornerShape(6.dp)
                            )
                            .padding(horizontal = 10.dp, vertical = 4.dp)
                    ) {
                        Text(
                            text = if (isRecording) "LIVE REC (10-MIN ROLLING)" else "STANDBY",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            color = Color.White
                        )
                    }

                    // Current Session Info Overlay
                    if (currentSession != null) {
                        Box(
                            modifier = Modifier
                                .align(Alignment.BottomStart)
                                .padding(12.dp)
                                .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                text = currentSession ?: "",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 10.sp,
                                color = Color(0xFF94A3B8)
                            )
                        }
                    }
                }

                // Bottom Action Controls
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 12.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    // Tactile Master Record/Stop Button
                    Button(
                        onClick = {
                            if (!hasCameraPermission) {
                                permissionLauncher.launch(permissionsToRequest)
                                return@Button
                            }
                            activity.lifecycleScope.launch {
                                if (!isRecording) {
                                    val sess = segmenterEngine.startSession(
                                        cameraManager = cameraCaptureManager,
                                        notes = "POCO C65 Mobile Capture"
                                    )
                                    currentSession = sess
                                    isRecording = true
                                } else {
                                    segmenterEngine.stopSession(cameraManager = cameraCaptureManager)
                                    isRecording = false
                                    currentSession = null
                                }
                            }
                        },
                        modifier = Modifier.size(88.dp),
                        shape = CircleShape,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (isRecording) Color(0xFFDC2626) else Color(0xFF2563EB)
                        ),
                        contentPadding = PaddingValues(0.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(32.dp)
                                .background(
                                    Color.White,
                                    if (isRecording) RoundedCornerShape(6.dp) else CircleShape
                                )
                        )
                    }

                    Text(
                        text = if (isRecording) "TAP TO STOP & FINALIZE" else "TAP TO START RECORDING",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = if (isRecording) Color(0xFFF87171) else Color(0xFF94A3B8)
                    )
                }
            }

            // Pairing & System Setup Modal Dialog
            if (showPairingDialog) {
                PairingSetupDialog(
                    pairingManager = pairingManager,
                    onDismiss = { showPairingDialog = false },
                    context = context
                )
            }
        }
    }
}

@Composable
fun PairingSetupDialog(
    pairingManager: PairingManager,
    onDismiss: () -> Unit,
    context: Context
) {
    var pairingUriInput by remember { mutableStateOf("") }
    var lanHostInput by remember { mutableStateOf(pairingManager.lanHost) }
    var tokenInput by remember { mutableStateOf(pairingManager.authToken) }
    var feedbackMessage by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF0F172A),
        title = {
            Text(
                text = "PAIRING & SYSTEM SETUP",
                fontFamily = FontFamily.Monospace,
                fontSize = 15.sp,
                color = Color.White
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "Paste connection string from laptop dashboard or adjust LAN target:",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    color = Color(0xFF94A3B8)
                )

                OutlinedTextField(
                    value = pairingUriInput,
                    onValueChange = { pairingUriInput = it },
                    placeholder = { Text("dispatch://pair?lan=...", fontSize = 11.sp, color = Color(0xFF475569)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Button(
                    onClick = {
                        if (pairingUriInput.isNotBlank()) {
                            val success = pairingManager.saveFromConnectionString(pairingUriInput)
                            if (success) {
                                lanHostInput = pairingManager.lanHost
                                tokenInput = pairingManager.authToken
                                feedbackMessage = "Pairing string saved successfully."
                            } else {
                                feedbackMessage = "Invalid pairing string format."
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1E293B))
                ) {
                    Text("APPLY PAIRING STRING", fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                }

                HorizontalDivider(color = Color(0x1AFFFFFF))

                // Manual fields
                OutlinedTextField(
                    value = lanHostInput,
                    onValueChange = {
                        lanHostInput = it
                        pairingManager.lanHost = it
                    },
                    label = { Text("LAN Host Endpoint", fontSize = 10.sp) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                OutlinedTextField(
                    value = tokenInput,
                    onValueChange = {
                        tokenInput = it
                        pairingManager.authToken = it
                    },
                    label = { Text("Auth Security Token", fontSize = 10.sp) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                HorizontalDivider(color = Color(0x1AFFFFFF))

                // POCO C65 / MIUI Battery Optimization Guide
                Button(
                    onClick = {
                        try {
                            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                                if (!pm.isIgnoringBatteryOptimizations(context.packageName)) {
                                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                                        data = Uri.parse("package:${context.packageName}")
                                    }
                                    context.startActivity(intent)
                                } else {
                                    Toast.makeText(context, "Battery optimizations already ignored.", Toast.LENGTH_SHORT).show()
                                }
                            }
                        } catch (e: Exception) {
                            val fallbackIntent = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                            context.startActivity(fallbackIntent)
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF0284C7))
                ) {
                    Text("IGNORE BATTERY RESTRICTIONS (POCO C65)", fontFamily = FontFamily.Monospace, fontSize = 10.sp)
                }

                if (feedbackMessage != null) {
                    Text(
                        text = feedbackMessage ?: "",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = Color(0xFF10B981)
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("DONE", fontFamily = FontFamily.Monospace, color = Color(0xFF38BDF8))
            }
        }
    )
}

@Composable
fun DispatchMobileTheme(content: @Composable () -> Unit) {
    MaterialTheme(content = content)
}
