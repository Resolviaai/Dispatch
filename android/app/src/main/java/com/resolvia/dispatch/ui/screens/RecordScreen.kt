package com.resolvia.dispatch.ui.screens

import android.app.Activity
import androidx.camera.core.CameraSelector
import androidx.camera.view.PreviewView
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
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

@Composable
fun RecordScreen(
    isRecording: Boolean,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit,
    segmenterEngine: SegmenterEngine,
    cameraCaptureManager: CameraCaptureManager,
    pairingManager: PairingManager,
    recentSegments: List<SegmentEntity>,
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    if (isRecording) {
        ActiveRecordingView(
            segmenterEngine = segmenterEngine,
            cameraCaptureManager = cameraCaptureManager,
            onStopRecording = onStopRecording
        )
    } else {
        HomeRecordView(
            onStartRecording = onStartRecording,
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
fun HomeRecordView(
    onStartRecording: () -> Unit,
    pairingManager: PairingManager,
    recentSegments: List<SegmentEntity>,
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(18.dp)
    ) {
        // 1. Top Header Row (Logo, PC Status Pill, Settings Action)
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
                // YouTube account status. The phone does not connect to the PC.
                val isOnline = pairingManager.isYouTubeConfigured
                val badgeBg = if (isOnline) SemanticSuccessBg else SemanticWarningBg
                val badgeDot = if (isOnline) SemanticSuccess else SemanticWarning
                val badgeText = if (isOnline) "YouTube ready" else "Set up YouTube"

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

                // Settings Gear Icon Box
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
                    .size(150.dp)
                    .background(PrimaryBlue.copy(alpha = 0.18f), CircleShape)
                    .border(2.dp, PrimarySky.copy(alpha = 0.35f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(120.dp)
                        .background(PrimarySky, CircleShape)
                        .clickable { onStartRecording() },
                    contentAlignment = Alignment.Center
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        Text("📹", fontSize = 28.sp)
                        Text(
                            text = "RECORD",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 13.sp,
                            color = Color.Black
                        )
                    }
                }
            }
        }

        // 4. Metric Cards Row (3 Cards: Ready, Processing, Attention)
        val readyCount = recentSegments.count { it.status == "UPLOADED_TO_YOUTUBE" }
        val processingCount = recentSegments.count { it.status == "RECORDING" || it.status == "QUEUED_FOR_UPLOAD" }
        val attentionCount = recentSegments.count { it.status.startsWith("ERROR") }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            MetricCard(
                count = readyCount.toString(),
                label = "uploaded to YouTube",
                modifier = Modifier.weight(1f),
                onClick = { onNavigate(NavigationTab.CLIPS) }
            )
            MetricCard(
                count = processingCount.toString(),
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
        if (lastSeg != null) {
            val dateStr = SimpleDateFormat("h:mm a", Locale.US).format(Date(lastSeg.createdAt))
            val durationText = "${lastSeg.durationSeconds.toInt() / 60} min ${lastSeg.durationSeconds.toInt() % 60} sec"

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(CardSurface, RoundedCornerShape(12.dp))
                    .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
                    .clickable { onNavigate(NavigationTab.SESSIONS) }
                    .padding(12.dp)
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
                        // Thumbnail Placeholder
                        Box(
                            modifier = Modifier
                                .size(54.dp, 44.dp)
                                .background(InputBackground, RoundedCornerShape(6.dp))
                                .border(1.dp, CardBorder, RoundedCornerShape(6.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Text("🎬", fontSize = 16.sp)
                        }

                        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(
                                text = "Today, $dateStr",
                                fontSize = 11.sp,
                                color = TextMuted
                            )
                            Text(
                                text = durationText,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Text("✓", fontSize = 10.sp, color = SemanticSuccess)
                                Text(
                                    text = if (lastSeg.status == "UPLOADED_TO_YOUTUBE") "Uploaded to YouTube" else "Waiting for YouTube upload",
                                    fontSize = 10.sp,
                                    color = SemanticSuccessText
                                )
                            }
                        }
                    }

                    Text("›", fontSize = 20.sp, color = TextMuted)
                }
            }
        } else {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(CardSurface, RoundedCornerShape(12.dp))
                    .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
                    .padding(16.dp),
                contentAlignment = Alignment.Center
            ) {
                Text("No previous sessions recorded yet.", fontSize = 12.sp, color = TextMuted)
            }
        }

        // 6. Attention / Notification Banner
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(SemanticSuccessBg.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
                .border(1.dp, SemanticSuccess.copy(alpha = 0.3f), RoundedCornerShape(12.dp))
                .padding(14.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Box(
                    modifier = Modifier.size(24.dp).background(SemanticSuccess, CircleShape),
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

/**
 * Metric Card Component (Ready, Processing, Attention)
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

/**
 * Screen 2: Active Recording Fullscreen Pro Viewfinder View
 */
@Composable
fun ActiveRecordingView(
    segmenterEngine: SegmenterEngine,
    cameraCaptureManager: CameraCaptureManager,
    onStopRecording: () -> Unit
) {
    val context = LocalContext.current
    var recordingDurationSeconds by remember { mutableLongStateOf(0L) }
    var isTorchOn by remember { mutableStateOf(false) }
    var currentZoom by remember { mutableFloatStateOf(1.0f) }
    var currentLens by remember { mutableStateOf(CameraSelector.LENS_FACING_BACK) }

    LaunchedEffect(Unit) {
        while (true) {
            delay(1000L)
            recordingDurationSeconds++
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
    ) {
        // Camera Hardware Viewfinder
        val lifecycleOwner = LocalLifecycleOwner.current
        AndroidView(
            factory = { ctx ->
                PreviewView(ctx).apply {
                    cameraCaptureManager.initializeCamera(lifecycleOwner, this)
                }
            },
            modifier = Modifier.fillMaxSize()
        )

        // 3x3 Grid Overlay
        Canvas(modifier = Modifier.fillMaxSize()) {
            val w = size.width
            val h = size.height
            val gridColor = Color(0x33FFFFFF)
            drawLine(gridColor, Offset(w / 3f, 0f), Offset(w / 3f, h), strokeWidth = 1f)
            drawLine(gridColor, Offset(w * 2f / 3f, 0f), Offset(w * 2f / 3f, h), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, h / 3f), Offset(w, h / 3f), strokeWidth = 1f)
            drawLine(gridColor, Offset(0f, h * 2f / 3f), Offset(w, h * 2f / 3f), strokeWidth = 1f)
        }

        // Top Status HUD (Torch, Red Dot Recording + Timer, Camera Flip)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.TopCenter)
                .padding(horizontal = 20.dp, vertical = 24.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Flash Toggle
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .background(Color(0x66000000), CircleShape)
                    .clickable { isTorchOn = cameraCaptureManager.toggleTorch() },
                contentAlignment = Alignment.Center
            ) {
                Text(if (isTorchOn) "⚡" else "💡", fontSize = 16.sp)
            }

            // Central Timer
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
                    Box(modifier = Modifier.size(8.dp).background(SemanticRecording, CircleShape))
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
                    fontSize = 32.sp,
                    color = Color.White
                )
            }

            // Flip Camera Toggle
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .background(Color(0x66000000), CircleShape)
                    .clickable {
                        (context as? Activity)?.let { act ->
                            // Simple flip trigger
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                Text("🔄", fontSize = 16.sp)
            }
        }

        // Bottom Section: Floating Segment Notice + Controls
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 36.dp, start = 20.dp, end = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(24.dp)
        ) {
            // Floating Auto-Saving Segment Badge
            Box(
                modifier = Modifier
                    .background(Color(0x99000000), RoundedCornerShape(12.dp))
                    .border(1.dp, Color(0x33FFFFFF), RoundedCornerShape(12.dp))
                    .padding(horizontal = 14.dp, vertical = 6.dp)
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "⏱ Auto-saving every 10 min",
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

            // Bottom Actions: Mic On, Master Stop Button, Lens Switcher
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceAround,
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Mic On Icon
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .background(Color(0x66000000), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("🎤", fontSize = 16.sp)
                    }
                    Text("Mic on", fontSize = 10.sp, color = TextSecondary)
                }

                // Big Red Square Stop Button
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(
                        modifier = Modifier
                            .size(76.dp)
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
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Tap to stop",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = Color.White
                    )
                }

                // 1x Lens Selector
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .background(Color(0x66000000), CircleShape)
                            .clickable {
                                currentZoom = cameraCaptureManager.toggleZoom()
                            },
                        contentAlignment = Alignment.Center
                    ) {
                        Text("${currentZoom.toInt()}x", fontFamily = FontFamily.Monospace, fontSize = 13.sp, color = Color.White)
                    }
                    Text("Lens", fontSize = 10.sp, color = TextSecondary)
                }
            }
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
