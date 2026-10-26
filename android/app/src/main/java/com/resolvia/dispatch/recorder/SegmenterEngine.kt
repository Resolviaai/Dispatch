package com.resolvia.dispatch.recorder

import android.content.Context
import android.media.MediaMetadataRetriever
import android.os.StatFs
import androidx.room.withTransaction
import androidx.work.*
import com.resolvia.dispatch.data.*
import kotlinx.coroutines.*
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest

/**
 * Native Android Recording Segmenter Engine.
 * Coordinates CameraX hardware video/audio capture into rolling 10-minute segments.
 * Crash-safe: writes to .tmp, atomically renames to .mp4 upon completion, calculates SHA-256,
 * and enqueues to WorkManager sync outbox.
 */
class SegmenterEngine(
    private val context: Context,
    private val database: AppDatabase
) {
    private val dao = database.recordingDao()
    private val recordingsDir: File = File(context.getExternalFilesDir(null) ?: context.filesDir, "recordings").apply {
        if (!exists()) mkdirs()
    }

    var currentSessionId: String? = null
        private set
    var currentSegmentId: String? = null
        private set
    var currentSequenceNumber: Int = 0
        private set
    var currentTmpFile: File? = null
        private set

    var isSessionActive: Boolean = false
        private set

    @Volatile var isSessionPaused: Boolean = false
        private set

    // Default 10-minute rolling segments (600,000 ms)
    var segmentDurationMs: Long = 10 * 60 * 1000L
    var currentUserId: String? = null

    private var segmentRollJob: Job? = null
    private var storageMonitorJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var activeFinalizeDeferred: CompletableDeferred<Unit>? = null

    suspend fun startSession(
        cameraManager: CameraCaptureManager,
        notes: String = ""
    ): String = withContext(Dispatchers.IO) {
        requireRecordingStorage()
        val sessionId = "sess_${System.currentTimeMillis()}"
        val session = SessionEntity(sessionId = sessionId, notes = notes, userId = currentUserId)
        RecordingForegroundService.markSessionActive(sessionId)
        try {
            dao.insertSession(session)
        } catch (e: Exception) {
            RecordingForegroundService.markSessionInactive(sessionId)
            throw e
        }

        currentSessionId = sessionId
        currentSequenceNumber = 0
        isSessionActive = true
        isSessionPaused = false

        // Bind CameraX directly to Foreground Service lifecycle so recording survives screen lock
        val bound = withTimeoutOrNull(10000L) {
            RecordingForegroundService.startAndAwaitBind(context, cameraManager)
        } ?: false

        if (!bound) {
            isSessionActive = false
            RecordingForegroundService.markSessionInactive(sessionId)
            dao.updateSessionStatus(sessionId, 0, "FINALIZED")
            throw IllegalStateException("Failed to bind camera capture to foreground service")
        }

        try {
            withContext(Dispatchers.Main) {
                startNextSegment(cameraManager)
                startStorageMonitor(cameraManager)
            }
        } catch (e: Exception) {
            isSessionActive = false
            if (e is RecordingStorageException) {
                runCatching { dao.updateSessionStatus(sessionId, 0, "FINALIZED") }
            }
            RecordingForegroundService.markSessionInactive(sessionId)
            RecordingForegroundService.stop(context)
            throw e
        }

        sessionId
    }

    suspend fun startNextSegment(cameraManager: CameraCaptureManager) {
        if (!isSessionActive) return
        requireRecordingStorage()
        val sessionId = currentSessionId ?: return

        currentSequenceNumber++
        val sequenceNumber = currentSequenceNumber
        val segId = String.format("%s_seg_%04d", sessionId, sequenceNumber)
        val tmpFile = File(recordingsDir, "$segId.tmp")

        currentSegmentId = segId
        currentTmpFile = tmpFile

        val segment = SegmentEntity(
            segmentId = segId,
            sessionId = sessionId,
            sequenceNumber = sequenceNumber,
            filename = "$segId.mp4",
            filepath = File(recordingsDir, "$segId.mp4").absolutePath,
            status = "RECORDING",
            userId = currentUserId
        )
        // Persist the row before CameraX can begin writing; recovery must always see ownership.
        withContext(Dispatchers.IO) { dao.insertSegment(segment) }
        if (!isSessionActive) {
            withContext(Dispatchers.IO) { dao.updateSegmentStatus(segId, "ERROR_STOPPED_BEFORE_START") }
            return
        }

        cameraManager.startSegmentRecording(
            targetTmpFile = tmpFile,
            onError = { errorCode, cause ->
                android.util.Log.e("SegmenterEngine", "Hardware recording error on $segId: code $errorCode", cause)
                segmentRollJob?.cancel()
                activeFinalizeDeferred?.complete(Unit)
                scope.launch(Dispatchers.IO) {
                    try {
                        // Keep the row and any partial file as recovery evidence on every hardware error.
                        dao.updateSegmentStatus(segId, "ERROR_$errorCode")
                        // CameraX errors can indicate an encoder or storage failure. Preserve the
                        // partial file and end this session instead of immediately retrying blindly.
                        isSessionActive = false
                        storageMonitorJob?.cancel()
                        android.util.Log.e("SegmenterEngine", "Hardware capture failed; stopping session without automatic retry")
                    } catch (e: Exception) {
                        android.util.Log.e("SegmenterEngine", "Could not persist recording error state; stopping session", e)
                        isSessionActive = false
                    }
                    if (!isSessionActive) {
                        storageMonitorJob?.cancel()
                        // Leave the session marked RECORDING so the next worker can safely recover it.
                        RecordingForegroundService.markSessionInactive(sessionId)
                        RecordingForegroundService.stop(context)
                    }
                }
            },
            onFinalized = { finalizedFile, durationMs ->
                scope.launch(Dispatchers.IO) {
                    var committed = false
                    var sessionCanFinalize = false
                    try {
                        onSegmentHardwareFinalized(segId, sessionId, sequenceNumber, finalizedFile, durationMs)
                        committed = true
                        sessionCanFinalize = true
                    } catch (e: Exception) {
                        android.util.Log.e("SegmenterEngine", "Could not commit finalized segment $segId", e)
                        runCatching { dao.updateSegmentStatus(segId, "ERROR_FINALIZATION") }
                        activeFinalizeDeferred?.complete(Unit)
                        isSessionActive = false
                    }

                    if (committed && isSessionActive) {
                        withContext(Dispatchers.Main) {
                            try {
                                startNextSegment(cameraManager)
                            } catch (e: Exception) {
                                android.util.Log.e("SegmenterEngine", "Could not start the next recording segment", e)
                                isSessionActive = false
                                storageMonitorJob?.cancel()
                                if (e is RecordingStorageException) {
                                    sessionCanFinalize = true
                                    withContext(Dispatchers.Main) {
                                        android.widget.Toast.makeText(context, e.message, android.widget.Toast.LENGTH_LONG).show()
                                    }
                                } else {
                                    sessionCanFinalize = false
                                }
                            }
                        }
                    }

                    if (!isSessionActive) {
                        storageMonitorJob?.cancel()
                        try {
                            if (sessionCanFinalize) dao.updateSessionStatus(sessionId, sequenceNumber, "FINALIZED")
                        } catch (e: Exception) {
                            // Keep RECORDING in Room so upload recovery can inspect the committed files.
                            android.util.Log.e("SegmenterEngine", "Could not finalize session state", e)
                        } finally {
                            RecordingForegroundService.markSessionInactive(sessionId)
                            RecordingForegroundService.stop(context)
                        }
                    }
                }
            }
        )

        // Pause-aware roll ticker: elapsed time only accumulates while the hardware
        // recording is actually running, so a pause never shortens the segment.
        segmentRollJob?.cancel()
        segmentRollJob = scope.launch {
            var elapsedMs = 0L
            while (true) {
                delay(1_000L)
                if (!isSessionActive) return@launch
                if (isSessionPaused || cameraManager.isRecordingPaused) continue
                elapsedMs += 1_000L
                if (elapsedMs >= segmentDurationMs && cameraManager.isRecording) {
                    cameraManager.stopActiveRecording() // triggers onFinalized callback above
                    return@launch
                }
            }
        }
    }

    /** Pauses the active hardware recording and freezes the 10-minute segment roll clock. */
    fun pauseSession(cameraManager: CameraCaptureManager) {
        if (!isSessionActive || isSessionPaused) return
        isSessionPaused = true
        cameraManager.pauseActiveRecording()
    }

    /** Resumes a paused hardware recording and unfreezes the segment roll clock. */
    fun resumeSession(cameraManager: CameraCaptureManager) {
        if (!isSessionActive || !isSessionPaused) return
        if (!cameraManager.resumeActiveRecording()) return
        isSessionPaused = false
    }

    private suspend fun onSegmentHardwareFinalized(
        segId: String,
        sessId: String,
        seqNum: Int,
        tmpFile: File,
        durationMs: Long
    ) = withContext(Dispatchers.IO) {
        if (RecordingSafetyPolicy.isAlreadyCommittedSegmentStatus(dao.getSegmentById(segId)?.status)) {
            activeFinalizeDeferred?.complete(Unit)
            return@withContext
        }

        val finalMp4 = File(recordingsDir, "$segId.mp4")
        val mediaFile = RecordingSafetyPolicy.finalizedFileCandidate(tmpFile, finalMp4)
        if (mediaFile == null || durationMs < 1000L) {
            val failureStatus = when {
                mediaFile == null && !tmpFile.exists() && !finalMp4.exists() -> "FAILED_MISSING_FILE"
                mediaFile == null -> "FAILED_EMPTY"
                else -> "FAILED_TOO_SHORT"
            }
            // Preserve the database row; retain non-empty evidence for diagnosis/recovery.
            dao.updateSegmentStatus(segId, failureStatus)
            activeFinalizeDeferred?.complete(Unit)
            return@withContext
        }

        if (!isReadableVideo(mediaFile)) {
            android.util.Log.e("SegmenterEngine", "Finalized media is unreadable; preserving ${mediaFile.absolutePath}")
            dao.updateSegmentStatus(segId, "FAILED_CORRUPT")
            activeFinalizeDeferred?.complete(Unit)
            return@withContext
        }

        // CameraX may deliver a duplicate completion after a rename-before-Room interruption.
        // Reuse the already-promoted file instead of changing a queued row back to failed.
        if (mediaFile != finalMp4) {
            if (!mediaFile.renameTo(finalMp4)) {
                val expectedLength = mediaFile.length()
                mediaFile.copyTo(finalMp4, overwrite = true)
                if (!finalMp4.isFile || finalMp4.length() != expectedLength) {
                    throw java.io.IOException("Could not verify copied finalized segment $segId")
                }
                mediaFile.delete()
            }
        }

        val fileSize = finalMp4.length()
        val sha256 = calculateSha256(finalMp4)

        val now = System.currentTimeMillis()
        val updatedSegment = SegmentEntity(
            segmentId = segId,
            sessionId = sessId,
            sequenceNumber = seqNum,
            filename = finalMp4.name,
            filepath = finalMp4.absolutePath,
            fileSizeBytes = fileSize,
            sha256Hash = sha256,
            status = "QUEUED_FOR_UPLOAD",
            createdAt = now - durationMs,
            finalizedAt = now
        )
        // Commit the segment state and outbox row together so a process death cannot strand a file.
        database.withTransaction {
            dao.updateSegment(updatedSegment)
            dao.insertOutbox(
                OutboxEntity(
                    segmentId = segId,
                    sessionId = sessId,
                    remoteOffset = 0L,
                    status = "QUEUED_FOR_UPLOAD"
                )
            )
        }

        // Trigger WorkManager background sync immediately
        triggerYouTubeUpload()

        activeFinalizeDeferred?.complete(Unit)
    }

    suspend fun stopSession(cameraManager: CameraCaptureManager) = withContext(Dispatchers.Main) {
        isSessionActive = false
        isSessionPaused = false
        segmentRollJob?.cancel()
        segmentRollJob = null
        storageMonitorJob?.cancel()
        storageMonitorJob = null

        // Await active recording finalization to ensure tail segment is committed
        if (cameraManager.isRecording) {
            val deferred = CompletableDeferred<Unit>()
            activeFinalizeDeferred = deferred
            cameraManager.stopActiveRecording()
            withTimeoutOrNull(6000L) {
                deferred.await()
            }
            activeFinalizeDeferred = null
        }

        // Stop foreground service after finalization has been requested. The completion callback
        // marks the session FINALIZED and clears its recovery guard only after the file is committed.
        RecordingForegroundService.stop(context)

        withContext(Dispatchers.IO) {
            currentSessionId = null
            currentSegmentId = null
            currentTmpFile = null
        }
    }

    fun triggerYouTubeUpload() {
        val pairing = PairingManager(context)
        val networkType = if (pairing.isWifiOnlyEnabled) NetworkType.UNMETERED else NetworkType.CONNECTED
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(networkType)
            .build()

        // Recordings leave the phone only through the YouTube cloud inbox.
        val ytSyncWork = OneTimeWorkRequestBuilder<com.resolvia.dispatch.sync.YouTubeDirectUploadWorker>()
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            "dispatch_youtube_uploads",
            ExistingWorkPolicy.KEEP,
            ytSyncWork
        )
    }

    private fun calculateSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { fis ->
            val buffer = ByteArray(8192)
            var bytesRead: Int
            while (fis.read(buffer).also { bytesRead = it } != -1) {
                digest.update(buffer, 0, bytesRead)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun isReadableVideo(file: File): Boolean {
        if (!file.isFile || file.length() <= 0L) return false
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(file.absolutePath)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO) == "yes" &&
                (retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L) > 0L
        } catch (e: Exception) {
            android.util.Log.w("SegmenterEngine", "Could not read finalized recording ${file.name}: ${e.message}")
            false
        } finally {
            runCatching { retriever.release() }
        }
    }

    private fun availableRecordingBytes(): Long {
        if (!recordingsDir.isDirectory && !recordingsDir.mkdirs()) {
            throw RecordingStorageException("Recording storage is unavailable. Check the phone's storage and try again.")
        }
        return try {
            StatFs(recordingsDir.absolutePath).availableBytes
        } catch (e: Exception) {
            throw RecordingStorageException("Could not check recording storage. Recording was stopped to protect your files.", e)
        }
    }

    private fun requireRecordingStorage(): Long {
        return RecordingSafetyPolicy.requireAvailableSpace(availableRecordingBytes())
    }

    private fun startStorageMonitor(cameraManager: CameraCaptureManager) {
        storageMonitorJob?.cancel()
        storageMonitorJob = scope.launch {
            while (isSessionActive) {
                delay(15_000L)
                val issue = try {
                    val availableBytes = availableRecordingBytes()
                    if (RecordingSafetyPolicy.hasEnoughSpace(availableBytes)) null else {
                        val availableMb = availableBytes / (1024L * 1024L)
                        "Recording stopped to protect your footage: only $availableMb MB of free storage remains."
                    }
                } catch (e: RecordingStorageException) {
                    e.message ?: "Recording stopped because phone storage could not be checked."
                }
                if (issue != null && isSessionActive) {
                    isSessionActive = false
                    segmentRollJob?.cancel()
                    android.util.Log.e("SegmenterEngine", issue)
                    android.widget.Toast.makeText(context, issue, android.widget.Toast.LENGTH_LONG).show()
                    if (cameraManager.isRecording) {
                        cameraManager.stopActiveRecording()
                    } else {
                        currentSessionId?.let { RecordingForegroundService.markSessionInactive(it) }
                        RecordingForegroundService.stop(context)
                    }
                    break
                }
            }
        }
    }
}
