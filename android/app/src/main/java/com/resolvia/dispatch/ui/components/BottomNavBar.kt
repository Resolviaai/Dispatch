package com.resolvia.dispatch.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.resolvia.dispatch.ui.theme.*

/**
 * Dispatch BottomNavigation bar for switching between core creator workflow screens.
 */
enum class NavigationTab(val title: String) {
    RECORD("Record"),
    SESSIONS("Sessions"),
    CLIPS("Clips"),
    SETTINGS("Settings")
}

@Composable
fun DispatchBottomBar(
    selectedTab: NavigationTab,
    onTabSelected: (NavigationTab) -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(CanvasBackground)
    ) {
        // Subtle top border divider
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(Color(0x1FFFFFFF))
                .align(Alignment.TopCenter)
        )

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 8.dp, horizontal = 12.dp),
            horizontalArrangement = Arrangement.SpaceAround,
            verticalAlignment = Alignment.CenterVertically
        ) {
            NavigationTab.values().forEach { tab ->
                val isSelected = tab == selectedTab
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                    modifier = Modifier
                        .clickable { onTabSelected(tab) }
                        .padding(horizontal = 16.dp, vertical = 6.dp)
                ) {
                    TabIcon(tab = tab, isSelected = isSelected)

                    Text(
                        text = tab.title,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
                        color = if (isSelected) PrimarySky else TextMuted
                    )
                }
            }
        }
    }
}

@Composable
private fun TabIcon(tab: NavigationTab, isSelected: Boolean) {
    val tint = if (isSelected) PrimarySky else TextMuted.copy(alpha = 0.7f)
    Canvas(modifier = Modifier.size(22.dp)) {
        val w = size.width
        val h = size.height
        when (tab) {
            NavigationTab.RECORD -> {
                // Bullseye / Target circle icon
                drawCircle(
                    color = tint,
                    radius = w * 0.42f,
                    style = Stroke(width = 2.dp.toPx())
                )
                drawCircle(
                    color = tint,
                    radius = w * 0.18f,
                    style = androidx.compose.ui.graphics.drawscope.Fill
                )
            }
            NavigationTab.SESSIONS -> {
                val strokeW = 2.dp.toPx()
                drawLine(tint, Offset(w * 0.15f, h * 0.28f), Offset(w * 0.85f, h * 0.28f), strokeWidth = strokeW, cap = StrokeCap.Round)
                drawLine(tint, Offset(w * 0.15f, h * 0.5f), Offset(w * 0.85f, h * 0.5f), strokeWidth = strokeW, cap = StrokeCap.Round)
                drawLine(tint, Offset(w * 0.15f, h * 0.72f), Offset(w * 0.85f, h * 0.72f), strokeWidth = strokeW, cap = StrokeCap.Round)
            }
            NavigationTab.CLIPS -> {
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(w * 0.22f, h * 0.12f),
                    size = Size(w * 0.56f, h * 0.76f),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(3.dp.toPx()),
                    style = Stroke(width = 2.dp.toPx())
                )
                val path = androidx.compose.ui.graphics.Path().apply {
                    moveTo(w * 0.44f, h * 0.38f)
                    lineTo(w * 0.62f, h * 0.5f)
                    lineTo(w * 0.44f, h * 0.62f)
                    close()
                }
                drawPath(path, color = tint)
            }
            NavigationTab.SETTINGS -> {
                // Gear wheel with teeth
                val cx = w * 0.5f
                val cy = h * 0.5f
                val r = w * 0.35f
                drawCircle(color = tint, radius = r, style = Stroke(width = 2.dp.toPx()))
                drawCircle(color = tint, radius = r * 0.35f, style = androidx.compose.ui.graphics.drawscope.Fill)
                // 4 gear teeth pegs
                val toothLen = 3.dp.toPx()
                val strokeW = 2.dp.toPx()
                drawLine(tint, Offset(cx, cy - r - toothLen), Offset(cx, cy - r), strokeWidth = strokeW, cap = StrokeCap.Round)
                drawLine(tint, Offset(cx, cy + r), Offset(cx, cy + r + toothLen), strokeWidth = strokeW, cap = StrokeCap.Round)
                drawLine(tint, Offset(cx - r - toothLen, cy), Offset(cx - r, cy), strokeWidth = strokeW, cap = StrokeCap.Round)
                drawLine(tint, Offset(cx + r, cy), Offset(cx + r + toothLen, cy), strokeWidth = strokeW, cap = StrokeCap.Round)
            }
        }
    }
}
