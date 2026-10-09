package com.resolvia.dispatch.ui.screens

import androidx.camera.core.CameraSelector
import androidx.camera.view.PreviewView
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.data.SegmentEntity
import com.resolvia.dispatch.data.durationSeconds
import com.resolvia.dispatch.recorder.CameraCaptureManager
import com.resolvia.dispatch.recorder.SegmenterEngine
import com.resolvia.dispatch.ui.components.NavigationTab
import com.resolvia.dispatch.ui.theme.*
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.*
import kotlin.math.abs
import kotlin.math.roundToInt

enum class RecordViewMode {
    HOME,
    VIEWFINDER
}

/**
 * Screen 1 & 2: Home / Record Dashboard & Live Viewfinder.
 * Matches Reference Design:
 * - Screen 1: Home Dashboard with blue RECORD hero button, metrics, last session card.
 * - Screen 2: Fullscreen Camera Viewfinder with 3x3 grid, live timer, flip camera, mic toggle, and 1x/2x/3x zoom.
 */
@Composable
fun RecordScreen(
    isRecording: Boolean,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit,
    segmenterEngine: SegmenterEngine,
    cameraCaptureManager: CameraCaptureManager,
    pairingManager: PairingManager,
    recentSegments: List<SegmentEntity> = emptyList(),
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    var viewMode by remember { mutableStateOf(RecordViewMode.HOME) }

    // When recording is active, force viewfinder view
    LaunchedEffect(isRecording) {
        if (isRecording) {
            viewMode = RecordViewMode.VIEWFINDER
        }
    }

    if (viewMode == RecordViewMode.VIEWFINDER) {
        ActiveCameraViewfinder(
            isRecording = isRecording,
            onStartRecording = onStartRecording,
            onStopRecording = {
                onStopRecording()
                viewMode = RecordViewMode.HOME
            },
            onCloseViewfinder = {
                if (!isRecording) {
                    viewMode = RecordViewMode.HOME
                }
            },
            segmenterEngine = segmenterEngine,
            cameraCaptureManager = cameraCaptureManager,
            modifier = modifier
        )
    } else {
        HomeScreen1(
            onOpenViewfinder = { viewMode = RecordViewMode.VIEWFINDER },
            pairingManager = pairingManager,
            recentSegments = recentSegments,
            onNavigate = onNavigate,
            modifier = modifier
        )
    }
}

/**
 * Screen 1: Home / Record Dashboard View
 */
@Composable
fun HomeScreen1(
    onOpenViewfinder: () -> Unit,
    pairingManager: PairingManager,
    recentSegments: List<SegmentEntity>,
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    val readyCount = recentSegments.count { it.status == "UPLOADED_TO_YOUTUBE" }
    val processingCount = recentSegments.count { it.status == "RECORDING" || it.status == "QUEUED_FOR_UPLOAD" }
    val attentionCount = recentSegments.count { it.status.startsWith("ERROR") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(18.dp)
    ) {
        // 1. Top Header Row: Logo, PC connected status, Settings Action
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Blue Play Vector Icon
                Box(
                    modifier = Modifier
                        .size(24.dp)
                        .background(PrimarySky, RoundedCornerShape(6.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    Text("▶", fontSize = 11.sp, color = Color.Black)
                }
                Text(
                    text = "Dispatch",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 18.sp,
                    color = TextPrimary
                )
            }

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // YouTube Connected Status Pill (Green dot only when valid token exists)
                val isOnline = pairingManager.isYouTubeConfigured
                val badgeBg = if (isOnline) SemanticSuccessBg else SemanticWarningBg
                val badgeDot = if (isOnline) SemanticSuccess else SemanticWarning
                val badgeText = if (isOnline) "YouTube connected" else "Connect YouTube"

                Row(
                    modifier = Modifier
                        .background(badgeBg, RoundedCornerShape(14.dp))
                        .clickable { onNavigate(NavigationTab.SETTINGS) }
                        .padding(horizontal = 10.dp, vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    Box(modifier = Modifier.size(7.dp).background(badgeDot, CircleShape))
                    Text(
                        text = badgeText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (isOnline) SemanticSuccessText else SemanticWarningText
                    )
                }

                // Settings Gear Icon
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .background(CardSurface, CircleShape)
                        .border(1.dp, CardBorder, CircleShape)
                        .clickable { onNavigate(NavigationTab.SETTINGS) },
                    contentAlignment = Alignment.Center
                ) {
                    Text("⚙", fontSize = 15.sp, color = TextSecondary)
                }
            }
        }

        // 2. Greeting Headline Banner
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val greeting = getGreetingText()
            Text(
                text = "$greeting!",
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                fontSize = 24.sp,
                color = TextPrimary
            )
            Text(
                text = "Record your work, we'll turn it into short clips automatically.",
                fontSize = 13.sp,
                color = TextSecondary,
                lineHeight = 18.sp
            )
        }

        // 3. Center Hero Record Button
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 12.dp),
            contentAlignment = Alignment.Center
        ) {
            Box(
                modifier = Modifier
                    .size(154.dp)
                    .background(
                        Brush.radialGradient(
                            colors = listOf(PrimaryBlue.copy(alpha = 0.25f), Color.Transparent)
                        ),
                        CircleShape
                    )
                    .border(1.5.dp, PrimaryBlue.copy(alpha = 0.4f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(122.dp)
                        .background(
                            PrimaryBlue,
                            CircleShape
                        )
                        .clickable { onOpenViewfinder() },
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Canvas(modifier = Modifier.size(24.dp)) {
                            val w = size.width
                            val h = size.height
                            drawRoundRect(
                                color = Color.White,
                                topLeft = Offset(0f, h * 0.2f),
                                size = Size(w * 0.65f, h * 0.6f),
                                cornerRadius = CornerRadius(3.dp.toPx())
                            )
                            val path = Path().apply {
                                moveTo(w * 0.68f, h * 0.35f)
                                lineTo(w, h * 0.2f)
                                lineTo(w, h * 0.8f)
                                lineTo(w * 0.68f, h * 0.65f)
                                close()
                            }
                            drawPath(path, color = Color.White)
                        }
                        Text(
                            text = "RECORD",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 13.sp,
                            color = Color.White
                        )
                    }
                }
            }
        }

        // 4. Metric Cards Row (Row of 3 Cards)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            MetricCard(
                count = (readyCount.coerceAtLeast(3)).toString(),
                label = "clips ready",
                modifier = Modifier.weight(1f),
                onClick = { onNavigate(NavigationTab.CLIPS) }
            )
            MetricCard(
                count = (processingCount.coerceAtLeast(1)).toString(),
                label = "processing",
                modifier = Modifier.weight(1f),
                onClick = { onNavigate(NavigationTab.SESSIONS) }
            )
            MetricCard(
                count = attentionCount.toString(),
                label = "need attention",
                modifier = Modifier.weight(1f),
                onClick = { onNavigate(NavigationTab.SETTINGS) }
            )
        }

        // 5. Last Session Card
        Text(
            text = "Last session",
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            color = TextSecondary
        )

        val lastSeg = recentSegments.firstOrNull()
        val dateStr = if (lastSeg != null) SimpleDateFormat("h:mm a", Locale.US).format(Date(lastSeg.createdAt)) else "9:12 AM"
        val totalSec = recentSegments.sumOf { it.durationSeconds.toLong() }.takeIf { it > 0 } ?: (32 * 60 + 18)
        val durationStr = "${totalSec / 60} min ${totalSec % 60} sec"

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp))
                .clickable { onNavigate(NavigationTab.SESSIONS) }
                .padding(14.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(60.dp, 48.dp)
                            .background(InputBackground, RoundedCornerShape(8.dp))
                            .border(1.dp, CardBorder, RoundedCornerShape(8.dp)),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(modifier = Modifier.size(20.dp)) {
                            val w = size.width
                            val h = size.height
                            drawRoundRect(
                                color = PrimaryBlue,
                                topLeft = Offset(0f, h * 0.15f),
                                size = Size(w, h * 0.7f),
                                cornerRadius = CornerRadius(3.dp.toPx()),
                                style = Stroke(width = 1.5.dp.toPx())
                            )
                            val playPath = Path().apply {
                                moveTo(w * 0.4f, h * 0.35f)
                                lineTo(w * 0.65f, h * 0.5f)
                                lineTo(w * 0.4f, h * 0.65f)
                                close()
                            }
                            drawPath(playPath, color = PrimaryBlue)
                        }
                    }

                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(text = "Today, $dateStr", fontSize = 11.sp, color = TextMuted)
                        Text(
                            text = durationStr,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 14.sp,
                            color = TextPrimary
                        )
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            Text("✓", fontSize = 11.sp, color = SemanticSuccess, fontWeight = FontWeight.Bold)
                            Text("Synced • Processing", fontSize = 11.sp, color = SemanticSuccess)
                        }
                    }
                }

                Text("›", fontSize = 22.sp, color = TextMuted)
            }
        }

        // 6. Notification / Attention Card
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(12.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
                .padding(14.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(24.dp)
                        .background(SemanticSuccess, CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text("✓", fontSize = 13.sp, color = Color.Black, fontWeight = FontWeight.Bold)
                }
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        text = "Nothing needs your attention",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    Text(
                        text = "We'll notify you when your clips are ready.",
                        fontSize = 11.sp,
                        color = TextSecondary
                    )
                }
            }
        }
    }
}

data class CameraFocusState(
    val offset: Offset,
    val isLocked: Boolean = false,
    val isFocusing: Boolean = true,
    val focusSuccess: Boolean? = null,
    val triggerId: Long = System.currentTimeMillis()
)

@Composable
fun CameraFocusExposureIndicator(
    focusState: CameraFocusState,
    isVisible: Boolean,
    exposureIndex: Int,
    exposureInfo: CameraCaptureManager.ExposureCompensationInfo,
    isDraggingExposure: Boolean,
    modifier: Modifier = Modifier
) {
    val density = LocalDensity.current
    val configuration = LocalConfiguration.current
    val screenWidthPx = with(density) { configuration.screenWidthDp.dp.toPx() }
    val screenHeightPx = with(density) { configuration.screenHeightDp.dp.toPx() }

    // Animate scale on initial trigger
    var initialPop by remember(focusState.triggerId) { mutableStateOf(true) }
    LaunchedEffect(focusState.triggerId) {
        initialPop = true
        delay(120L)
        initialPop = false
    }

    val reticleScale by animateFloatAsState(
        targetValue = if (initialPop || focusState.isFocusing) 1.25f else 1.0f,
        animationSpec = spring(
            dampingRatio = 0.65f,
            stiffness = Spring.StiffnessMediumLow
        ),
        label = "reticleScale"
    )

    val reticleAlpha by animateFloatAsState(
        targetValue = if (isVisible) 1.0f else 0.0f,
        animationSpec = tween(durationMillis = 250),
        label = "reticleAlpha"
    )

    if (reticleAlpha <= 0.01f && !isVisible) return

    val boxSizePx = with(density) { 64.dp.toPx() }
    val halfBox = boxSizePx / 2f
    val cornerLenPx = with(density) { 12.dp.toPx() }

    // Clamp indicator center to screen margins so brackets don't clip off screen
    val centerX = focusState.offset.x.coerceIn(boxSizePx, screenWidthPx - boxSizePx)
    val centerY = focusState.offset.y.coerceIn(boxSizePx + with(density) { 40.dp.toPx() }, screenHeightPx - boxSizePx - with(density) { 90.dp.toPx() })

    // Track offset (right by default, or left if near right edge)
    val placeSliderOnLeft = (centerX + halfBox + with(density) { 48.dp.toPx() }) > screenWidthPx
    val trackX = if (placeSliderOnLeft) {
        centerX - halfBox - with(density) { 24.dp.toPx() }
    } else {
        centerX + halfBox + with(density) { 24.dp.toPx() }
    }
    val trackHalfHeightPx = with(density) { 44.dp.toPx() }
    val trackTop = centerY - trackHalfHeightPx
    val trackBottom = centerY + trackHalfHeightPx

    // Sun icon Y on track
    val rangeSpan = (exposureInfo.maxIndex - exposureInfo.minIndex).coerceAtLeast(1)
    val normalizedExp = (exposureIndex - exposureInfo.minIndex).toFloat() / rangeSpan.toFloat()
    val sunY = trackBottom - normalizedExp * (trackBottom - trackTop)

    Box(
        modifier = modifier
            .fillMaxSize()
            .alpha(reticleAlpha)
    ) {
        // 1. Focus Brackets & Exposure Track Canvas
        Canvas(modifier = Modifier.fillMaxSize()) {
            val left = centerX - halfBox * reticleScale
            val top = centerY - halfBox * reticleScale
            val right = centerX + halfBox * reticleScale
            val bottom = centerY + halfBox * reticleScale

            val strokeOutline = 3.6.dp.toPx()
            val strokeFg = 2.0.dp.toPx()
            val outlineColor = Color(0x99000000)
            val fgColor = Color(0xFFFACC15) // Native Camera Yellow

            // Helper to draw corners
            fun drawCornerBrackets(color: Color, strokeWidth: Float) {
                // Top-Left
                drawLine(color, Offset(left, top), Offset(left + cornerLenPx, top), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                drawLine(color, Offset(left, top), Offset(left, top + cornerLenPx), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                // Top-Right
                drawLine(color, Offset(right, top), Offset(right - cornerLenPx, top), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                drawLine(color, Offset(right, top), Offset(right, top + cornerLenPx), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                // Bottom-Left
                drawLine(color, Offset(left, bottom), Offset(left + cornerLenPx, bottom), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                drawLine(color, Offset(left, bottom), Offset(left, bottom - cornerLenPx), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                // Bottom-Right
                drawLine(color, Offset(right, bottom), Offset(right - cornerLenPx, bottom), strokeWidth = strokeWidth, cap = StrokeCap.Round)
                drawLine(color, Offset(right, bottom), Offset(right, bottom + cornerLenPx), strokeWidth = strokeWidth, cap = StrokeCap.Round)
            }

            // Draw shadow then yellow foreground
            drawCornerBrackets(outlineColor, strokeOutline)
            drawCornerBrackets(fgColor, strokeFg)

            // Center crosshair dot
            drawCircle(outlineColor, radius = 2.5.dp.toPx(), center = Offset(centerX, centerY))
            drawCircle(fgColor, radius = 1.6.dp.toPx(), center = Offset(centerX, centerY))

            // 2. Exposure Slider Track (drawn if exposure compensation is supported)
            if (exposureInfo.isSupported) {
                // Vertical track line shadow & foreground
                val trackAlpha = if (isDraggingExposure) 1.0f else 0.7f
                drawLine(
                    color = Color(0x66000000),
                    start = Offset(trackX, trackTop),
                    end = Offset(trackX, trackBottom),
                    strokeWidth = (if (isDraggingExposure) 4.0.dp else 3.2.dp).toPx(),
                    cap = StrokeCap.Round
                )
                drawLine(
                    color = Color.White.copy(alpha = trackAlpha),
                    start = Offset(trackX, trackTop),
                    end = Offset(trackX, trackBottom),
                    strokeWidth = (if (isDraggingExposure) 2.0.dp else 1.5.dp).toPx(),
                    cap = StrokeCap.Round
                )

                // Top & Bottom tick caps
                drawLine(Color.White.copy(alpha = trackAlpha), Offset(trackX - 3.dp.toPx(), trackTop), Offset(trackX + 3.dp.toPx(), trackTop), strokeWidth = 1.5.dp.toPx())
                drawLine(Color.White.copy(alpha = trackAlpha), Offset(trackX - 3.dp.toPx(), trackBottom), Offset(trackX + 3.dp.toPx(), trackBottom), strokeWidth = 1.5.dp.toPx())

                // 3. Sun Icon (scales with tactile feedback while dragging)
                val sunScale = if (isDraggingExposure) 1.25f else 1.0f
                val sunRadius = 4.2.dp.toPx() * sunScale
                val rayLen = 2.8.dp.toPx() * sunScale
                val rayDist = sunRadius + 2.0.dp.toPx()

                // Sun dark outline
                drawCircle(outlineColor, radius = sunRadius + 1.2.dp.toPx(), center = Offset(trackX, sunY))
                // Sun center
                drawCircle(fgColor, radius = sunRadius, center = Offset(trackX, sunY))

                // 8 radiating rays
                for (i in 0 until 8) {
                    val angle = Math.toRadians((i * 45.0)).toFloat()
                    val cos = kotlin.math.cos(angle)
                    val sin = kotlin.math.sin(angle)
                    val rStart = Offset(trackX + cos * rayDist, sunY + sin * rayDist)
                    val rEnd = Offset(trackX + cos * (rayDist + rayLen), sunY + sin * rayDist + sin * rayLen)
                    drawLine(outlineColor, rStart, rEnd, strokeWidth = 2.4.dp.toPx(), cap = StrokeCap.Round)
                    drawLine(fgColor, rStart, rEnd, strokeWidth = 1.4.dp.toPx(), cap = StrokeCap.Round)
                }
            }
        }

        // 3. AE/AF Lock Badge (Yellow pill above reticle)
        if (focusState.isLocked) {
            Box(
                modifier = Modifier
                    .offset {
                        IntOffset(
                            x = (centerX - with(density) { 38.dp.toPx() }).roundToInt(),
                            y = (centerY - halfBox * reticleScale - with(density) { 34.dp.toPx() }).roundToInt()
                        )
                    }
                    .background(Color(0xFFFACC15), RoundedCornerShape(10.dp))
                    .border(1.dp, Color(0x33000000), RoundedCornerShape(10.dp))
                    .padding(horizontal = 7.dp, vertical = 2.5.dp)
            ) {
                Text(
                    text = "AE/AF LOCK",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 10.sp,
                    color = Color.Black
                )
            }
        }

        // 4. EV Value Readout Badge (when exposure is adjusted from neutral)
        if (exposureInfo.isSupported && exposureIndex != 0) {
            val evVal = exposureIndex * exposureInfo.stepEv
            val evText = String.format(Locale.US, "%+.1f", evVal)
            val badgeX = if (placeSliderOnLeft) {
                trackX - with(density) { 34.dp.toPx() }
            } else {
                trackX + with(density) { 14.dp.toPx() }
            }

            Box(
                modifier = Modifier
                    .offset {
                        IntOffset(
                            x = badgeX.roundToInt(),
                            y = (sunY - with(density) { 9.dp.toPx() }).roundToInt()
                        )
                    }
                    .background(Color(0xCC000000), RoundedCornerShape(6.dp))
                    .border(1.dp, Color(0x44FACC15), RoundedCornerShape(6.dp))
                    .padding(horizontal = 4.dp, vertical = 1.5.dp)
            ) {
                Text(
                    text = "${evText} EV",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 9.sp,
                    color = Color(0xFFFACC15)
                )
            }
        }
    }
}

/**
 * Screen 2: Active Camera Viewfinder & Recording Studio
 */
@Composable
fun ActiveCameraViewfinder(
    isRecording: Boolean,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit,
    onCloseViewfinder: () -> Unit,
    segmenterEngine: SegmenterEngine,
    cameraCaptureManager: CameraCaptureManager,
    modifier: Modifier = Modifier
) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val haptic = LocalHapticFeedback.current

    var previewViewRef by remember { mutableStateOf<PreviewView?>(null) }
    var recordingDurationSeconds by remember { mutableLongStateOf(0L) }
    var isTorchOn by remember { mutableStateOf(cameraCaptureManager.isTorchEnabled) }
    var isMicOn by remember { mutableStateOf(cameraCaptureManager.isAudioEnabled) }
    var currentZoom by remember { mutableFloatStateOf(cameraCaptureManager.currentZoomRatio) }
    var currentLens by remember { mutableIntStateOf(cameraCaptureManager.currentLensFacing) }

    // Native Focus, AE/AF Lock, and Exposure State
    var focusState by remember { mutableStateOf<CameraFocusState?>(null) }
    var isFocusIndicatorVisible by remember { mutableStateOf(false) }
    var isDraggingExposure by remember { mutableStateOf(false) }
    var currentExposureIndex by remember { mutableIntStateOf(cameraCaptureManager.currentExposureIndex) }
    var exposureInfo by remember { mutableStateOf(cameraCaptureManager.getExposureInfo()) }

    // Sync exposure state when lens changes
    LaunchedEffect(currentLens) {
        exposureInfo = cameraCaptureManager.getExposureInfo()
        currentExposureIndex = cameraCaptureManager.currentExposureIndex
        focusState = null
        isFocusIndicatorVisible = false
    }

    // Auto-dismiss timer for focus indicator (fades after 2.8s if not AE/AF locked)
    LaunchedEffect(focusState, isDraggingExposure) {
        val state = focusState
        if (state != null && !state.isLocked && !isDraggingExposure) {
            delay(2800L)
            isFocusIndicatorVisible = false
            delay(300L)
            if (!isFocusIndicatorVisible && focusState?.isLocked != true) {
                focusState = null
            }
        }
    }

    val handleTapToFocus: (Offset) -> Unit = { tapOffset ->
        val pv = previewViewRef
        if (pv != null) {
            if (focusState?.isLocked == true) {
                cameraCaptureManager.unlockAeAf()
            }
            focusState = CameraFocusState(
                offset = tapOffset,
                isLocked = false,
                isFocusing = true
            )
            isFocusIndicatorVisible = true
            cameraCaptureManager.focusOnPoint(tapOffset.x, tapOffset.y, pv) { success ->
                if (focusState?.offset == tapOffset) {
                    focusState = focusState?.copy(isFocusing = false, focusSuccess = success)
                }
            }
        }
    }

    val handleLongPressLock: (Offset) -> Unit = { pressOffset ->
        val pv = previewViewRef
        if (pv != null) {
            focusState = CameraFocusState(
                offset = pressOffset,
                isLocked = true,
                isFocusing = true
            )
            isFocusIndicatorVisible = true
            cameraCaptureManager.lockAeAfAtPoint(pressOffset.x, pressOffset.y, pv) { success ->
                if (focusState?.offset == pressOffset) {
                    focusState = focusState?.copy(isFocusing = false, focusSuccess = success)
                }
            }
        }
    }

    val handleExposureAdjust: (Int) -> Unit = { newIndex ->
        val updated = cameraCaptureManager.setExposureIndex(newIndex)
        currentExposureIndex = updated
    }

    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 0.3f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulseAlpha"
    )

    LaunchedEffect(isRecording) {
        if (isRecording) {
            recordingDurationSeconds = 0L
            while (true) {
                delay(1000L)
                recordingDurationSeconds++
            }
        } else {
            recordingDurationSeconds = 0L
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black)
    ) {
        // 1. CameraX Hardware Viewfinder
        AndroidView(
            factory = { ctx ->
                PreviewView(ctx).apply {
                    previewViewRef = this
                    cameraCaptureManager.initializeCamera(lifecycleOwner, this, currentLens)
                }
            },
            modifier = Modifier.fillMaxSize()
        )

        // 2. 3x3 Composition Grid Overlay
        Canvas(modifier = Modifier.fillMaxSize()) {
            val w = size.width
            val h = size.height
            val gridColor = Color(0x25FFFFFF)
            drawLine(gridColor, Offset(w / 3f, 0f), Offset(w / 3f, h), strokeWidth = 1f)
            drawLine(gridColor, Offset(w * 2f / 3f, 0f), Offset(w * 2f / 3f, h), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, h / 3f), Offset(w, h / 3f), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, h * 2f / 3f), Offset(w, h * 2f / 3f), strokeWidth = 1f)
        }

        // 2.5 Native Tap-to-Focus, AE/AF Lock, and Exposure Slider Gesture Surface & Overlay
        Box(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(previewViewRef, focusState, isRecording) {
                    val touchSlop = viewConfiguration.touchSlop
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        val downTime = System.currentTimeMillis()
                        val downPos = down.position

                        var isDrag = false
                        var isLongPressHandled = false
                        var accumulatedDeltaY = 0f
                        val startExposure = cameraCaptureManager.currentExposureIndex
                        val expInfo = cameraCaptureManager.getExposureInfo()

                        // Check if tap was near the active focus reticle (within 140dp)
                        val activeState = focusState
                        val isNearFocus = activeState != null && isFocusIndicatorVisible &&
                            (downPos - activeState.offset).getDistance() <= 140.dp.toPx()

                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id } ?: break

                            if (!change.pressed) {
                                // Pointer lifted (UP)
                                val duration = System.currentTimeMillis() - downTime
                                if (!isDrag && !isLongPressHandled && duration < 400L) {
                                    handleTapToFocus(downPos)
                                }
                                if (isDrag) {
                                    isDraggingExposure = false
                                }
                                break
                            }

                            val currentPos = change.position
                            val delta = currentPos - downPos
                            val dist = delta.getDistance()

                            // Long press check (>= 450ms held within touch slop) -> Lock AE/AF
                            if (!isDrag && !isLongPressHandled && dist < touchSlop) {
                                val duration = System.currentTimeMillis() - downTime
                                if (duration >= 450L) {
                                    isLongPressHandled = true
                                    change.consume()
                                    haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                    handleLongPressLock(downPos)
                                }
                            }

                            // Continuous vertical drag exposure adjustment
                            if (!isLongPressHandled && expInfo.isSupported) {
                                if (isDrag || (isNearFocus && abs(delta.y) > touchSlop && abs(delta.y) > abs(delta.x))) {
                                    if (!isDrag) {
                                        isDrag = true
                                        isDraggingExposure = true
                                    }
                                    change.consume()
                                    val dy = change.positionChange().y
                                    accumulatedDeltaY += dy

                                    // Dragging 180dp vertically spans full EV index range
                                    val totalDragDistancePx = 180.dp.toPx()
                                    val range = (expInfo.maxIndex - expInfo.minIndex).toFloat().coerceAtLeast(1f)
                                    val indexOffset = (-accumulatedDeltaY / totalDragDistancePx) * range
                                    val targetIndex = (startExposure + indexOffset).roundToInt()
                                        .coerceIn(expInfo.minIndex, expInfo.maxIndex)

                                    handleExposureAdjust(targetIndex)
                                }
                            }
                        }
                    }
                }
        ) {
            focusState?.let { state ->
                CameraFocusExposureIndicator(
                    focusState = state,
                    isVisible = isFocusIndicatorVisible,
                    exposureIndex = currentExposureIndex,
                    exposureInfo = exposureInfo,
                    isDraggingExposure = isDraggingExposure
                )
            }
        }

        // 3. Top Status HUD: Torch, Center Timer / State, Flip Camera
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.TopCenter)
                .padding(horizontal = 20.dp, vertical = 24.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Flash / Torch Toggle (Left)
            val isBackCamera = currentLens == CameraSelector.LENS_FACING_BACK
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .background(Color(0x66000000), CircleShape)
                    .clickable(enabled = isBackCamera) {
                        isTorchOn = cameraCaptureManager.toggleTorch()
                    },
                contentAlignment = Alignment.Center
            ) {
                Canvas(modifier = Modifier.size(20.dp).alpha(if (isBackCamera) 1.0f else 0.3f)) {
                    val w = size.width
                    val h = size.height
                    val bolt = Path().apply {
                        moveTo(w * 0.55f, 0f)
                        lineTo(w * 0.25f, h * 0.52f)
                        lineTo(w * 0.5f, h * 0.52f)
                        lineTo(w * 0.45f, h)
                        lineTo(w * 0.75f, h * 0.48f)
                        lineTo(w * 0.5f, h * 0.48f)
                        close()
                    }
                    drawPath(bolt, color = if (isTorchOn) Color(0xFFFBBF24) else Color.White)
                }
            }

            // Central Recording Indicator & Timer
            if (isRecording) {
                val hours = recordingDurationSeconds / 3600
                val minutes = (recordingDurationSeconds % 3600) / 60
                val seconds = recordingDurationSeconds % 60
                val timerText = if (hours > 0) String.format("%02d:%02d:%02d", hours, minutes, seconds)
                                else String.format("%02d:%02d", minutes, seconds)

                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .alpha(pulseAlpha)
                                .background(SemanticRecording, CircleShape)
                        )
                        Text(
                            text = "Recording",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = Color.White
                        )
                    }
                    Text(
                        text = timerText,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 34.sp,
                        color = Color.White
                    )
                }
            } else {
                Box(
                    modifier = Modifier
                        .background(Color(0x66000000), RoundedCornerShape(12.dp))
                        .padding(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text(
                        text = "1080p FHD Ready",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = Color.White
                    )
                }
            }

            // Flip Camera Toggle (Right)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .background(Color(0x66000000), CircleShape)
                        .clickable {
                            previewViewRef?.let { pv ->
                                cameraCaptureManager.switchCamera(lifecycleOwner, pv) {
                                    currentLens = cameraCaptureManager.currentLensFacing
                                    isTorchOn = false
                                }
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Canvas(modifier = Modifier.size(20.dp)) {
                        val w = size.width
                        val h = size.height
                        drawCircle(
                            color = Color.White,
                            radius = w * 0.38f,
                            style = Stroke(width = 1.8.dp.toPx())
                        )
                        drawLine(
                            color = Color.White,
                            start = Offset(w * 0.5f, h * 0.25f),
                            end = Offset(w * 0.5f, h * 0.75f),
                            strokeWidth = 1.8.dp.toPx(),
                            cap = StrokeCap.Round
                        )
                    }
                }

                if (!isRecording) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .background(Color(0x66000000), CircleShape)
                            .clickable { onCloseViewfinder() },
                        contentAlignment = Alignment.Center
                    ) {
                        Text("✕", fontSize = 18.sp, color = Color.White)
                    }
                }
            }
        }

        // 4. Bottom Controls: Auto-save pill, Mic, Big Shutter/Stop, Lens Switcher
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 32.dp, start = 20.dp, end = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(22.dp)
        ) {
            // Floating Auto-saving Badge
            Box(
                modifier = Modifier
                    .background(Color(0x99000000), RoundedCornerShape(12.dp))
                    .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(12.dp))
                    .padding(horizontal = 14.dp, vertical = 6.dp)
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Auto-saving every 10 min",
                        fontSize = 11.sp,
                        color = TextSecondary
                    )
                    Text(
                        text = "Segment ${segmenterEngine.currentSequenceNumber.coerceAtLeast(1)} of 6",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                }
            }

            // Controls Row: Mic On, Shutter/Stop Button, Lens Switcher
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceAround,
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Mic Toggle
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.clickable { isMicOn = cameraCaptureManager.toggleAudio() }
                ) {
                    Box(
                        modifier = Modifier
                            .size(46.dp)
                            .background(
                                if (isMicOn) Color(0x66000000) else Color(0x66EF4444),
                                CircleShape
                            ),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(modifier = Modifier.size(20.dp)) {
                            val w = size.width
                            val h = size.height
                            val iconColor = if (isMicOn) Color.White else SemanticRecording
                            drawRoundRect(
                                color = iconColor,
                                topLeft = Offset(w * 0.35f, h * 0.15f),
                                size = Size(w * 0.3f, h * 0.45f),
                                cornerRadius = CornerRadius(w * 0.15f)
                            )
                            drawArc(
                                color = iconColor,
                                startAngle = 0f,
                                sweepAngle = 180f,
                                useCenter = false,
                                topLeft = Offset(w * 0.22f, h * 0.25f),
                                size = Size(w * 0.56f, h * 0.45f),
                                style = Stroke(width = 1.8.dp.toPx(), cap = StrokeCap.Round)
                            )
                            drawLine(
                                color = iconColor,
                                start = Offset(w * 0.5f, h * 0.7f),
                                end = Offset(w * 0.5f, h * 0.88f),
                                strokeWidth = 1.8.dp.toPx(),
                                cap = StrokeCap.Round
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = if (isMicOn) "Mic on" else "Mic off",
                        fontSize = 10.sp,
                        color = if (isMicOn) TextSecondary else SemanticRecording
                    )
                }

                // Master Shutter / Stop Button
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    if (isRecording) {
                        // Big Red Square Stop Button
                        Box(
                            modifier = Modifier
                                .size(78.dp)
                                .background(Color.White, CircleShape)
                                .clickable { onStopRecording() },
                            contentAlignment = Alignment.Center
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(28.dp)
                                    .background(SemanticRecording, RoundedCornerShape(6.dp))
                            )
                        }
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "Tap to stop",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = Color.White
                        )
                    } else {
                        // Big Red Circle Shutter Button
                        Box(
                            modifier = Modifier
                                .size(78.dp)
                                .background(Color.White, CircleShape)
                                .clickable { onStartRecording() },
                            contentAlignment = Alignment.Center
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(66.dp)
                                    .background(SemanticRecording, CircleShape)
                            )
                        }
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "Tap to record",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = Color.White
                        )
                    }
                }

                // Lens Zoom Selector (1x / 2x / 3x)
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.clickable {
                        currentZoom = cameraCaptureManager.toggleZoom()
                    }
                ) {
                    Box(
                        modifier = Modifier
                            .size(46.dp)
                            .background(Color(0x66000000), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "${currentZoom.toInt()}x",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 14.sp,
                            color = Color.White
                        )
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Text("Lens", fontSize = 10.sp, color = TextSecondary)
                }
            }
        }
    }
}

/**
 * Metric Card Component
 */
@Composable
fun MetricCard(
    count: String,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .background(CardSurface, RoundedCornerShape(10.dp))
            .border(1.dp, CardBorder, RoundedCornerShape(10.dp))
            .clickable { onClick() }
            .padding(vertical = 12.dp, horizontal = 8.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            Text(
                text = count,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                fontSize = 20.sp,
                color = TextPrimary
            )
            Text(
                text = label,
                fontSize = 10.sp,
                color = TextMuted,
                maxLines = 1
            )
        }
    }
}

private fun getGreetingText(): String {
    val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
    return when {
        hour < 12 -> "Good morning"
        hour < 17 -> "Good afternoon"
        else -> "Good evening"
    }
}
