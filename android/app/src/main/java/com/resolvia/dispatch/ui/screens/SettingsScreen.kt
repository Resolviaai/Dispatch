package com.resolvia.dispatch.ui.screens

import android.content.Context
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.sync.LiveSyncManager
import com.resolvia.dispatch.sync.SyncState
import com.resolvia.dispatch.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    pairingManager: PairingManager,
    liveSyncManager: LiveSyncManager,
    syncState: SyncState,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    var hostInput by remember { mutableStateOf(pairingManager.lanHost.ifBlank { "http://192.168.0.102:8000" }) }
    var pinInput by remember { mutableStateOf(pairingManager.pairingPin) }
    var isTestingConnection by remember { mutableStateOf(false) }
    var isPairingWithPin by remember { mutableStateOf(false) }
    var autoWifiSyncEnabled by remember { mutableStateOf(true) }
    var autoSegmentEnabled by remember { mutableStateOf(true) }
    var ytCloudEnabled by remember { mutableStateOf(pairingManager.isYouTubeConfigured) }

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

        // 2. PC Connection Card (Screen 6)
        Text(
            text = "PC Connection",
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
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .background(InputBackground, RoundedCornerShape(10.dp))
                            .border(1.dp, CardBorder, RoundedCornerShape(10.dp)),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "PC",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 15.sp,
                            color = PrimarySky
                        )
                    }

                    Column(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(2.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            val isOnline = syncState.isServerOnline
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .background(if (isOnline) SemanticSuccess else SemanticWarning, CircleShape)
                            )
                            Text(
                                text = if (isOnline) "Connected" else "Disconnected",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                color = if (isOnline) SemanticSuccessText else SemanticWarningText
                            )
                        }

                        Text(
                            text = hostInput.replace("http://", ""),
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            color = TextMuted
                        )
                    }
                }

                // PC Host Address Input
                OutlinedTextField(
                    value = hostInput,
                    onValueChange = { hostInput = it },
                    label = { Text("PC Host URL / IP", fontSize = 11.sp, fontFamily = FontFamily.Monospace) },
                    singleLine = true,
                    textStyle = androidx.compose.ui.text.TextStyle(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        color = TextPrimary
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = InputBackground,
                        unfocusedContainerColor = InputBackground,
                        focusedBorderColor = PrimarySky,
                        unfocusedBorderColor = CardBorder,
                        focusedLabelColor = PrimarySky,
                        unfocusedLabelColor = TextMuted
                    )
                )

                // 6-Digit Pairing PIN Input
                OutlinedTextField(
                    value = pinInput,
                    onValueChange = { if (it.length <= 6) pinInput = it },
                    label = { Text("6-Digit Pairing PIN", fontSize = 11.sp, fontFamily = FontFamily.Monospace) },
                    placeholder = { Text("e.g. 123456", fontSize = 12.sp, color = TextMuted, fontFamily = FontFamily.Monospace) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    textStyle = androidx.compose.ui.text.TextStyle(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = InputBackground,
                        unfocusedContainerColor = InputBackground,
                        focusedBorderColor = PrimarySky,
                        unfocusedBorderColor = CardBorder,
                        focusedLabelColor = PrimarySky,
                        unfocusedLabelColor = TextMuted
                    )
                )

                // Action Buttons: Pair with PIN, Test Connection, Reconnect
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Button(
                        onClick = {
                            isPairingWithPin = true
                            pairingManager.autoPairFromHost(hostInput, pinInput) { success, msg ->
                                isPairingWithPin = false
                                android.os.Handler(android.os.Looper.getMainLooper()).post {
                                    Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
                                }
                            }
                        },
                        modifier = Modifier.weight(1.1f).height(42.dp),
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue),
                        enabled = !isPairingWithPin
                    ) {
                        Text(
                            text = if (isPairingWithPin) "Pairing..." else "Pair (PIN)",
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp,
                            color = Color.White
                        )
                    }

                    Button(
                        onClick = {
                            isTestingConnection = true
                            coroutineScope.launch {
                                val ok = liveSyncManager.networkDiscovery.pingEndpoint(hostInput)
                                isTestingConnection = false
                                Toast.makeText(
                                    context,
                                    if (ok) "PC is Reachable & Online!" else "Could not reach PC at $hostInput",
                                    Toast.LENGTH_SHORT
                                ).show()
                            }
                        },
                        modifier = Modifier.weight(0.9f).height(42.dp),
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = CardSurfaceElevated),
                        border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder),
                        enabled = !isTestingConnection
                    ) {
                        Text(
                            text = if (isTestingConnection) "Testing..." else "Test ping",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = TextPrimary
                        )
                    }

                    Button(
                        onClick = {
                            isTestingConnection = true
                            coroutineScope.launch {
                                val discovered = liveSyncManager.networkDiscovery.discoverAndConnect(timeoutMs = 2500L)
                                isTestingConnection = false
                                if (discovered != null) {
                                    hostInput = discovered
                                    pinInput = pairingManager.pairingPin
                                    Toast.makeText(context, "Discovered & Paired: $discovered", Toast.LENGTH_SHORT).show()
                                } else {
                                    Toast.makeText(context, "Search timed out. Check Wi-Fi.", Toast.LENGTH_SHORT).show()
                                }
                            }
                        },
                        modifier = Modifier.weight(1.0f).height(42.dp),
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = CardSurfaceElevated),
                        border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder),
                        enabled = !isTestingConnection
                    ) {
                        Text(
                            text = "Auto-Find",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = TextPrimary
                        )
                    }
                }
            }
        }

        // 3. Background Sync Section
        Text(
            text = "Background Sync",
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
                    Text("📶", fontSize = 20.sp)
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            text = "Sync over Wi-Fi automatically",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                        Text(
                            text = "Uploads start complete after each recording. You can leave the app.",
                            fontSize = 10.sp,
                            color = TextSecondary
                        )
                    }
                }

                Switch(
                    checked = autoWifiSyncEnabled,
                    onCheckedChange = { autoWifiSyncEnabled = it },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = PrimaryBlue
                    )
                )
            }
        }

        // 4. YouTube Cloud Sync Section (User Priority)
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
                            text = "Direct YouTube Cloud Sync",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary
                        )
                        Text(
                            text = if (pairingManager.isYouTubeConfigured) "Connected to YouTube. Cloud transcription active." else "Pair with PC on Wi-Fi to sync YouTube credentials.",
                            fontSize = 10.sp,
                            color = if (pairingManager.isYouTubeConfigured) SemanticSuccessText else TextSecondary
                        )
                    }
                }

                Switch(
                    checked = ytCloudEnabled,
                    onCheckedChange = { ytCloudEnabled = it },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = PrimaryBlue
                    )
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
