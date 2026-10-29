package com.resolvia.dispatch.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface RecordingDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSession(session: SessionEntity)

    @Update
    suspend fun updateSession(session: SessionEntity)

    @Query("UPDATE sessions SET totalSegments = :totalSegments, status = :status WHERE sessionId = :sessionId")
    suspend fun updateSessionStatus(sessionId: String, totalSegments: Int, status: String)

    @Query("SELECT * FROM sessions WHERE status = 'RECORDING'")
    suspend fun getInterruptedSessions(): List<SessionEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSegment(segment: SegmentEntity)

    @Update
    suspend fun updateSegment(segment: SegmentEntity)

    @Query("SELECT * FROM segments WHERE sessionId = :sessionId AND (status = 'RECORDING' OR status LIKE 'ERROR_%')")
    suspend fun getUnfinalizedSegments(sessionId: String): List<SegmentEntity>

    @Query("SELECT * FROM segments WHERE status IN ('QUEUED_FOR_UPLOAD', 'FAILED_RETRY', 'WAITING_FOR_YOUTUBE') ORDER BY sequenceNumber ASC")
    suspend fun getPendingUploadSegments(): List<SegmentEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertOutbox(outbox: OutboxEntity)

    @Update
    suspend fun updateOutbox(outbox: OutboxEntity)

    @Query("SELECT * FROM outbox WHERE status = 'QUEUED_FOR_UPLOAD' ORDER BY updatedAt ASC")
    suspend fun getPendingOutboxItems(): List<OutboxEntity>

    @Query("SELECT COUNT(*) FROM outbox WHERE status = 'QUEUED_FOR_UPLOAD'")
    fun getPendingOutboxCountFlow(): Flow<Int>

    @Query("SELECT * FROM segments ORDER BY createdAt DESC LIMIT 25")
    fun getAllSegmentsFlow(): Flow<List<SegmentEntity>>

    @Query("SELECT * FROM segments WHERE sessionId = :sessionId ORDER BY sequenceNumber ASC")
    suspend fun getSegmentsBySessionId(sessionId: String): List<SegmentEntity>

    @Query("SELECT * FROM segments ORDER BY createdAt DESC LIMIT 500")
    suspend fun getAllSegmentsList(): List<SegmentEntity>

    @Query("SELECT * FROM segments WHERE (userId = :userId OR userId IS NULL OR userId = '') ORDER BY createdAt DESC LIMIT 500")
    suspend fun getSegmentsForUser(userId: String): List<SegmentEntity>

    @Query("DELETE FROM segments WHERE userId IS NULL OR userId = ''")
    suspend fun deleteLegacyUnownedSegments()

    @Query("DELETE FROM sessions WHERE userId IS NULL OR userId = ''")
    suspend fun deleteLegacyUnownedSessions()

    @Query("UPDATE segments SET status = :status WHERE segmentId = :segmentId")
    suspend fun updateSegmentStatus(segmentId: String, status: String)

    @Query("UPDATE segments SET status = :status, youtubeVideoId = :youtubeVideoId WHERE segmentId = :segmentId")
    suspend fun updateSegmentStatusAndVideoId(segmentId: String, status: String, youtubeVideoId: String?)

    @Query("SELECT * FROM segments WHERE segmentId = :segmentId LIMIT 1")
    suspend fun getSegmentById(segmentId: String): SegmentEntity?

    @Query("UPDATE outbox SET remoteOffset = :offset, updatedAt = :updatedAt WHERE segmentId = :segmentId")
    suspend fun updateOutboxOffset(segmentId: String, offset: Long, updatedAt: Long = System.currentTimeMillis())

    @Query("SELECT remoteOffset FROM outbox WHERE segmentId = :segmentId")
    suspend fun getOutboxOffset(segmentId: String): Long?

    @Query("SELECT segmentId, remoteOffset, status, lastError FROM outbox")
    suspend fun getAllOutboxProgress(): List<OutboxProgress>

    @Query("DELETE FROM outbox WHERE segmentId = :segmentId")
    suspend fun deleteOutboxItem(segmentId: String)

    @Query("DELETE FROM segments WHERE segmentId = :segmentId")
    suspend fun deleteSegment(segmentId: String)

    @Query("DELETE FROM segments WHERE sessionId = :sessionId")
    suspend fun deleteSegmentsBySessionId(sessionId: String)

    /**
     * Freeze queued/failed segments before local deletion so the upload worker
     * cannot claim them. A fresh UPLOADING lease is left untouched.
     */
    @Query("""
        UPDATE segments SET status = 'DELETING', uploadLeaseAt = NULL
        WHERE sessionId = :sessionId
          AND (status != 'UPLOADING' OR uploadLeaseAt IS NULL OR uploadLeaseAt < :staleBeforeMs)
    """)
    suspend fun markSessionSegmentsDeleting(sessionId: String, staleBeforeMs: Long): Int

    @Query("DELETE FROM sessions WHERE sessionId = :sessionId")
    suspend fun deleteSession(sessionId: String)

    @Query("SELECT * FROM segments WHERE status IN ('UPLOADED_TO_YOUTUBE', 'PROCESSED_ON_YOUTUBE', 'UPLOADED_AWAITING_PROCESSING') AND status != 'VERIFIED_BY_LAPTOP' ORDER BY sequenceNumber ASC")
    suspend fun getSegmentsAwaitingPcAck(): List<SegmentEntity>

    /** Segments uploaded and waiting for YouTube processing to reach a verdict. */
    @Query("SELECT * FROM segments WHERE (status = 'UPLOADED_AWAITING_PROCESSING' AND youtubeVideoId IS NOT NULL AND youtubeVideoId != '') OR status = 'UPLOADED_TO_YOUTUBE' ORDER BY youtubeCheckAttempts ASC")
    suspend fun getSegmentsUploadedAwaitingProcessing(): List<SegmentEntity>

    /** Requeues segments whose UPLOADING lease expired (the owning worker died mid-upload). */
    @Query("UPDATE segments SET status = 'FAILED_RETRY', uploadLeaseAt = NULL WHERE status = 'UPLOADING' AND uploadLeaseAt IS NOT NULL AND uploadLeaseAt < :staleBeforeMs")
    suspend fun requeueStaleUploading(staleBeforeMs: Long): Int

    /** Atomic claim: succeeds only while the segment is still in a pending state. */
    @Query("UPDATE segments SET status = 'UPLOADING', uploadLeaseAt = :nowMs WHERE segmentId = :segmentId AND status IN ('QUEUED_FOR_UPLOAD', 'FAILED_RETRY', 'WAITING_FOR_YOUTUBE')")
    suspend fun claimForUpload(segmentId: String, nowMs: Long): Int

    /** Renew the lease while a long upload is still in progress. */
    @Query("UPDATE segments SET uploadLeaseAt = :nowMs WHERE segmentId = :segmentId AND status = 'UPLOADING'")
    suspend fun renewUploadLease(segmentId: String, nowMs: Long): Int

    @Query("UPDATE segments SET youtubeCheckAttempts = :attempts, youtubeCheckAt = :checkedAt WHERE segmentId = :segmentId")
    suspend fun updateProcessingCheck(segmentId: String, attempts: Int, checkedAt: Long)

    @Query("UPDATE segments SET uploadOrigin = :origin WHERE segmentId = :segmentId")
    suspend fun updateSegmentUploadOrigin(segmentId: String, origin: String)
}
