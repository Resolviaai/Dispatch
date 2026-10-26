package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "sessions",
    indices = [Index(value = ["userId"])]
)
data class SessionEntity(
    @PrimaryKey
    val sessionId: String,
    val startedAt: Long = System.currentTimeMillis(),
    val totalSegments: Int = 0,
    val status: String = "RECORDING", // RECORDING, FINALIZED, CRASH_RECOVERED
    val notes: String = "",
    val userId: String? = null
)
