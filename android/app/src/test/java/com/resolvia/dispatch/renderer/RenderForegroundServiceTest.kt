package com.resolvia.dispatch.renderer

import com.resolvia.dispatch.data.RenderJobEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class RenderForegroundServiceTest {

    private fun makeJob(
        jobId: String,
        clipId: String,
        status: String,
        createdAt: Long = 1000L,
        updatedAt: Long = 1000L,
        sourceVideoPath: String = ""
    ) = RenderJobEntity(
        jobId = jobId,
        clipId = clipId,
        renderFingerprint = "fp_$jobId",
        projectDocumentJson = "{}",
        sourceVideoPath = sourceVideoPath,
        outputVideoPath = "/tmp/out_$jobId.mp4",
        status = status,
        createdAt = createdAt,
        updatedAt = updatedAt
    )

    @Test
    fun resolveWorkerJwt_rejectsPcAuthSecretAndInvalidTokens() {
        assertNull(RenderForegroundService.resolveWorkerJwt(null))
        assertNull(RenderForegroundService.resolveWorkerJwt(""))
        assertNull(RenderForegroundService.resolveWorkerJwt("   "))
        // Local PC LAN shared secrets are NOT 3-segment JWTs and must never be sent to Worker
        assertNull(RenderForegroundService.resolveWorkerJwt("dispatch_pc_shared_secret_token_987654321"))
        assertNull(RenderForegroundService.resolveWorkerJwt("header.payload"))

        val validJwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyJ9.signature_segment_123456"
        assertEquals(validJwt, RenderForegroundService.resolveWorkerJwt("  $validJwt  "))
    }

    @Test
    fun mintReservationClientToken_createsDistinctPerAttemptTokens() {
        val t1 = RenderForegroundService.mintReservationClientToken("render_job_42")
        val t2 = RenderForegroundService.mintReservationClientToken("render_job_42")
        assertTrue(t1.startsWith("render_render_job_42_"))
        assertTrue(t2.startsWith("render_render_job_42_"))
        assertNotEquals("Each reservation attempt must mint a fresh client_token", t1, t2)
    }

    @Test
    fun evaluatePublishQueueResponse_requires2xxAndNeverClaimsPublishedWhenQueued() {
        val okOutcome = RenderForegroundService.evaluatePublishQueueResponse(
            200,
            """{"ok": true, "job_id": "pub_1", "status": "queued"}"""
        )
        assertTrue(okOutcome.queued)
        assertEquals(RenderJobEntity.STATUS_PUBLISHING_QUEUED, okOutcome.statusToPersist)
        assertFalse(
            "Queued notification must never falsely notify Published!",
            okOutcome.notificationText.contains("Published!", ignoreCase = true)
        )
        assertTrue(okOutcome.notificationText.contains("Queued", ignoreCase = true))

        val serverErr = RenderForegroundService.evaluatePublishQueueResponse(
            502,
            """{"error": "publish enqueue failed"}"""
        )
        assertFalse(serverErr.queued)
        assertEquals(RenderJobEntity.STATUS_FAILED, serverErr.statusToPersist)
        assertEquals("publish enqueue failed", serverErr.errorMessage)

        val unauthErr = RenderForegroundService.evaluatePublishQueueResponse(401, """{"error": "unauthorized"}""")
        assertFalse(unauthErr.queued)
        assertEquals(RenderJobEntity.STATUS_FAILED, unauthErr.statusToPersist)

        val softFalse = RenderForegroundService.evaluatePublishQueueResponse(
            200,
            """{"ok": false, "error": "clip missing"}"""
        )
        assertFalse(softFalse.queued)
        assertEquals(RenderJobEntity.STATUS_FAILED, softFalse.statusToPersist)
        assertEquals("clip missing", softFalse.errorMessage)
    }

    @Test
    fun selectEligiblePendingRenderJobs_resumesAllPendingInOrderExcludingTerminalAndFailed() {
        val jobs = listOf(
            makeJob("j_done", "c1", RenderJobEntity.STATUS_PUBLISHING_QUEUED, createdAt = 100),
            makeJob("j_upload", "c2", RenderJobEntity.STATUS_UPLOADING, createdAt = 400),
            makeJob("j_queued", "c3", RenderJobEntity.STATUS_QUEUED, createdAt = 200),
            makeJob("j_failed", "c4", RenderJobEntity.STATUS_FAILED, createdAt = 150),
            makeJob("j_render_done", "c5", RenderJobEntity.STATUS_RENDER_COMPLETE, createdAt = 300),
            makeJob("j_upload_done", "c6", RenderJobEntity.STATUS_UPLOAD_COMPLETE, createdAt = 500),
            makeJob("j_rendering", "c7", RenderJobEntity.STATUS_RENDERING, createdAt = 250),
            makeJob("j_queued_dup", "c3", RenderJobEntity.STATUS_QUEUED, createdAt = 210),
            makeJob("j_queued", "c3", RenderJobEntity.STATUS_QUEUED, createdAt = 200)
        )

        val selected = RenderForegroundService.selectEligiblePendingRenderJobs(jobs)
        assertEquals(
            listOf("j_queued", "j_queued_dup", "j_rendering", "j_render_done", "j_upload", "j_upload_done"),
            selected.map { it.jobId }
        )
    }

    @Test
    fun reclaimUnusedSourceVideos_preservesActiveAndRetryableAndReclaimsCompletedOrOrphans() {
        val tempDir = Files.createTempDirectory("dispatch-source-reclaim-test").toFile()
        try {
            val now = 10_000_000_000L
            val activeFile = File(tempDir, "clip_active.mp4").apply { writeBytes(byteArrayOf(1, 2, 3)) }
            val retryableFailedFile = File(tempDir, "clip_retry.mp4").apply { writeBytes(byteArrayOf(4, 5, 6)) }
            val sharedClipFile = File(tempDir, "clip_shared.mp4").apply { writeBytes(byteArrayOf(7, 8)) }
            val completedFile = File(tempDir, "clip_done.mp4").apply { writeBytes(byteArrayOf(9, 10)) }
            val expiredOrphan = File(tempDir, "orphan_old.mp4").apply {
                writeBytes(byteArrayOf(11))
                setLastModified(now - RenderForegroundService.ORPHAN_SOURCE_MAX_AGE_MS - 10_000L)
            }
            val freshTmp = File(tempDir, "clip_downloading.mp4.tmp").apply {
                writeBytes(byteArrayOf(12))
                setLastModified(now - 5_000L)
            }

            val jobs = listOf(
                makeJob("j1", "clip_active", RenderJobEntity.STATUS_RENDERING, updatedAt = now - 1_000L),
                makeJob("j2", "clip_retry", RenderJobEntity.STATUS_FAILED, updatedAt = now - 60_000L),
                makeJob("j3a", "clip_shared", RenderJobEntity.STATUS_PUBLISHING_QUEUED, updatedAt = now - 1_000L),
                makeJob("j3b", "clip_shared", RenderJobEntity.STATUS_FAILED, updatedAt = now - 1_000L),
                makeJob("j4", "clip_done", RenderJobEntity.STATUS_PUBLISHING_QUEUED, updatedAt = now - 1_000L)
            )

            val result = RenderForegroundService.reclaimUnusedSourceVideos(
                sourceVideosDir = tempDir,
                jobs = jobs,
                completedClipId = "clip_done",
                nowMs = now
            )

            assertTrue("Active job source must be preserved", activeFile.exists())
            assertTrue("Retryable FAILED job source must be preserved", retryableFailedFile.exists())
            assertTrue("Shared clip source needed by retryable job must be preserved", sharedClipFile.exists())
            assertTrue("Fresh in-flight .tmp download must be preserved", freshTmp.exists())
            assertFalse("Completed job source with no active references must be reclaimed", completedFile.exists())
            assertFalse("Expired orphan source must be reclaimed", expiredOrphan.exists())
            assertEquals(2, result.deletedFiles.size)
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun renderJobQueue_queuesMultipleJobsSequentiallyWithoutCancellingOrDuplicating() {
        val queue = RenderForegroundService.RenderJobQueue()
        assertTrue(queue.enqueueAndShouldStartDrain(listOf("job1" to "clip1", "job2" to "clip2", "job3" to "clip3")))
        assertFalse("Duplicate jobId must not enqueue twice", queue.enqueue("job2", "clip2"))
        assertFalse("Already draining queue must not trigger second drain", queue.enqueueAndShouldStartDrain(listOf("job4" to "clip4")))

        val first = queue.pollNext()
        assertNotNull(first)
        assertEquals("job1", first!!.first)
        assertFalse("Active jobId must not re-enqueue while running", queue.enqueue("job1", "clip1"))

        // Cancel remaining queued job2 while job1 runs; job3 and job4 remain queued
        assertTrue(queue.removeQueued("job2"))
        assertEquals("job3", queue.pollNext()?.first)
        assertEquals("job4", queue.pollNext()?.first)
        assertNull(queue.pollNext())
        assertTrue(queue.finishDrainIfEmpty())
        assertFalse(queue.isDraining())
    }

    @Test
    fun renderJobQueue_activeCancelAndShutdownRacePreserveRemainingAndNewJobs() {
        val queue = RenderForegroundService.RenderJobQueue()
        assertTrue(queue.enqueueAndShouldStartDrain(listOf("job1" to "clip1", "job2" to "clip2")))

        // Poll job1 (active) while job2 sits queued
        assertEquals("job1", queue.pollNext()?.first)
        // Simulate active child cancellation: clearActive(job1) does NOT clear queue or stop drain
        queue.clearActive("job1")
        assertTrue(queue.isDraining())
        assertFalse("finishDrainIfEmpty must return false when job2 remains after cancel", queue.finishDrainIfEmpty())
        assertEquals("job2", queue.pollNext()?.first)
        queue.clearActive("job2")

        // Simulate shutdown race: queue drained empty on IO, then ACTION_START_RENDER enqueues job3
        // right before Main thread runs finishDrainIfEmpty()/stopForegroundService()
        assertNull(queue.pollNext())
        assertFalse(
            "Enqueueing during active drain cleanup must keep drain alive without starting second drain",
            queue.enqueueAndShouldStartDrain(listOf("job3" to "clip3"))
        )
        assertFalse(
            "finishDrainIfEmpty must refuse shutdown when job3 arrived during cleanup window",
            queue.finishDrainIfEmpty()
        )
        assertTrue(queue.isDraining())

        // Drain picks up job3 and only stops once queue is genuinely empty
        assertEquals("job3", queue.pollNext()?.first)
        queue.clearActive("job3")
        assertNull(queue.pollNext())
        assertTrue(queue.finishDrainIfEmpty())
        assertFalse(queue.isDraining())
    }
}
