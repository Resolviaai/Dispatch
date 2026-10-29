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

/** Read-only projection for the Sessions upload progress display. */
data class OutboxProgress(
    val segmentId: String,
    val remoteOffset: Long,
    val status: String,
    val lastError: String?
)
