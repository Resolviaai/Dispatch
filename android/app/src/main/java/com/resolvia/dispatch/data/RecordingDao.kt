package com.resolvia.dispatch.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface RecordingDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSession(session: SessionEntity)

    @Update
    suspend fun updateSession(session: SessionEntity)

    @Query("SELECT * FROM sessions WHERE status = 'RECORDING'")
    suspend fun getInterruptedSessions(): List<SessionEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSegment(segment: SegmentEntity)

    @Update
    suspend fun updateSegment(segment: SegmentEntity)

    @Query("SELECT * FROM segments WHERE sessionId = :sessionId AND status = 'RECORDING'")
    suspend fun getUnfinalizedSegments(sessionId: String): List<SegmentEntity>

    @Query("SELECT * FROM segments WHERE status IN ('QUEUED_FOR_UPLOAD', 'FAILED_RETRY') ORDER BY sequenceNumber ASC")
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

    @Query("SELECT * FROM segments ORDER BY createdAt DESC LIMIT 50")
    suspend fun getAllSegmentsList(): List<SegmentEntity>

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

    @Query("DELETE FROM outbox WHERE segmentId = :segmentId")
    suspend fun deleteOutboxItem(segmentId: String)

    @Query("DELETE FROM segments WHERE segmentId = :segmentId")
    suspend fun deleteSegment(segmentId: String)
}
