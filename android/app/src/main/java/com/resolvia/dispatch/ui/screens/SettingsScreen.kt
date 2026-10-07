package com.resolvia.dispatch.ui.screens

import android.content.Context
import android.os.Environment
import android.os.StatFs
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.ui.theme.*

@Composable
fun SettingsScreen(
    pairingManager: PairingManager,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    var autoSegmentEnabled by remember { mutableStateOf(true) }
    var isYouTubeConfigured by remember { mutableStateOf(pairingManager.isYouTubeConfigured) }
    var refreshToken by remember { mutableStateOf(pairingManager.youtubeRefreshToken) }
    var clientId by remember { mutableStateOf(pairingManager.youtubeClientId) }
    var clientSecret by remember { mutableStateOf(pairingManager.youtubeClientSecret) }

    // Storage stat
    val (freeGb, totalGb) = getStorageStats()

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // 1. Header
        Text(
            text = "Settings",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 24.sp,
            color = TextPrimary,
            modifier = Modifier.padding(top = 4.dp)
        )

        // YouTube credentials are stored on the phone and used only for YouTube uploads.
        Box(
            modifier = Modifier.fillMaxWidth().background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp)).padding(16.dp)
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("YouTube upload account", fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                    fontWeight = FontWeight.Bold, color = TextPrimary)
                Text("Enter the refresh token, client ID, and client secret from your authorized YouTube OAuth credentials. The phone uploads directly to YouTube and does not connect to the PC.", fontSize = 11.sp, color = TextSecondary)
                OutlinedTextField(value = refreshToken, onValueChange = { refreshToken = it }, label = { Text("Refresh token") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = clientId, onValueChange = { clientId = it }, label = { Text("OAuth client ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = clientSecret, onValueChange = { clientSecret = it }, label = { Text("OAuth client secret") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                Button(onClick = {
                    pairingManager.saveYouTubeCredentials(refreshToken, clientId, clientSecret)
                    isYouTubeConfigured = pairingManager.isYouTubeConfigured
                }, enabled = refreshToken.isNotBlank() && clientId.isNotBlank() && clientSecret.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
                    Text("Save YouTube credentials")
                }
            }
        }

        // YouTube upload status
        Text(
            text = "YouTube Cloud Inbox",
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
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f)
                ) {
                    Text("☁️", fontSize = 20.sp)
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            text = "Direct phone-to-YouTube upload",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                        Text(
                            text = if (isYouTubeConfigured) "Ready. Uploads go to YouTube; PC processing runs independently." else "Add YouTube OAuth credentials above. The PC is not contacted by the phone.",
                            fontSize = 10.sp,
                            color = if (isYouTubeConfigured) SemanticSuccessText else TextSecondary
                        )
                    }
                }

                Text(
                    text = if (isYouTubeConfigured) "READY" else "SET UP",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (isYouTubeConfigured) SemanticSuccessText else TextSecondary
                )
            }
        }

        // 5. Recording Section
        Text(
            text = "Recording",
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
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                SettingRowItem(label = "Video quality", value = "1080p (Recommended)")
                HorizontalDivider(color = CardBorder)
                SettingRowItem(label = "Microphone", value = "Phone mic (Default)")
                HorizontalDivider(color = CardBorder)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(
                            text = "Auto-segment",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                        Text(
                            text = "Split long recordings automatically",
                            fontSize = 10.sp,
                            color = TextSecondary
                        )
                    }
                    Switch(
                        checked = autoSegmentEnabled,
                        onCheckedChange = { autoSegmentEnabled = it },
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = Color.White,
                            checkedTrackColor = PrimaryBlue
                        )
                    )
                }
            }
        }

        // 6. Storage Section
        Text(
            text = "Storage",
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
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                SettingRowItem(label = "Storage location", value = "Internal storage")

                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Free space", fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = TextMuted)
                        Text(
                            text = "$freeGb GB / $totalGb GB",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                    }
                    LinearProgressIndicator(
                        progress = { (1f - (freeGb / totalGb.toFloat()).coerceIn(0f, 1f)) },
                        modifier = Modifier.fillMaxWidth().height(6.dp),
                        color = PrimarySky,
                        trackColor = InputBackground
                    )
                }
            }
        }

        // 7. App Version Footer
        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
            horizontalArrangement = Arrangement.Center
        ) {
            Text(
                text = "App version v1.0.0 (Build 12)",
                fontFamily = FontFamily.Monospace,
                fontSize = 10.sp,
                color = TextMuted
            )
        }
    }
}

@Composable
fun SettingRowItem(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            fontFamily = FontFamily.Monospace,
            fontSize = 12.sp,
            color = TextPrimary
        )
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Text(
                text = value,
                fontSize = 11.sp,
                color = TextSecondary
            )
            Text("›", fontSize = 16.sp, color = TextMuted)
        }
    }
}

private fun getStorageStats(): Pair<Int, Int> {
    return try {
        val path = Environment.getDataDirectory()
        val stat = StatFs(path.path)
        val blockSize = stat.blockSizeLong
        val totalBlocks = stat.blockCountLong
        val availableBlocks = stat.availableBlocksLong
        val totalGb = (totalBlocks * blockSize / (1024L * 1024L * 1024L)).toInt()
        val freeGb = (availableBlocks * blockSize / (1024L * 1024L * 1024L)).toInt()
        Pair(freeGb, totalGb)
    } catch (_: Exception) {
        Pair(48, 128)
    }
}
