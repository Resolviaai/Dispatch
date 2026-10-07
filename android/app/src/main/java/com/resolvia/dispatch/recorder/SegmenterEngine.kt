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

        // Start Android Foreground Service for camera/mic immunity
        RecordingForegroundService.start(context)

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

        // Start hardware CameraX recording
        cameraManager.startSegmentRecording(
            targetTmpFile = tmpFile,
            onError = { errorCode, cause ->
                android.util.Log.e("SegmenterEngine", "Hardware recording error on $segId: code $errorCode", cause)
                scope.launch(Dispatchers.IO) {
                    dao.updateSegmentStatus(segId, "ERROR_$errorCode")
                }
            },
            onFinalized = { finalizedFile, durationMs ->
                scope.launch(Dispatchers.IO) {
                    onSegmentHardwareFinalized(segId, sessionId, finalizedFile, durationMs)
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
        tmpFile: File,
        durationMs: Long
    ) = withContext(Dispatchers.IO) {
        if (!tmpFile.exists() || tmpFile.length() == 0L) {
            tmpFile.delete()
            return@withContext
        }

        val finalMp4 = File(recordingsDir, "$segId.mp4")
        val fileSize = tmpFile.length()
        val sha256 = calculateSha256(tmpFile)

        // Atomic commit: .tmp -> .mp4
        tmpFile.renameTo(finalMp4)

        val updatedSegment = SegmentEntity(
            segmentId = segId,
            sessionId = sessId,
            sequenceNumber = currentSequenceNumber,
            filename = finalMp4.name,
            filepath = finalMp4.absolutePath,
            fileSizeBytes = fileSize,
            sha256Hash = sha256,
            status = "QUEUED_FOR_UPLOAD",
            finalizedAt = System.currentTimeMillis()
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
    }

    suspend fun stopSession(cameraManager: CameraCaptureManager) = withContext(Dispatchers.Main) {
        isSessionActive = false
        segmentRollJob?.cancel()
        segmentRollJob = null

        // Stop camera and foreground service
        cameraManager.stopActiveRecording()
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
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
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
