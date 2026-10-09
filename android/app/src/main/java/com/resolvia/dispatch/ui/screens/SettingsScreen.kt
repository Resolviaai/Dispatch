package com.resolvia.dispatch.ui.screens

import android.os.Environment
import android.os.StatFs
import android.widget.Toast
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.ui.theme.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Screen 6: Settings & Transport Configuration.
 * - YouTube Connection with real OAuth state & channel info
 * - 1-Tap Sync from PC hub
 * - Upload over Wi-Fi automatically (unmetered only)
 * - Video Quality (1080p FHD 30fps)
 * - Device Storage meter
 */
@Composable
fun SettingsScreen(
    pairingManager: PairingManager,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    var isConnected by remember { mutableStateOf(pairingManager.isYouTubeConfigured) }
    var channelName by remember { mutableStateOf("") }
    var isSyncing by remember { mutableStateOf(false) }
    var wifiOnly by remember { mutableStateOf(true) }

    // Check YouTube credentials on load
    LaunchedEffect(Unit) {
        isConnected = pairingManager.isYouTubeConfigured
    }

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

        // 2. YouTube Connection Card
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
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                 .size(8.dp)
                                 .background(if (isConnected) SemanticSuccess else SemanticRecording, CircleShape)
                        )
                        Text(
                            text = if (isConnected) "YouTube Connected" else "Not Connected",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                    }

                    if (isConnected) {
                        Text(
                            text = "Disconnect",
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            color = SemanticRecording,
                            modifier = Modifier.clickable {
                                pairingManager.clearCredentials()
                                isConnected = false
                                Toast.makeText(context, "YouTube credentials cleared.", Toast.LENGTH_SHORT).show()
                            }
                        )
                    }
                }

                Text(
                    text = "Recordings are uploaded as private videos tagged [DISPATCH] and ingested by your PC.",
                    fontSize = 11.sp,
                    color = TextSecondary,
                    lineHeight = 16.sp
                )

                // 1-Tap Sync Credentials from PC
                Button(
                    onClick = {
                        coroutineScope.launch {
                            isSyncing = true
                            try {
                                val client = OkHttpClient.Builder()
                                    .connectTimeout(5, TimeUnit.SECONDS)
                                    .build()
                                val req = Request.Builder()
                                    .url("http://192.168.0.103:8000/api/youtube/credentials")
                                    .build()
                                val (ok, msg) = withContext(Dispatchers.IO) {
                                    try {
                                        client.newCall(req).execute().use { resp ->
                                            if (resp.isSuccessful) {
                                                val body = Gson().fromJson(resp.body?.string(), JsonObject::class.java)
                                                val refreshToken = body.get("refresh_token")?.asString ?: ""
                                                val clientId = body.get("client_id")?.asString ?: ""
                                                val clientSecret = body.get("client_secret")?.asString ?: ""
                                                if (refreshToken.isNotBlank() && clientId.isNotBlank()) {
                                                    pairingManager.youtubeRefreshToken = refreshToken
                                                    pairingManager.youtubeClientId = clientId
                                                    pairingManager.youtubeClientSecret = clientSecret
                                                    Pair(true, "Synced YouTube credentials from PC!")
                                                } else {
                                                    Pair(false, "PC does not have YouTube credentials configured yet.")
                                                }
                                            } else {
                                                Pair(false, "PC returned HTTP ${resp.code}")
                                            }
                                        }
                                    } catch (e: Exception) {
                                        Pair(false, "Cannot connect to PC (192.168.0.103:8000): ${e.message}")
                                    }
                                }
                                Toast.makeText(context, msg, Toast.LENGTH_LONG).show()
                                isConnected = pairingManager.isYouTubeConfigured
                            } finally {
                                isSyncing = false
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth().height(42.dp),
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = PrimarySky)
                ) {
                    Text(
                        text = if (isSyncing) "Syncing..." else "1-Tap Sync Credentials from PC",
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = Color.Black
                    )
                }
            }
        }

        // 3. Network & Upload Behavior
        Text(
            text = "Upload Policy",
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
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Upload over Wi-Fi automatically",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = TextPrimary
                    )
                    Text(
                        text = "Unmetered network only (never mobile data)",
                        fontSize = 10.sp,
                        color = TextMuted
                    )
                }

                Switch(
                    checked = wifiOnly,
                    onCheckedChange = { wifiOnly = it },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = PrimarySky,
                        uncheckedTrackColor = InputBackground
                    )
                )
            }
        }

        // 4. Video Recording Specifications
        Text(
            text = "Recording Specifications",
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
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Resolution", fontSize = 11.sp, color = TextSecondary)
                    Text("1080p FHD (1920x1080)", fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = TextPrimary)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Frame Rate", fontSize = 11.sp, color = TextSecondary)
                    Text("30 FPS Constant", fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = TextPrimary)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Segment Duration", fontSize = 11.sp, color = TextSecondary)
                    Text("10-minute continuous roll", fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = TextPrimary)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Audio Codec", fontSize = 11.sp, color = TextSecondary)
                    Text("AAC 48kHz Stereo", fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = TextPrimary)
                }
            }
        }

        // 5. Storage Info
        val stat = StatFs(Environment.getDataDirectory().path)
        val freeBytes = stat.availableBlocksLong * stat.blockSizeLong
        val totalBytes = stat.blockCountLong * stat.blockSizeLong
        val freeGb = freeBytes / (1024L * 1024L * 1024L)
        val totalGb = totalBytes / (1024L * 1024L * 1024L)

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .background(CardSurface, RoundedCornerShape(14.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(14.dp))
                .padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text("Device Storage", fontSize = 12.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, color = TextPrimary)
                    Text("POCO C65 Internal", fontSize = 10.sp, color = TextMuted)
                }
                Text("$freeGb GB free of $totalGb GB", fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = PrimarySky)
            }
        }
    }
}
