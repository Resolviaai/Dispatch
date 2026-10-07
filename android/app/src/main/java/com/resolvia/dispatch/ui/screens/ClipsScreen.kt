package com.resolvia.dispatch.ui.screens

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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.resolvia.dispatch.data.OutboxEntity
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.ui.theme.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

data class RemoteClipItem(
    val id: String,
    val title: String,
    val hook: String,
    val duration: Float,
    val status: String,
    val filename: String,
    val caption: String = ""
)

@Composable
fun ClipsScreen(
    pairingManager: PairingManager,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()
    val scrollState = rememberScrollState()

    var selectedTab by remember { mutableStateOf(0) } // 0 = Ready, 1 = Approved
    var clipsList by remember { mutableStateOf<List<RemoteClipItem>>(emptyList()) }
    var isLoading by remember { mutableStateOf(false) }
    var currentClipIndex by remember { mutableStateOf(0) }

    // Form inputs for current clip
    var currentTitle by remember { mutableStateOf("Why Consistency Beats Motivation") }
    var currentCaption by remember { mutableStateOf("Small steps every day compound into big results. #consistency #growth") }

    // Platform toggles
    var ytShortsEnabled by remember { mutableStateOf(true) }
    var igReelsEnabled by remember { mutableStateOf(true) }
    var linkedInEnabled by remember { mutableStateOf(true) }
    var twitterEnabled by remember { mutableStateOf(false) }

    // Load clips from PC server
    fun fetchClips() {
        if (pairingManager.lanHost.isBlank()) return
        isLoading = true
        coroutineScope.launch(Dispatchers.IO) {
            try {
                val client = OkHttpClient()
                val req = Request.Builder()
                    .url("${pairingManager.lanHost.trimEnd('/')}/api/clips")
                    .get()
                    .build()

                client.newCall(req).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val body = resp.body?.string() ?: ""
                        val json = Gson().fromJson(body, JsonArray::class.java)
                        val items = mutableListOf<RemoteClipItem>()
                        for (i in 0 until json.size()) {
                            val obj = json[i].asJsonObject
                            items.add(
                                RemoteClipItem(
                                    id = obj.get("id")?.asString ?: "clip_$i",
                                    title = obj.get("title")?.asString ?: "Generated Clip",
                                    hook = obj.get("hook")?.asString ?: "Strong hook",
                                    duration = obj.get("duration")?.asFloat ?: 42.0f,
                                    status = obj.get("status")?.asString ?: "ready_review",
                                    filename = obj.get("filename")?.asString ?: "clip.mp4"
                                )
                            )
                        }
                        withContext(Dispatchers.Main) {
                            clipsList = items
                            isLoading = false
                            if (items.isNotEmpty()) {
                                currentTitle = items[0].title
                                currentCaption = items[0].hook
                            }
                        }
                    }
                }
            } catch (_: Exception) {
                withContext(Dispatchers.Main) { isLoading = false }
            }
        }
    }

    LaunchedEffect(Unit) {
        fetchClips()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(CanvasBackground)
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        // 1. Top Header Row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = "Ready to review",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 20.sp,
                    color = TextPrimary
                )
                Text(
                    text = "${clipsList.size.coerceAtLeast(1)} clips ready from this session",
                    fontSize = 12.sp,
                    color = TextSecondary
                )
            }

            Box(
                modifier = Modifier
                    .size(32.dp)
                    .background(CardSurface, CircleShape)
                    .border(1.dp, CardBorder, CircleShape)
                    .clickable { fetchClips() },
                contentAlignment = Alignment.Center
            ) {
                Text("🔄", fontSize = 14.sp)
            }
        }

        // 2. Filter Tabs (Ready | Approved)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(24.dp)
        ) {
            Column(
                modifier = Modifier.clickable { selectedTab = 0 },
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text(
                    text = "Ready (${clipsList.size.coerceAtLeast(1)})",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    fontWeight = if (selectedTab == 0) FontWeight.Bold else FontWeight.Normal,
                    color = if (selectedTab == 0) PrimarySky else TextMuted
                )
                if (selectedTab == 0) {
                    Box(modifier = Modifier.fillMaxWidth().height(2.dp).background(PrimarySky))
                }
            }

            Column(
                modifier = Modifier.clickable { selectedTab = 1 },
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text(
                    text = "Approved (0)",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    fontWeight = if (selectedTab == 1) FontWeight.Bold else FontWeight.Normal,
                    color = if (selectedTab == 1) PrimarySky else TextMuted
                )
                if (selectedTab == 1) {
                    Box(modifier = Modifier.fillMaxWidth().height(2.dp).background(PrimarySky))
                }
            }
        }

        // 3. 9:16 Vertical Video Player Card (Screen 5)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(340.dp)
                .background(CardSurface, RoundedCornerShape(16.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(16.dp))
                .clip(RoundedCornerShape(16.dp)),
            contentAlignment = Alignment.Center
        ) {
            // Dark Viewport background
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color(0xFF090D14)),
                contentAlignment = Alignment.Center
            ) {
                // Mock Video Scene / Subtitle Preview
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                    modifier = Modifier.padding(20.dp)
                ) {
                    Text("🎬", fontSize = 48.sp)
                    Spacer(modifier = Modifier.height(16.dp))

                    // Karaoke Highlight Subtitle Overlay
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("The", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Color.White)
                        Box(
                            modifier = Modifier
                                .background(PrimarySky, RoundedCornerShape(3.dp))
                                .padding(horizontal = 4.dp, vertical = 1.dp)
                        ) {
                            Text("key", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Color.Black)
                        }
                        Text("here is to keep it simple...", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = Color.White)
                    }
                }
            }

            // Top Badges Overlay (Index 1/4 and More Options)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.TopCenter)
                    .padding(14.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .background(Color(0x88000000), RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 3.dp)
                ) {
                    Text(
                        text = "${currentClipIndex + 1} / ${clipsList.size.coerceAtLeast(1)}",
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        color = Color.White
                    )
                }

                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .background(Color(0x88000000), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text("⋮", fontSize = 16.sp, color = Color.White)
                }
            }

            // Bottom Player Progress Bar
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomCenter)
                    .background(Color(0x99000000))
                    .padding(horizontal = 14.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text("▶", fontSize = 12.sp, color = Color.White)
                    Text("0:04 / 0:42", fontFamily = FontFamily.Monospace, fontSize = 10.sp, color = Color.White)
                }
                Text("⛶", fontSize = 14.sp, color = Color.White)
            }
        }

        // 4. Hook Pill & Duration
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .background(SemanticSuccessBg, RoundedCornerShape(6.dp))
                    .padding(horizontal = 8.dp, vertical = 4.dp)
            ) {
                Text(
                    text = "42 sec • Strong hook",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = SemanticSuccessText
                )
            }

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text("✏", fontSize = 12.sp, color = PrimarySky)
                Text(
                    text = "Edit",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    color = PrimarySky
                )
            }
        }

        // 5. Title & Caption Inputs
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Title", fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = TextMuted)
            OutlinedTextField(
                value = currentTitle,
                onValueChange = { currentTitle = it },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = PrimarySky,
                    unfocusedBorderColor = CardBorder,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    focusedContainerColor = InputBackground,
                    unfocusedContainerColor = InputBackground
                )
            )

            Text("Caption", fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = TextMuted)
            OutlinedTextField(
                value = currentCaption,
                onValueChange = { currentCaption = it },
                modifier = Modifier.fillMaxWidth(),
                maxLines = 3,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = PrimarySky,
                    unfocusedBorderColor = CardBorder,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    focusedContainerColor = InputBackground,
                    unfocusedContainerColor = InputBackground
                )
            )
        }

        // 6. Publish To Platform Toggles
        Text("Publish to", fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = TextMuted)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            PlatformToggleChip(
                label = "YouTube Shorts",
                isEnabled = ytShortsEnabled,
                onClick = { ytShortsEnabled = !ytShortsEnabled },
                modifier = Modifier.weight(1f)
            )
            PlatformToggleChip(
                label = "Instagram",
                isEnabled = igReelsEnabled,
                onClick = { igReelsEnabled = !igReelsEnabled },
                modifier = Modifier.weight(1f)
            )
            PlatformToggleChip(
                label = "LinkedIn",
                isEnabled = linkedInEnabled,
                onClick = { linkedInEnabled = !linkedInEnabled },
                modifier = Modifier.weight(1f)
            )
            PlatformToggleChip(
                label = "X",
                isEnabled = twitterEnabled,
                onClick = { twitterEnabled = !twitterEnabled },
                modifier = Modifier.weight(0.7f)
            )
        }

        // 7. Bottom Actions: Reject and Approve & Publish
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Button(
                onClick = {
                    Toast.makeText(context, "Clip rejected", Toast.LENGTH_SHORT).show()
                },
                modifier = Modifier.weight(1f).height(48.dp),
                shape = RoundedCornerShape(10.dp),
                colors = ButtonDefaults.buttonColors(containerColor = CardSurface),
                border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder)
            ) {
                Text(
                    text = "Reject",
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 12.sp,
                    color = TextPrimary
                )
            }

            Button(
                onClick = {
                    // Approve clip on PC
                    coroutineScope.launch(Dispatchers.IO) {
                        try {
                            val client = OkHttpClient()
                            val clipId = if (clipsList.isNotEmpty()) clipsList[0].id else "clip_01"
                            val payload = "{\"title\":\"${currentTitle}\",\"publish_mode\":\"public\"}"
                            val req = Request.Builder()
                                .url("${pairingManager.lanHost.trimEnd('/')}/api/clips/$clipId/approve")
                                .post(payload.toRequestBody("application/json".toMediaType()))
                                .build()
                            client.newCall(req).execute().use { resp ->
                                withContext(Dispatchers.Main) {
                                    if (resp.isSuccessful) {
                                        Toast.makeText(context, "Clip Approved & Queued for Publish!", Toast.LENGTH_SHORT).show()
                                        fetchClips()
                                    } else {
                                        Toast.makeText(context, "Approve failed: HTTP ${resp.code}", Toast.LENGTH_SHORT).show()
                                    }
                                }
                            }
                        } catch (e: Exception) {
                            withContext(Dispatchers.Main) {
                                Toast.makeText(context, "Network error: ${e.message}", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                },
                modifier = Modifier.weight(1.5f).height(48.dp),
                shape = RoundedCornerShape(10.dp),
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryBlue)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    Text("✈", fontSize = 13.sp)
                    Text(
                        text = "Approve & publish",
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        fontSize = 12.sp,
                        color = Color.White
                    )
                }
            }
        }
    }
}

@Composable
fun PlatformToggleChip(
    label: String,
    isEnabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .background(if (isEnabled) CardSurfaceElevated else CardSurface, RoundedCornerShape(8.dp))
            .border(1.dp, if (isEnabled) PrimarySky else CardBorder, RoundedCornerShape(8.dp))
            .clickable { onClick() }
            .padding(vertical = 8.dp, horizontal = 4.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = label,
            fontFamily = FontFamily.Monospace,
            fontSize = 9.sp,
            fontWeight = if (isEnabled) FontWeight.Bold else FontWeight.Normal,
            color = if (isEnabled) PrimarySky else TextMuted,
            maxLines = 1
        )
    }
}
