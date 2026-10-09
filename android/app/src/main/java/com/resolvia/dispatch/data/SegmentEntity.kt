package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "segments")
data class SegmentEntity(
    @PrimaryKey
    val segmentId: String,
    val sessionId: String,
    val sequenceNumber: Int,
    val filename: String,
    val filepath: String,
    val fileSizeBytes: Long = 0L,
    val sha256Hash: String = "",
    val status: String = "RECORDING", // RECORDING, FINALIZED, QUEUED_FOR_UPLOAD, VERIFIED_BY_LAPTOP
    val createdAt: Long = System.currentTimeMillis(),
    val finalizedAt: Long? = null,
    val youtubeVideoId: String? = null
)

val SegmentEntity.durationSeconds: Long
    get() = if (finalizedAt != null && finalizedAt > createdAt) (finalizedAt - createdAt) / 1000L else 0L
