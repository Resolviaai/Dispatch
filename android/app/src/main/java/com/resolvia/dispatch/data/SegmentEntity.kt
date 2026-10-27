package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "segments",
    indices = [Index(value = ["userId"])]
)
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
    val youtubeVideoId: String? = null,
    val userId: String? = null,
    // Which credential flow owns the uploaded video: "cloud" (vault session)
    // or "legacy" (locally stored OAuth). Drives status reconciliation.
    val uploadOrigin: String = "",
    val youtubeCheckAttempts: Int = 0,
    val youtubeCheckAt: Long = 0
)

val SegmentEntity.durationSeconds: Long
    get() = if (finalizedAt != null && finalizedAt > createdAt) (finalizedAt - createdAt) / 1000L else 0L
