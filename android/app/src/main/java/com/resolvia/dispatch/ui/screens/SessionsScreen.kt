package com.resolvia.dispatch.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
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

/**
 * Screen: Sessions & Per-Segment Upload State.
 * Reads 100% real data from Room Database (segments, outbox).
 * Zero hardcoded durations, zero mock file sizes, zero fake processing timelines.
 */
@Composable
fun SessionsScreen(
    recentSegments: List<SegmentEntity>,
    onSyncNow: () -> Unit,
    onNavigate: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    val totalBytes = recentSegments.sumOf { it.fileSizeBytes }
    val uploadedCount = recentSegments.count { it.status == "UPLOADED_TO_YOUTUBE" }
    val queuedCount = recentSegments.count { it.status == "QUEUED_FOR_UPLOAD" || it.status == "WAITING_FOR_YOUTUBE" }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        // 1. Header Row
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "Sessions & Uploads",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 18.sp,
                    color = TextPrimary
                )
                Text(
                    text = "${recentSegments.size} total recorded segments",
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    color = TextSecondary
                )
            }

            if (queuedCount > 0) {
                Button(
                    onClick = onSyncNow,
                    colors = ButtonDefaults.buttonColors(containerColor = PrimarySky),
                    shape = RoundedCornerShape(8.dp),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text("Upload Now", fontSize = 11.sp, color = Color.Black, fontWeight = FontWeight.Bold)
                }
            }
        }

        // 2. Real Metrics Overview Card
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(12.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
                .padding(14.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceAround,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "$uploadedCount",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = SemanticSuccess
                    )
                    Text("Uploaded", fontSize = 10.sp, color = TextMuted)
                }
                Box(modifier = Modifier.width(1.dp).height(24.dp).background(CardBorder))
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "$queuedCount",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = if (queuedCount > 0) SemanticWarning else TextSecondary
                    )
                    Text("Queued", fontSize = 10.sp, color = TextMuted)
                }
                Box(modifier = Modifier.width(1.dp).height(24.dp).background(CardBorder))
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    val mb = String.format(Locale.US, "%.1f MB", totalBytes / (1024.0 * 1024.0))
                    Text(
                        text = mb,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp,
                        color = TextPrimary
                    )
                    Text("Storage", fontSize = 10.sp, color = TextMuted)
                }
            }
        }

        // 3. Segment List or Honest Empty State
        if (recentSegments.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "No recordings yet",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                        color = TextSecondary
                    )
                    Text(
                        text = "Record video on the Record tab to start rolling 1080p segments.",
                        fontSize = 11.sp,
                        color = TextMuted
                    )
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxWidth().weight(1f),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(recentSegments, key = { it.segmentId }) { seg ->
                    SegmentRowItem(segment = seg, onNavigate = onNavigate)
                }
            }
        }
    }
}

@Composable
fun SegmentRowItem(
    segment: SegmentEntity,
    onNavigate: (NavigationTab) -> Unit
) {
    val dateStr = SimpleDateFormat("MMM d, h:mm a", Locale.US).format(Date(segment.createdAt))
    val durationSec = segment.durationSeconds
    val durStr = String.format(Locale.US, "%02d:%02d", durationSec / 60, durationSec % 60)
    val mbStr = String.format(Locale.US, "%.1f MB", segment.fileSizeBytes / (1024.0 * 1024.0))

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(CardSurface, RoundedCornerShape(10.dp))
            .border(1.dp, CardBorder, RoundedCornerShape(10.dp))
            .padding(12.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(3.dp), modifier = Modifier.weight(1f)) {
                Text(
                    text = segment.filename,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 12.sp,
                    color = TextPrimary
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(text = dateStr, fontSize = 10.sp, color = TextMuted)
                    Text(text = "•", fontSize = 10.sp, color = TextMuted)
                    Text(text = durStr, fontSize = 10.sp, color = TextSecondary, fontFamily = FontFamily.Monospace)
                    Text(text = "•", fontSize = 10.sp, color = TextMuted)
                    Text(text = mbStr, fontSize = 10.sp, color = TextSecondary, fontFamily = FontFamily.Monospace)
                }
            }

            // Real status pill
            when (segment.status) {
                "UPLOADED_TO_YOUTUBE" -> {
                    Text(
                        text = "Uploaded",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = SemanticSuccess,
                        modifier = Modifier
                            .background(SemanticSuccessBg, RoundedCornerShape(10.dp))
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
                "UPLOADING" -> {
                    Text(
                        text = "Uploading...",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = PrimarySky,
                        modifier = Modifier
                            .background(PrimarySky.copy(alpha = 0.15f), RoundedCornerShape(10.dp))
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
                "WAITING_FOR_YOUTUBE" -> {
                    Text(
                        text = "Connect YouTube",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = SemanticWarning,
                        modifier = Modifier
                            .background(SemanticWarningBg, RoundedCornerShape(10.dp))
                            .clickable { onNavigate(NavigationTab.SETTINGS) }
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
                "QUEUED_FOR_UPLOAD" -> {
                    Text(
                        text = "Queued",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = SemanticWarning,
                        modifier = Modifier
                            .background(SemanticWarningBg, RoundedCornerShape(10.dp))
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
                "RECORDING" -> {
                    Text(
                        text = "Recording",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = SemanticRecording,
                        modifier = Modifier
                            .background(SemanticRecordingBg, RoundedCornerShape(10.dp))
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
                else -> {
                    Text(
                        text = "Failed",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = SemanticRecording,
                        modifier = Modifier
                            .background(SemanticRecordingBg, RoundedCornerShape(10.dp))
                            .padding(horizontal = 8.dp, vertical = 4.dp)
                    )
                }
            }
        }
    }
}
