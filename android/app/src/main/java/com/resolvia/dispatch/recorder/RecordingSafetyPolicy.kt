package com.resolvia.dispatch.recorder

import java.io.File

internal class RecordingStorageException(message: String, cause: Throwable? = null) :
    IllegalStateException(message, cause)

internal enum class RecordingFinalizeDisposition {
    COMMIT,
    PRESERVE_AND_REPORT_ERROR,
    DELETE_EMPTY_AND_REPORT_ERROR
}

/** Production policy shared by CameraX finalization and recording recovery tests. */
internal object RecordingSafetyPolicy {
    const val MINIMUM_FREE_BYTES: Long = 512L * 1024L * 1024L

    fun finalizeDisposition(hasCameraError: Boolean, fileLength: Long, durationMs: Long): RecordingFinalizeDisposition = when {
        hasCameraError -> RecordingFinalizeDisposition.PRESERVE_AND_REPORT_ERROR
        fileLength <= 0L -> RecordingFinalizeDisposition.DELETE_EMPTY_AND_REPORT_ERROR
        durationMs < 1_000L -> RecordingFinalizeDisposition.PRESERVE_AND_REPORT_ERROR
        else -> RecordingFinalizeDisposition.COMMIT
    }

    fun hasEnoughSpace(availableBytes: Long): Boolean = availableBytes >= MINIMUM_FREE_BYTES

    fun requireAvailableSpace(availableBytes: Long?): Long {
        if (availableBytes == null) {
            throw RecordingStorageException("Could not check recording storage. Recording was stopped to protect your files.")
        }
        if (!hasEnoughSpace(availableBytes)) {
            val availableMb = availableBytes / (1024L * 1024L)
            val requiredMb = MINIMUM_FREE_BYTES / (1024L * 1024L)
            throw RecordingStorageException("Not enough free storage to record safely ($availableMb MB available; $requiredMb MB required). Free space and try again.")
        }
        return availableBytes
    }

    fun isAlreadyCommittedSegmentStatus(status: String?): Boolean = status in setOf(
        "QUEUED_FOR_UPLOAD",
        "FAILED_RETRY",
        "UPLOADING",
        "WAITING_FOR_YOUTUBE",
        "UPLOADED_TO_YOUTUBE",
        "UPLOADED_AWAITING_PROCESSING",
        "PROCESSED_ON_YOUTUBE",
        "VERIFIED_BY_LAPTOP"
    ) || status?.startsWith("FAILED_") == true

    fun finalizedFileCandidate(tmpFile: File, finalFile: File): File? = when {
        tmpFile.isFile && tmpFile.length() > 0L -> tmpFile
        finalFile.isFile && finalFile.length() > 0L -> finalFile
        else -> null
    }
}
