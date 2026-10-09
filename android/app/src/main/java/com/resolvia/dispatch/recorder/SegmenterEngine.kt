package com.resolvia.dispatch.recorder

import android.content.Context
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
    private val recordingsDir: File = File(context.getExternalFilesDir(null), "recordings").apply {
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

    // Default 10-minute rolling segments (600,000 ms)
    var segmentDurationMs: Long = 10 * 60 * 1000L

    private var segmentRollJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var activeFinalizeDeferred: CompletableDeferred<Unit>? = null

    suspend fun startSession(
        cameraManager: CameraCaptureManager,
        notes: String = ""
    ): String = withContext(Dispatchers.IO) {
        val sessionId = "sess_${System.currentTimeMillis()}"
        val session = SessionEntity(sessionId = sessionId, notes = notes)
        dao.insertSession(session)

        currentSessionId = sessionId
        currentSequenceNumber = 0
        isSessionActive = true

        // Bind CameraX directly to Foreground Service lifecycle so recording survives screen lock
        val bound = withTimeoutOrNull(10000L) {
            RecordingForegroundService.startAndAwaitBind(context, cameraManager)
        } ?: false

        if (!bound) {
            isSessionActive = false
            throw IllegalStateException("Failed to bind camera capture to foreground service")
        }

        withContext(Dispatchers.Main) {
            startNextSegment(cameraManager)
        }

        sessionId
    }

    fun startNextSegment(cameraManager: CameraCaptureManager) {
        if (!isSessionActive) return
        val sessionId = currentSessionId ?: return

        currentSequenceNumber++
        val segId = String.format("%s_seg_%04d", sessionId, currentSequenceNumber)
        val tmpFile = File(recordingsDir, "$segId.tmp")

        currentSegmentId = segId
        currentTmpFile = tmpFile

        scope.launch(Dispatchers.IO) {
            val segment = SegmentEntity(
                segmentId = segId,
                sessionId = sessionId,
                sequenceNumber = currentSequenceNumber,
                filename = "$segId.mp4",
                filepath = File(recordingsDir, "$segId.mp4").absolutePath,
                status = "RECORDING"
            )
            dao.insertSegment(segment)
        }

        cameraManager.startSegmentRecording(
            targetTmpFile = tmpFile,
            onError = { errorCode, cause ->
                android.util.Log.e("SegmenterEngine", "Hardware recording error on $segId: code $errorCode", cause)
                segmentRollJob?.cancel()
                activeFinalizeDeferred?.complete(Unit)
                scope.launch(Dispatchers.IO) {
                    if (errorCode == -2) {
                        dao.deleteSegment(segId)
                    } else {
                        dao.updateSegmentStatus(segId, "ERROR_$errorCode")
                    }
                    if (errorCode == -1) {
                        // Uninitialized hardware: abort session instead of infinite error loop
                        isSessionActive = false
                        android.util.Log.e("SegmenterEngine", "Hardware capture uninitialized; aborting session")
                    } else if (isSessionActive) {
                        withContext(Dispatchers.Main) {
                            startNextSegment(cameraManager)
                        }
                    }
                }
            },
            onFinalized = { finalizedFile, durationMs ->
                val seq = currentSequenceNumber
                scope.launch(Dispatchers.IO) {
                    onSegmentHardwareFinalized(segId, sessionId, seq, finalizedFile, durationMs)
                    if (isSessionActive) {
                        withContext(Dispatchers.Main) {
                            startNextSegment(cameraManager)
                        }
                    }
                }
            }
        )

        // Schedule timer to roll into next segment
        segmentRollJob?.cancel()
        segmentRollJob = scope.launch {
            delay(segmentDurationMs)
            if (isSessionActive && cameraManager.isRecording) {
                cameraManager.stopActiveRecording() // triggers onFinalized callback above
            }
        }
    }

    private suspend fun onSegmentHardwareFinalized(
        segId: String,
        sessId: String,
        seqNum: Int,
        tmpFile: File,
        durationMs: Long
    ) = withContext(Dispatchers.IO) {
        if (!tmpFile.exists() || tmpFile.length() == 0L || durationMs < 1000L) {
            tmpFile.delete()
            dao.deleteSegment(segId)
            activeFinalizeDeferred?.complete(Unit)
            return@withContext
        }

        val finalMp4 = File(recordingsDir, "$segId.mp4")
        val fileSize = tmpFile.length()
        val sha256 = calculateSha256(tmpFile)

        // Atomic commit: .tmp -> .mp4 with safe copy fallback
        val renamed = tmpFile.renameTo(finalMp4)
        if (!renamed) {
            tmpFile.copyTo(finalMp4, overwrite = true)
            tmpFile.delete()
        }

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
        dao.updateSegment(updatedSegment)

        // Durable Outbox Entry
        dao.insertOutbox(
            OutboxEntity(
                segmentId = segId,
                sessionId = sessId,
                remoteOffset = 0L,
                status = "QUEUED_FOR_UPLOAD"
            )
        )

        // Trigger WorkManager background sync immediately
        triggerYouTubeUpload()

        activeFinalizeDeferred?.complete(Unit)
    }

    suspend fun stopSession(cameraManager: CameraCaptureManager) = withContext(Dispatchers.Main) {
        isSessionActive = false
        segmentRollJob?.cancel()
        segmentRollJob = null

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

        // Stop foreground service after final segment is safely committed
        RecordingForegroundService.stop(context)

        withContext(Dispatchers.IO) {
            val sessId = currentSessionId ?: return@withContext
            val session = SessionEntity(
                sessionId = sessId,
                totalSegments = currentSequenceNumber,
                status = "FINALIZED"
            )
            dao.updateSession(session)
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
            "DispatchYouTubeUploadWorker",
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
}
