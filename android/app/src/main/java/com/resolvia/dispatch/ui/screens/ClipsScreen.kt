package com.resolvia.dispatch.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.ui.theme.CanvasBackground
import com.resolvia.dispatch.ui.theme.CardBorder
import com.resolvia.dispatch.ui.theme.CardSurface
import com.resolvia.dispatch.ui.theme.InputBackground
import com.resolvia.dispatch.ui.theme.PrimarySky
import com.resolvia.dispatch.ui.theme.TextMuted
import com.resolvia.dispatch.ui.theme.TextPrimary
import com.resolvia.dispatch.ui.theme.TextSecondary

/**
 * Phone-side Clips tab.
 *
 * Dispatch intentionally keeps clip review and subtitle editing on the PC
 * dashboard. The phone has no PC->phone transport channel, so it must not
 * fabricate processing or clip-review state.
 */
data class RemoteClipItem(
    val id: String,
    val title: String,
    val durationSeconds: Double = 0.0,
    val status: String = "READY",
    val videoUrl: String? = null
)

@Composable
fun ClipsScreen(
    pairingManager: PairingManager,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .navigationBarsPadding()
            .padding(horizontal = 20.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(16.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(16.dp))
                .padding(20.dp),
            contentAlignment = Alignment.Center
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(56.dp)
                        .background(PrimarySky.copy(alpha = 0.12f), RoundedCornerShape(14.dp))
                        .border(1.dp, PrimarySky.copy(alpha = 0.30f), RoundedCornerShape(14.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "WEB",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 12.sp,
                        color = PrimarySky
                    )
                }

                Text(
                    text = "Review clips on the PC dashboard",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = TextPrimary,
                    textAlign = TextAlign.Center
                )

                Text(
                    text = "The phone records and uploads raw segments to YouTube. The PC handles transcription, AI highlights, subtitles, rendering, review, and publishing.",
                    fontSize = 14.sp,
                    color = TextSecondary,
                    lineHeight = 20.sp,
                    textAlign = TextAlign.Center
                )

                Spacer(modifier = Modifier.height(4.dp))

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(InputBackground, RoundedCornerShape(10.dp))
                        .border(1.dp, CardBorder, RoundedCornerShape(10.dp))
                        .padding(12.dp)
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(
                            text = "Phone responsibilities",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = TextPrimary
                        )
                        Text(
                            text = "Record → save → upload to YouTube → report upload status",
                            fontSize = 12.sp,
                            color = TextSecondary,
                            lineHeight = 18.sp
                        )
                        Text(
                            text = "Clip review is intentionally not shown here.",
                            fontSize = 10.sp,
                            color = TextMuted
                        )
                    }
                }

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(InputBackground, RoundedCornerShape(10.dp))
                        .border(1.dp, CardBorder, RoundedCornerShape(10.dp))
                        .padding(12.dp)
                ) {
                    Text(
                        text = "Use the PC dashboard on your computer for clip review and subtitle editing.",
                        fontSize = 12.sp,
                        color = TextSecondary,
                        textAlign = TextAlign.Center,
                        lineHeight = 18.sp
                    )
                }
            }
        }
    }
}
