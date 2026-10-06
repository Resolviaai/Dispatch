package com.resolvia.dispatch.recorder

import android.content.Context
import com.resolvia.dispatch.data.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest

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

    suspend fun startSession(notes: String = ""): String = withContext(Dispatchers.IO) {
        val sessionId = "sess_${System.currentTimeMillis()}"
        val session = SessionEntity(sessionId = sessionId, notes = notes)
        dao.insertSession(session)
        currentSessionId = sessionId
        currentSequenceNumber = 0
        startNextSegment()
        sessionId
    }

    suspend fun startNextSegment(): File = withContext(Dispatchers.IO) {
        val sessionId = currentSessionId ?: throw IllegalStateException("No active session")

        // Finalize any ongoing segment first
        if (currentTmpFile != null && currentTmpFile!!.exists()) {
            finalizeCurrentSegment()
        }

        currentSequenceNumber++
        val segId = String.format("%s_seg_%04d", sessionId, currentSequenceNumber)
        val tmpFile = File(recordingsDir, "$segId.tmp")
        tmpFile.createNewFile()

        val segment = SegmentEntity(
            segmentId = segId,
            sessionId = sessionId,
            sequenceNumber = currentSequenceNumber,
            filename = "$segId.mp4",
            filepath = File(recordingsDir, "$segId.mp4").absolutePath,
            status = "RECORDING"
        )
        dao.insertSegment(segment)

        currentSegmentId = segId
        currentTmpFile = tmpFile
        tmpFile
    }

    suspend fun finalizeCurrentSegment(): SegmentEntity? = withContext(Dispatchers.IO) {
        val segId = currentSegmentId ?: return@withContext null
        val tmpFile = currentTmpFile ?: return@withContext null
        val sessId = currentSessionId ?: return@withContext null

        if (!tmpFile.exists() || tmpFile.length() == 0L) {
            tmpFile.delete()
            return@withContext null
        }

        val finalMp4 = File(recordingsDir, "$segId.mp4")
        val fileSize = tmpFile.length()
        val sha256 = calculateSha256(tmpFile)

        // Atomic file finalization: .tmp -> .mp4
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

        // Add to durable outbox
        dao.insertOutbox(
            OutboxEntity(
                segmentId = segId,
                sessionId = sessId,
                remoteOffset = 0L,
                status = "QUEUED_FOR_UPLOAD"
            )
        )

        currentSegmentId = null
        currentTmpFile = null
        updatedSegment
    }

    suspend fun stopSession() = withContext(Dispatchers.IO) {
        val sessId = currentSessionId ?: return@withContext
        finalizeCurrentSegment()

        val session = SessionEntity(
            sessionId = sessId,
            totalSegments = currentSequenceNumber,
            status = "FINALIZED"
        )
        dao.updateSession(session)
        currentSessionId = null
    }

    private fun calculateSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { fis ->
            val buffer = ByteArray(65536)
            var read: Int
            while (fis.read(buffer).also { read = it } != -1) {
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
