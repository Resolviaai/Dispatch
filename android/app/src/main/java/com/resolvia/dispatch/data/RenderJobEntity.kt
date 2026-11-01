package com.resolvia.dispatch.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Durable tracking of on-device video rendering and R2 publishing jobs.
 * Persisted in Room SQLite WAL so jobs survive process death and device restarts.
 */
@Entity(
    tableName = "render_jobs",
    indices = [
        Index(value = ["clipId"]),
        Index(value = ["status"]),
        Index(value = ["renderFingerprint"])
    ]
)
data class RenderJobEntity(
    @PrimaryKey
    val jobId: String,
    val clipId: String,
    val renderFingerprint: String,
    val projectDocumentJson: String,
    val sourceVideoPath: String,
    val outputVideoPath: String,
    val thumbnailPath: String? = null,
    val layoutMode: String = "portrait_9_16",
    val cropAnchorX: Float = 0.5f,
    val targetResolution: String = "1080p", // "1080p" or "720p"
    val title: String = "",
    val description: String = "",
    val platforms: String = "youtube",
    val publishMode: String = "private",
    val status: String = STATUS_QUEUED,
    val progressPercent: Int = 0,
    val renderedDurationSeconds: Float = 0.0f,
    val r2Key: String? = null,
    val r2UploadOffset: Long = 0L,
    val r2TotalBytes: Long = 0L,
    val lastError: String? = null,
    val retryCount: Int = 0,
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
    val completedAt: Long? = null
) {
    companion object {
        const val STATUS_QUEUED = "QUEUED"
        const val STATUS_RENDERING = "RENDERING"
        const val STATUS_RENDER_COMPLETE = "RENDER_COMPLETE"
        const val STATUS_UPLOADING = "UPLOADING"
        const val STATUS_UPLOAD_COMPLETE = "UPLOAD_COMPLETE"
        const val STATUS_PUBLISHING_QUEUED = "PUBLISHING_QUEUED"
        const val STATUS_FAILED = "FAILED"
        const val STATUS_CANCELLED = "CANCELLED"
    }
}
