package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey
    val sessionId: String,
    val startedAt: Long = System.currentTimeMillis(),
    val totalSegments: Int = 0,
    val status: String = "RECORDING", // RECORDING, FINALIZED, CRASH_RECOVERED
    val notes: String = ""
)
