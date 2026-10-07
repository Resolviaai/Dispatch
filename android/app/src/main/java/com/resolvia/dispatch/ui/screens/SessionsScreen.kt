package com.resolvia.dispatch.ui.screens

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
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.resolvia.dispatch.data.SegmentEntity
import com.resolvia.dispatch.data.durationSeconds
import com.resolvia.dispatch.ui.components.NavigationTab
import com.resolvia.dispatch.ui.theme.*
import java.text.SimpleDateFormat
import java.util.*

@Composable
fun SessionsScreen(
    recentSegments: List<SegmentEntity>,
    onSyncNow: () -> Unit,
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    val isRecording = recentSegments.any { it.status == "RECORDING" }
    val totalBytes = recentSegments.sumOf { it.fileSizeBytes }
    val pendingCount = recentSegments.count { it.status == "QUEUED_FOR_UPLOAD" }
    val uploadedCount = recentSegments.count { it.status == "UPLOADED_TO_YOUTUBE" }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // 1. Top Saved / Processing Banner
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.padding(top = 4.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .background(SemanticSuccess, CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Text("✓", fontSize = 18.sp, color = Color.Black, fontWeight = FontWeight.Bold)
            }
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = if (isRecording) "Recording" else "Upload status",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 18.sp,
                    color = TextPrimary
                )
                Text(
                    text = "Phone uploads go to YouTube. Dispatch PC processes the upload independently.",
                    fontSize = 12.sp,
                    color = TextSecondary
                )
            }
        }

        // 2. Session Summary Card
        val lastSeg = recentSegments.firstOrNull()
        val dateStr = if (lastSeg != null) SimpleDateFormat("h:mm a", Locale.US).format(Date(lastSeg.createdAt)) else "9:12 AM"
        val totalSec = recentSegments.sumOf { it.durationSeconds.toLong() }
        val durationStr = "${totalSec / 60} min ${totalSec % 60} sec"
        val segmentCountStr = "${recentSegments.size.coerceAtLeast(1)} segments"

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp))
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
                        Text("🎬", fontSize = 20.sp)
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
                        Text(text = segmentCountStr, fontSize = 11.sp, color = TextSecondary)
                    }
                }

                Text("›", fontSize = 22.sp, color = TextMuted)
            }
        }

        // 3. Circular Uploading Progress Card (Screen 3)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp))
                .padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                // Circular Progress Indicator
                val percent = if (pendingCount == 0 && uploadedCount > 0) 100 else 0
                Box(
                    modifier = Modifier.size(76.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Canvas(modifier = Modifier.fillMaxSize()) {
                        val strokeWidth = 7.dp.toPx()
                        drawArc(
                            color = CardBorder,
                            startAngle = -90f,
                            sweepAngle = 360f,
                            useCenter = false,
                            style = Stroke(width = strokeWidth)
                        )
                        drawArc(
                            color = PrimarySky,
                            startAngle = -90f,
                            sweepAngle = (percent / 100f) * 360f,
                            useCenter = false,
                            style = Stroke(width = strokeWidth, cap = StrokeCap.Round)
                        )
                    }
                    Text(
                        text = "$percent%",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                        color = TextPrimary
                    )
                }

                // Transfer Details
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(3.dp)
                ) {
                    Text(
                        text = when {
                            pendingCount > 0 -> "Waiting to upload to YouTube"
                            uploadedCount > 0 -> "Uploaded to YouTube"
                            else -> "No uploads yet"
                        },
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                        color = TextPrimary
                    )
                    Text(
                        text = "Cloud inbox: YouTube",
                        fontSize = 11.sp,
                        color = TextSecondary
                    )

                    val totalMb = (totalBytes / (1024.0 * 1024.0)).toInt().coerceAtLeast(1)
                    Text(
                        text = "$uploadedCount uploaded • $pendingCount queued • $totalMb MB on phone",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = TextMuted
                    )

                }
            }
        }

        // Action Trigger Button
        Button(
            onClick = onSyncNow,
            enabled = pendingCount > 0,
            modifier = Modifier.fillMaxWidth().height(46.dp),
            shape = RoundedCornerShape(10.dp),
            colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue)
        ) {
            Text(
                text = "RETRY YOUTUBE UPLOADS",
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                fontSize = 12.sp,
                color = Color.White
            )
        }

        // 4. Background Wi-Fi Notice Card
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
                Text("☁️", fontSize = 20.sp)
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        text = "Background YouTube upload",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    Text(
                        text = "Uploads continue while Dispatch is in the background.",
                        fontSize = 11.sp,
                        color = TextSecondary
                    )
                }
            }
        }

        // 5. Horizontal Pipeline Breadcrumbs (Saved -> Uploading -> Processing -> Clips ready)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(12.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
                .padding(vertical = 12.dp, horizontal = 14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            PipelineStepIcon("✓", "Saved", isCompleted = true, isActive = false)
            Text("—", color = CardBorder)
            PipelineStepIcon("↑", "YouTube", isCompleted = uploadedCount > 0, isActive = pendingCount > 0)
            Text("—", color = CardBorder)
            PipelineStepIcon("✨", "PC processing", isCompleted = false, isActive = false)
            Text("—", color = CardBorder)
            PipelineStepIcon("▶", "Clips ready", isCompleted = false, isActive = false)
        }

        // 6. Vertical Processing Timeline (Screen 4)
        Text(
            text = "Processing stages",
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            color = TextSecondary
        )

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp))
                .padding(16.dp)
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                TimelineStageItem(
                    title = "Transcript complete",
                    subtitle = "Finished a few moments ago",
                    state = StageState.COMPLETED
                )
                TimelineStageItem(
                    title = "Finding highlights",
                    subtitle = "AI locating best hooks and points...",
                    state = StageState.IN_PROGRESS
                )
                TimelineStageItem(
                    title = "Rendering clips",
                    subtitle = "FFmpeg 9:16 vertical render queued",
                    state = StageState.QUEUED
                )
                TimelineStageItem(
                    title = "Finalizing",
                    subtitle = "Preparing clips for review",
                    state = StageState.QUEUED
                )
            }
        }
    }
}

enum class StageState { COMPLETED, IN_PROGRESS, QUEUED }

@Composable
fun TimelineStageItem(
    title: String,
    subtitle: String,
    state: StageState
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        val (iconText, iconBg, iconColor) = when (state) {
            StageState.COMPLETED -> Triple("✓", SemanticSuccess, Color.Black)
            StageState.IN_PROGRESS -> Triple("●", PrimarySky, Color.Black)
            StageState.QUEUED -> Triple("○", CardBorder, TextMuted)
        }

        Box(
            modifier = Modifier
                .size(22.dp)
                .background(iconBg, CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Text(iconText, fontSize = 11.sp, color = iconColor, fontWeight = FontWeight.Bold)
        }

        Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {
            Text(
                text = title,
                fontFamily = FontFamily.Monospace,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = if (state == StageState.QUEUED) TextMuted else TextPrimary
            )
            Text(
                text = subtitle,
                fontSize = 10.sp,
                color = TextSecondary
            )
        }
    }
}

@Composable
fun PipelineStepIcon(
    symbol: String,
    label: String,
    isCompleted: Boolean,
    isActive: Boolean
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        val bg = when {
            isCompleted -> SemanticSuccess
            isActive -> PrimarySky
            else -> CardBorder
        }
        val textColor = if (isCompleted || isActive) Color.Black else TextMuted

        Box(
            modifier = Modifier.size(24.dp).background(bg, CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Text(symbol, fontSize = 11.sp, color = textColor, fontWeight = FontWeight.Bold)
        }
        Text(label, fontSize = 9.sp, color = if (isActive || isCompleted) TextPrimary else TextMuted)
    }
}
