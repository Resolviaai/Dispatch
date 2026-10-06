package com.resolvia.dispatch

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.recorder.SegmenterEngine
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var segmenterEngine: SegmenterEngine
    private lateinit var database: AppDatabase

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        database = AppDatabase.getDatabase(this)
        segmenterEngine = SegmenterEngine(this, database)

        setContent {
            val pendingCount by database.recordingDao().getPendingOutboxCountFlow().collectAsState(initial = 0)
            var isRecording by remember { mutableStateOf(false) }
            var currentSession by remember { mutableStateOf<String?>(null) }

            DispatchMobileTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = Color(0xFF070A0F)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        verticalArrangement = Arrangement.SpaceBetween,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        // Header
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "DISPATCH",
                                fontFamily = FontFamily.Monospace,
                                fontSize = 16.sp,
                                color = Color.White
                            )
                            Box(
                                modifier = Modifier
                                    .background(Color(0xFF131A26), RoundedCornerShape(8.dp))
                                    .padding(horizontal = 10.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = "OUTBOX: $pendingCount",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 12.sp,
                                    color = Color(0xFF94A3B8)
                                )
                            }
                        }

                        // Center Status Card
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(280.dp)
                                .background(Color(0xFF0B0F17), RoundedCornerShape(16.dp))
                                .border(1.dp, Color(0x1AFFFFFF), RoundedCornerShape(16.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text(
                                    text = if (isRecording) "RECORDING ACTIVE" else "STANDBY",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 14.sp,
                                    color = if (isRecording) Color(0xFFEF4444) else Color(0xFF64748B)
                                )
                                Spacer(modifier = Modifier.height(8.dp))
                                Text(
                                    text = currentSession ?: "Ready to record",
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 12.sp,
                                    color = Color(0xFF475569)
                                )
                            }
                        }

                        // Trigger Button
                        Button(
                            onClick = {
                                lifecycleScope.launch {
                                    if (!isRecording) {
                                        val sess = segmenterEngine.startSession("POCO C65 Mobile Capture")
                                        currentSession = sess
                                        isRecording = true
                                    } else {
                                        segmenterEngine.stopSession()
                                        isRecording = false
                                        currentSession = null
                                    }
                                }
                            },
                            modifier = Modifier.size(80.dp),
                            shape = CircleShape,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (isRecording) Color(0xFFDC2626) else Color(0xFF2563EB)
                            )
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(24.dp)
                                    .background(Color.White, if (isRecording) RoundedCornerShape(4.dp) else CircleShape)
                            )
                        }

                        Text(
                            text = "POCO C65 Durable Native Recorder",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            color = Color(0xFF475569)
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun DispatchMobileTheme(content: @Composable () -> Unit) {
    MaterialTheme(content = content)
}
