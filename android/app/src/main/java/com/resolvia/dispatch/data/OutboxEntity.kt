package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "outbox")
data class OutboxEntity(
    @PrimaryKey
    val segmentId: String,
    val sessionId: String,
    val remoteOffset: Long = 0L,
    val status: String = "QUEUED_FOR_UPLOAD", // QUEUED_FOR_UPLOAD, UPLOADING, VERIFIED_BY_LAPTOP, FAILED_RETRY
    val attemptCount: Int = 0,
    val lastError: String? = null,
    val updatedAt: Long = System.currentTimeMillis()
)
