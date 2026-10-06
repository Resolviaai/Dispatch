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
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.data.SegmentEntity
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.SegmenterEngine
import com.resolvia.dispatch.sync.LiveSyncManager
import com.resolvia.dispatch.sync.SyncState
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var cameraCaptureManager: CameraCaptureManager
    private lateinit var database: AppDatabase
    private lateinit var pairingManager: PairingManager
    private lateinit var liveSyncManager: LiveSyncManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        database = AppDatabase.getDatabase(this)
        segmenterEngine = SegmenterEngine(this, database)
        cameraCaptureManager = CameraCaptureManager(this)
        pairingManager = PairingManager(this)
        liveSyncManager = LiveSyncManager(this)

        setContent {
            DispatchApp(
                activity = this,
                segmenterEngine = segmenterEngine,
                cameraCaptureManager = cameraCaptureManager,
                database = database,
                pairingManager = pairingManager,
                liveSyncManager = liveSyncManager
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
    liveSyncManager: LiveSyncManager
) {
    val context = LocalContext.current
    val pendingCount by database.recordingDao().getPendingOutboxCountFlow().collectAsState(initial = 0)
    val recentSegments by database.recordingDao().getAllSegmentsFlow().collectAsState(initial = emptyList())
    val syncState by liveSyncManager.syncState.collectAsState()

    var isRecording by remember { mutableStateOf(false) }
    var currentSession by remember { mutableStateOf<String?>(null) }
    var showPairingDialog by remember { mutableStateOf(false) }
    var showOutboxSheet by remember { mutableStateOf(false) }
    var hasCameraPermission by remember { mutableStateOf(false) }
    var previewViewRef by remember { mutableStateOf<PreviewView?>(null) }

    // Pro Camera Controls State
    var isTorchOn by remember { mutableStateOf(false) }
    var isAeAfLocked by remember { mutableStateOf(false) }
    var currentLens by remember { mutableStateOf(CameraSelector.LENS_FACING_BACK) }
    var currentZoom by remember { mutableFloatStateOf(1.0f) }
    var exposureBias by remember { mutableIntStateOf(0) }
    var showFramingGrid by remember { mutableStateOf(false) }
    var tapFocusOffset by remember { mutableStateOf<Offset?>(null) }
    var showFocusReticle by remember { mutableStateOf(false) }

    // Running Recording Timer State
    var recordingDurationSeconds by remember { mutableLongStateOf(0L) }

    // Ticking Timer Effect
    LaunchedEffect(isRecording) {
        if (isRecording) {
            recordingDurationSeconds = 0L
            while (isRecording) {
                delay(1000L)
                recordingDurationSeconds++
            }
        } else {
            recordingDurationSeconds = 0L
        }
    }

    // Auto-dismiss Focus Reticle after 2.5 seconds (unless AE/AF Locked)
    LaunchedEffect(tapFocusOffset) {
        if (tapFocusOffset != null) {
            showFocusReticle = true
            delay(2500L)
            if (!isAeAfLocked) {
                showFocusReticle = false
            }
        }
    }

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

    DispatchMobileTheme {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = Color(0xFF070A0F)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.SpaceBetween,
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                // 1. Top Status & Navigation Bar
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                text = "DISPATCH",
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.Bold,
                                fontSize = 17.sp,
                                color = Color.White
                            )
                            Box(
                                modifier = Modifier
                                    .background(Color(0xFF1E1B4B), RoundedCornerShape(4.dp))
                                    .border(1.dp, Color(0xFF4338CA), RoundedCornerShape(4.dp))
                                    .padding(horizontal = 6.dp, vertical = 1.dp)
                            ) {
                                Text(
                                    text = "PRO CAMERA",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFFA5B4FC)
                                )
                            }
                        }
                        Text(
                            text = if (pairingManager.lanHost.isNotBlank()) "PC: ${pairingManager.lanHost.replace("http://", "")}" else "WI-FI STANDBY",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = if (pairingManager.lanHost.isNotBlank()) Color(0xFF10B981) else Color(0xFF64748B)
                        )
                    }

                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        // Pairing Setup Button
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
                                fontWeight = FontWeight.SemiBold,
                                color = Color(0xFF38BDF8)
                            )
                        }

                        // Outbox Pill (Clickable -> Opens Outbox & Sync Sheet)
                        val outboxBg = when {
                            syncState.isSyncing -> Color(0xFF065F46)
                            pendingCount > 0 -> Color(0x33F59E0B)
                            else -> Color(0xFF131A26)
                        }
                        val outboxBorder = when {
                            syncState.isSyncing -> Color(0xFF10B981)
                            pendingCount > 0 -> Color(0xFFF59E0B)
                            else -> Color(0x1AFFFFFF)
                        }
                        val outboxText = when {
                            syncState.isSyncing -> "SYNCING: ${syncState.percent}%"
                            pendingCount > 0 -> "OUTBOX: $pendingCount"
                            else -> "OUTBOX: 0"
                        }
                        val outboxTextColor = when {
                            syncState.isSyncing -> Color(0xFF6EE7B7)
                            pendingCount > 0 -> Color(0xFFF59E0B)
                            else -> Color(0xFF94A3B8)
                        }
                        Box(
                            modifier = Modifier
                                .background(outboxBg, RoundedCornerShape(8.dp))
                                .border(1.dp, outboxBorder, RoundedCornerShape(8.dp))
                                .clickable { showOutboxSheet = true }
                                .padding(horizontal = 10.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = outboxText,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = outboxTextColor
                            )
                        }
                    }
                }

                // 2. Center Viewfinder & Pro HUD
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .padding(vertical = 10.dp)
                        .clip(RoundedCornerShape(18.dp))
                        .background(Color(0xFF0B0F17))
                        .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(18.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    if (hasCameraPermission) {
                        // CameraX Hardware Preview View
                        AndroidView(
                            factory = { ctx ->
                                PreviewView(ctx).apply {
                                    previewViewRef = this
                                    cameraCaptureManager.initializeCamera(activity, this)
                                    setOnTouchListener { v, event ->
                                        if (event.action == android.view.MotionEvent.ACTION_UP) {
                                            tapFocusOffset = Offset(event.x, event.y)
                                            showFocusReticle = true
                                            cameraCaptureManager.focusOnPoint(event.x, event.y, this) { success ->
                                                if (success) {
                                                    // Focus locked onto point
                                                }
                                            }
                                            v.performClick()
                                        }
                                        true
                                    }
                                }
                            },
                            modifier = Modifier.fillMaxSize()
                        )

                        // Rule of Thirds & 9:16 Shorts Safe Zone Overlay
                        if (showFramingGrid) {
                            FramingGridOverlay()
                        }

                        // Pro Tap-To-Focus Reticle
                        if (showFocusReticle && tapFocusOffset != null) {
                            FocusReticle(
                                offset = tapFocusOffset!!,
                                isLocked = isAeAfLocked
                            )
                        }

                        // Top-Center High-Visibility Recording Timer HUD
                        if (isRecording) {
                            val hours = recordingDurationSeconds / 3600
                            val minutes = (recordingDurationSeconds % 3600) / 60
                            val seconds = recordingDurationSeconds % 60
                            val timerText = String.format("%02d:%02d:%02d", hours, minutes, seconds)

                            Row(
                                modifier = Modifier
                                    .align(Alignment.TopCenter)
                                    .padding(top = 14.dp)
                                    .background(Color(0xDD000000), RoundedCornerShape(20.dp))
                                    .border(1.dp, Color(0x88EF4444), RoundedCornerShape(20.dp))
                                    .padding(horizontal = 14.dp, vertical = 6.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                // Pulsing Red Record Light
                                val infiniteTransition = rememberInfiniteTransition(label = "rec_pulse")
                                val alpha by infiniteTransition.animateFloat(
                                    initialValue = 0.3f,
                                    targetValue = 1.0f,
                                    animationSpec = infiniteRepeatable(
                                        animation = tween(600, easing = LinearEasing),
                                        repeatMode = RepeatMode.Reverse
                                    ),
                                    label = "rec_alpha"
                                )
                                Box(
                                    modifier = Modifier
                                        .size(10.dp)
                                        .background(Color.Red.copy(alpha = alpha), CircleShape)
                                )

                                Text(
                                    text = "REC $timerText",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color(0xFFF87171)
                                )

                                Box(
                                    modifier = Modifier
                                        .background(Color(0x33FFFFFF), RoundedCornerShape(4.dp))
                                        .padding(horizontal = 5.dp, vertical = 1.dp)
                                ) {
                                    Text(
                                        text = "SEG #${segmenterEngine.currentSequenceNumber.coerceAtLeast(1)}",
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 9.sp,
                                        color = Color.White
                                    )
                                }
                            }
                        }

                        // AE/AF Lock Centered Pill (when locked)
                        if (isAeAfLocked) {
                            Box(
                                modifier = Modifier
                                    .align(Alignment.TopCenter)
                                    .padding(top = if (isRecording) 56.dp else 14.dp)
                                    .background(Color(0xFFF59E0B), RoundedCornerShape(12.dp))
                                    .clickable {
                                        previewViewRef?.let { pv ->
                                            isAeAfLocked = cameraCaptureManager.toggleAeAfLock(pv)
                                        }
                                    }
                                    .padding(horizontal = 12.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "AE / AF LOCKED (TAP TO RELEASE)",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color.Black
                                )
                            }
                        }

                        // Top-Right Quick Pro Controls (Grid, Torch, Lock, Flip, Zoom)
                        Column(
                            modifier = Modifier
                                .align(Alignment.TopEnd)
                                .padding(12.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                            horizontalAlignment = Alignment.End
                        ) {
                            // Framing Grid Toggle
                            ProControlPill(
                                label = if (showFramingGrid) "GRID ON" else "GRID",
                                isActive = showFramingGrid,
                                onClick = { showFramingGrid = !showFramingGrid }
                            )

                            // Torch / Flashlight Toggle (Back Camera Only)
                            if (currentLens == CameraSelector.LENS_FACING_BACK) {
                                ProControlPill(
                                    label = if (isTorchOn) "TORCH ON" else "TORCH",
                                    isActive = isTorchOn,
                                    activeColor = Color(0xFFF59E0B),
                                    onClick = { isTorchOn = cameraCaptureManager.toggleTorch() }
                                )
                            }

                            // AE/AF Lock Toggle
                            ProControlPill(
                                label = if (isAeAfLocked) "LOCKED" else "LOCK",
                                isActive = isAeAfLocked,
                                activeColor = Color(0xFFF59E0B),
                                onClick = {
                                    previewViewRef?.let { pv ->
                                        isAeAfLocked = cameraCaptureManager.toggleAeAfLock(pv)
                                    }
                                }
                            )

                            // Flip Camera (Front <-> Back)
                            ProControlPill(
                                label = if (currentLens == CameraSelector.LENS_FACING_BACK) "BACK" else "FRONT",
                                isActive = false,
                                onClick = {
                                    previewViewRef?.let { pv ->
                                        cameraCaptureManager.switchCamera(activity, pv) {
                                            currentLens = cameraCaptureManager.currentLensFacing
                                            isTorchOn = false
                                            isAeAfLocked = false
                                        }
                                    }
                                }
                            )

                            // Zoom (1x / 2x)
                            ProControlPill(
                                label = "${currentZoom.toInt()}x",
                                isActive = currentZoom > 1.0f,
                                onClick = {
                                    currentZoom = cameraCaptureManager.toggleZoom()
                                }
                            )
                        }

                        // Viewfinder Bottom HUD Bar (Studio Info + Mic Level + EV)
                        Row(
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .fillMaxWidth()
                                .padding(12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            // Studio 1080p Badge
                            Box(
                                modifier = Modifier
                                    .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 8.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "1080p FHD 30FPS",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFF38BDF8)
                                )
                            }

                            // Live Audio VU Meter
                            Row(
                                modifier = Modifier
                                    .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 8.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Text(
                                    text = "MIC",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFF10B981)
                                )
                                AudioVUBars(isActive = isRecording)
                            }

                            // EV Stepper Controls (-1 / 0 / +1)
                            Row(
                                modifier = Modifier
                                    .background(Color(0xCC070A0F), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x22FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 4.dp, vertical = 2.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(2.dp)
                            ) {
                                Text(
                                    text = "-",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color.White,
                                    modifier = Modifier
                                        .clickable {
                                            exposureBias = cameraCaptureManager.stepExposure(-1)
                                        }
                                        .padding(horizontal = 4.dp)
                                )
                                Text(
                                    text = "EV: $exposureBias",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    color = Color(0xFF94A3B8)
                                )
                                Text(
                                    text = "+",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color.White,
                                    modifier = Modifier
                                        .clickable {
                                            exposureBias = cameraCaptureManager.stepExposure(1)
                                        }
                                        .padding(horizontal = 4.dp)
                                )
                            }
                        }

                    } else {
                        // Permissions Required View
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                            modifier = Modifier.padding(24.dp)
                        ) {
                            Text(
                                text = "CAMERA & AUDIO ACCESS REQUIRED",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 12.sp,
                                color = Color(0xFFEF4444)
                            )
                            Spacer(modifier = Modifier.height(12.dp))
                            Button(
                                onClick = { permissionLauncher.launch(permissionsToRequest) },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2563EB))
                            ) {
                                Text("GRANT PERMISSIONS", fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                            }
                        }
                    }
                }

                // 3. Bottom Master Action Bar
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    // Tactile Master Record/Stop Button
                    val buttonGlowColor = if (isRecording) Color(0xFFEF4444) else Color(0xFF2563EB)

                    Box(
                        contentAlignment = Alignment.Center,
                        modifier = Modifier
                            .size(86.dp)
                            .background(buttonGlowColor.copy(alpha = 0.2f), CircleShape)
                            .border(3.dp, buttonGlowColor, CircleShape)
                            .clickable {
                                if (!hasCameraPermission) {
                                    permissionLauncher.launch(permissionsToRequest)
                                    return@clickable
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
                            }
                    ) {
                        // Inner Shape: Red Square when recording, Circle when stopped
                        Box(
                            modifier = Modifier
                                .size(if (isRecording) 30.dp else 40.dp)
                                .background(
                                    Color.White,
                                    if (isRecording) RoundedCornerShape(6.dp) else CircleShape
                                )
                        )
                    }

                    Text(
                        text = if (isRecording) "TAP TO STOP & SYNC TO PC" else "TAP TO START RECORDING",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (isRecording) Color(0xFFF87171) else Color(0xFF94A3B8)
                    )
                }
            }

            // Slide-Up Outbox & Sync Drawer
            if (showOutboxSheet) {
                OutboxSyncDialog(
                    pendingCount = pendingCount,
                    segments = recentSegments,
                    pairingManager = pairingManager,
                    syncState = syncState,
                    onSyncNow = {
                        segmenterEngine.triggerBackgroundSync()
                        (activity.application as? DispatchApplication)?.applicationScope?.launch {
                            liveSyncManager.syncNow()
                        } ?: activity.lifecycleScope.launch {
                            liveSyncManager.syncNow()
                        }
                    },
                    onDismiss = { showOutboxSheet = false }
                )
            }

            // Pairing Setup Dialog
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

/**
 * Reusable Pro Camera Control Pill
 */
@Composable
fun ProControlPill(
    label: String,
    isActive: Boolean,
    activeColor: Color = Color(0xFF38BDF8),
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .background(if (isActive) activeColor else Color(0xCC131A26), RoundedCornerShape(8.dp))
            .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(8.dp))
            .clickable { onClick() }
            .padding(horizontal = 9.dp, vertical = 6.dp)
    ) {
        Text(
            text = label,
            fontFamily = FontFamily.Monospace,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            color = if (isActive) Color.Black else Color.White
        )
    }
}

/**
 * Tap-to-Focus Reticle with corner marks and pulse animation
 */
@Composable
fun FocusReticle(offset: Offset, isLocked: Boolean) {
    val reticleColor = if (isLocked) Color(0xFFF59E0B) else Color(0xFF38BDF8)
    Canvas(modifier = Modifier.fillMaxSize()) {
        val size = 70.dp.toPx()
        val half = size / 2f
        val left = offset.x - half
        val top = offset.y - half
        val cornerLen = 16.dp.toPx()

        // Outer Bounding Box (dashed or solid corners)
        // Top-Left
        drawLine(reticleColor, Offset(left, top), Offset(left + cornerLen, top), strokeWidth = 3.dp.toPx())
        drawLine(reticleColor, Offset(left, top), Offset(left, top + cornerLen), strokeWidth = 3.dp.toPx())

        // Top-Right
        drawLine(reticleColor, Offset(left + size, top), Offset(left + size - cornerLen, top), strokeWidth = 3.dp.toPx())
        drawLine(reticleColor, Offset(left + size, top), Offset(left + size, top + cornerLen), strokeWidth = 3.dp.toPx())

        // Bottom-Left
        drawLine(reticleColor, Offset(left, top + size), Offset(left + cornerLen, top + size), strokeWidth = 3.dp.toPx())
        drawLine(reticleColor, Offset(left, top + size), Offset(left, top + size - cornerLen), strokeWidth = 3.dp.toPx())

        // Bottom-Right
        drawLine(reticleColor, Offset(left + size, top + size), Offset(left + size - cornerLen, top + size), strokeWidth = 3.dp.toPx())
        drawLine(reticleColor, Offset(left + size, top + size), Offset(left + size, top + size - cornerLen), strokeWidth = 3.dp.toPx())

        // Center Point
        drawCircle(reticleColor, radius = 3.dp.toPx(), center = offset)
    }
}

/**
 * Rule of Thirds and 9:16 Shorts Safe Zone Overlay
 */
@Composable
fun FramingGridOverlay() {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val w = size.width
        val h = size.height

        // 3x3 Grid Lines
        val gridColor = Color(0x33FFFFFF)
        drawLine(gridColor, Offset(w / 3f, 0f), Offset(w / 3f, h), strokeWidth = 1f)
        drawLine(gridColor, Offset(w * 2f / 3f, 0f), Offset(w * 2f / 3f, h), strokeWidth = 1f)
        drawLine(gridColor, Offset(0f, h / 3f), Offset(w, h / 3f), strokeWidth = 1f)
        drawLine(gridColor, Offset(0f, h * 2f / 3f), Offset(w, h * 2f / 3f), strokeWidth = 1f)

        // Center 9:16 Shorts Safe Boundary
        val safeMarginX = w * 0.12f
        val safeMarginY = h * 0.08f
        drawRect(
            color = Color(0x22818CF8),
            topLeft = Offset(safeMarginX, safeMarginY),
            size = Size(w - (safeMarginX * 2), h - (safeMarginY * 2)),
            style = Stroke(width = 1.5.dp.toPx())
        )
    }
}

/**
 * Animated Mic VU Bars
 */
@Composable
fun AudioVUBars(isActive: Boolean) {
    val infiniteTransition = rememberInfiniteTransition(label = "vu_bars")
    val bar1 by infiniteTransition.animateFloat(
        initialValue = 4f, targetValue = 12f,
        animationSpec = infiniteRepeatable(tween(300, easing = LinearEasing), RepeatMode.Reverse),
        label = "b1"
    )
    val bar2 by infiniteTransition.animateFloat(
        initialValue = 10f, targetValue = 4f,
        animationSpec = infiniteRepeatable(tween(250, easing = LinearEasing), RepeatMode.Reverse),
        label = "b2"
    )
    val bar3 by infiniteTransition.animateFloat(
        initialValue = 6f, targetValue = 14f,
        animationSpec = infiniteRepeatable(tween(350, easing = LinearEasing), RepeatMode.Reverse),
        label = "b3"
    )

    Row(
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.Bottom,
        modifier = Modifier.height(14.dp)
    ) {
        val h1 = if (isActive) bar1.dp else 4.dp
        val h2 = if (isActive) bar2.dp else 6.dp
        val h3 = if (isActive) bar3.dp else 4.dp

        Box(modifier = Modifier.width(2.dp).height(h1).background(Color(0xFF10B981), RoundedCornerShape(1.dp)))
        Box(modifier = Modifier.width(2.dp).height(h2).background(Color(0xFF10B981), RoundedCornerShape(1.dp)))
        Box(modifier = Modifier.width(2.dp).height(h3).background(Color(0xFF10B981), RoundedCornerShape(1.dp)))
    }
}

/**
 * Outbox & Sync Status Drawer Modal
 */
@Composable
fun OutboxSyncDialog(
    pendingCount: Int,
    segments: List<SegmentEntity>,
    pairingManager: PairingManager,
    syncState: SyncState,
    onSyncNow: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF0F172A),
        title = {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "UPLOAD QUEUE & SYNC",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.White
                )
                val badgeText = when {
                    syncState.isSyncing -> "SYNCING ${syncState.percent}%"
                    pendingCount > 0 -> "$pendingCount PENDING"
                    else -> "ALL SYNCED"
                }
                val badgeBg = when {
                    syncState.isSyncing -> Color(0xFF065F46)
                    pendingCount > 0 -> Color(0xFFF59E0B)
                    else -> Color(0xFF10B981)
                }
                val badgeColor = if (syncState.isSyncing) Color(0xFF6EE7B7) else Color.Black
                Box(
                    modifier = Modifier
                        .background(badgeBg, RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = badgeText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        color = badgeColor
                    )
                }
            }
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // Connection Target Summary
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFF1E293B), RoundedCornerShape(8.dp))
                        .padding(10.dp)
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text(
                            text = "TARGET: ${if (pairingManager.lanHost.isNotBlank()) pairingManager.lanHost else "Home Wi-Fi (192.168.0.101:8000)"}",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = Color(0xFF38BDF8)
                        )
                        Text(
                            text = "STATUS: ${if (syncState.isSyncing) syncState.message else if (syncState.error != null) syncState.error else "Ready"}",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = if (syncState.error != null) Color(0xFFF87171) else Color(0xFF94A3B8)
                        )
                    }
                }

                // Live Sync Progress Bar
                if (syncState.isSyncing) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF0F1E2A), RoundedCornerShape(8.dp))
                            .border(1.dp, Color(0xFF0284C7), RoundedCornerShape(8.dp))
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = "UPLOADING: ${syncState.percent}%",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color(0xFF38BDF8)
                            )
                            if (syncState.speedMbps > 0) {
                                Text(
                                    text = String.format("%.1f MB/s", syncState.speedMbps),
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 10.sp,
                                    color = Color(0xFF10B981)
                                )
                            }
                        }
                        LinearProgressIndicator(
                            progress = { (syncState.percent / 100f).coerceIn(0f, 1f) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(6.dp)
                                .clip(RoundedCornerShape(3.dp)),
                            color = Color(0xFF38BDF8),
                            trackColor = Color(0xFF1E293B)
                        )
                    }
                }

                // Error Warning Box
                if (!syncState.isSyncing && syncState.error != null) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF450A0A), RoundedCornerShape(8.dp))
                            .border(1.dp, Color(0xFFDC2626), RoundedCornerShape(8.dp))
                            .padding(10.dp)
                    ) {
                        Text(
                            text = "SYNC PAUSED: ${syncState.error}",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = Color(0xFFFCA5A5)
                        )
                    }
                }

                // Sync Now / Retry Action Button
                Button(
                    onClick = onSyncNow,
                    enabled = !syncState.isSyncing,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (syncState.error != null) Color(0xFFDC2626) else Color(0xFF2563EB)
                    )
                ) {
                    Text(
                        text = if (syncState.isSyncing) "UPLOADING CHUNKS..." else if (syncState.error != null) "RETRY UPLOAD NOW" else "SYNC NOW / RETRY ALL CHUNKS",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold
                    )
                }

                HorizontalDivider(color = Color(0x1AFFFFFF))

                Text(
                    text = "RECENT RECORDINGS ON DEVICE:",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp,
                    color = Color(0xFF64748B)
                )

                if (segments.isEmpty()) {
                    Box(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("No recordings yet. Tap Record to start.", fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = Color(0xFF64748B))
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 240.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        items(segments) { seg ->
                            val sizeMb = if (seg.fileSizeBytes > 0) String.format("%.1f MB", seg.fileSizeBytes / (1024.0 * 1024.0)) else "-- MB"
                            val dateStr = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(seg.createdAt))

                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(Color(0xFF131A26), RoundedCornerShape(6.dp))
                                    .border(1.dp, Color(0x11FFFFFF), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 8.dp, vertical = 6.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column {
                                    Text(
                                        text = seg.filename,
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 11.sp,
                                        color = Color.White
                                    )
                                    Text(
                                        text = "$sizeMb • $dateStr",
                                        fontFamily = FontFamily.Monospace,
                                        fontSize = 9.sp,
                                        color = Color(0xFF64748B)
                                    )
                                }

                                val statusColor = when (seg.status) {
                                    "UPLOADED_TO_PC", "UPLOADED_TO_YOUTUBE", "VERIFIED_BY_LAPTOP" -> Color(0xFF10B981)
                                    "QUEUED_FOR_UPLOAD" -> Color(0xFFF59E0B)
                                    "RECORDING" -> Color(0xFFEF4444)
                                    else -> Color(0xFF94A3B8)
                                }

                                Text(
                                    text = seg.status.replace("_", " "),
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = statusColor
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("CLOSE", fontFamily = FontFamily.Monospace, color = Color(0xFF38BDF8))
            }
        }
    )
}

/**
 * Pairing & System Setup Dialog
 */
@Composable
fun PairingSetupDialog(
    pairingManager: PairingManager,
    onDismiss: () -> Unit,
    context: Context
) {
    var hostInput by remember { mutableStateOf(if (pairingManager.lanHost.isNotBlank()) pairingManager.lanHost else "192.168.0.101") }
    var pairingUriInput by remember { mutableStateOf("") }
    var feedbackMessage by remember { mutableStateOf<String?>(null) }
    var isTestingConnection by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF0F172A),
        title = {
            Text(
                text = "PAIR PHONE WITH LAPTOP",
                fontFamily = FontFamily.Monospace,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // Section 1: 1-Click Wi-Fi Connect (Easiest)
                Text(
                    text = "1. QUICK WI-FI CONNECT (ENTER PC IP):",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF38BDF8)
                )

                OutlinedTextField(
                    value = hostInput,
                    onValueChange = { hostInput = it },
                    label = { Text("PC IP Address", fontSize = 10.sp) },
                    placeholder = { Text("192.168.0.101", fontSize = 11.sp, color = Color(0xFF475569)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Button(
                    onClick = {
                        isTestingConnection = true
                        pairingManager.autoPairFromHost(hostInput) { success, msg ->
                            isTestingConnection = false
                            feedbackMessage = msg
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF10B981))
                ) {
                    Text(
                        text = if (isTestingConnection) "CONNECTING..." else "AUTO-CONNECT & SYNC KEYS",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.Black
                    )
                }

                HorizontalDivider(color = Color(0x1AFFFFFF))

                // Section 2: Paste Pairing String
                Text(
                    text = "2. OR PASTE CONNECTION STRING FROM PC:",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp,
                    color = Color(0xFF94A3B8)
                )

                OutlinedTextField(
                    value = pairingUriInput,
                    onValueChange = { pairingUriInput = it },
                    placeholder = { Text("dispatch://pair?lan=...", fontSize = 10.sp, color = Color(0xFF475569)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Button(
                    onClick = {
                        if (pairingUriInput.isNotBlank()) {
                            val ok = pairingManager.saveFromConnectionString(pairingUriInput)
                            feedbackMessage = if (ok) "Pairing string saved!" else "Invalid pairing format."
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1E293B))
                ) {
                    Text("APPLY CONNECTION STRING", fontFamily = FontFamily.Monospace, fontSize = 10.sp)
                }

                HorizontalDivider(color = Color(0x1AFFFFFF))

                // POCO C65 Battery Optimization
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
                                    Toast.makeText(context, "Battery restrictions already disabled.", Toast.LENGTH_SHORT).show()
                                }
                            }
                        } catch (e: Exception) {
                            val fallbackIntent = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                            context.startActivity(fallbackIntent)
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF0369A1))
                ) {
                    Text("DISABLE MIUI BATTERY KILLER", fontFamily = FontFamily.Monospace, fontSize = 9.sp)
                }

                if (feedbackMessage != null) {
                    Text(
                        text = feedbackMessage ?: "",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = if (feedbackMessage?.startsWith("Success") == true) Color(0xFF10B981) else Color(0xFFF59E0B)
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
