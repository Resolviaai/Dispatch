package com.resolvia.dispatch.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfirmedUploadProgressTest {
    @Test
    fun acceptsOnlyConfirmedOffsetsAndRewindsStaleLocalProgress() {
        val progress = ConfirmedUploadProgress(initialOffset = 800, totalBytes = 1_000)

        assertTrue(progress.observe(400))
        assertEquals(400L, progress.offset)
        assertEquals(0, progress.consecutiveNoProgress)
    }

    @Test
    fun repeatedOffsetAndAmbiguousStatusExhaustTheBoundedRetryBudget() {
        val progress = ConfirmedUploadProgress(initialOffset = 400, totalBytes = 1_000)

        repeat(ConfirmedUploadProgress.MAX_NO_PROGRESS_ATTEMPTS - 1) {
            assertFalse(progress.observe(400))
            assertFalse(progress.retryExhausted)
        }
        assertFalse(progress.observe(null))
        assertTrue(progress.retryExhausted)
    }

    @Test
    fun retryDelayIsCappedAndIncludesOnlyBoundedJitter() {
        val progress = ConfirmedUploadProgress(initialOffset = 0, totalBytes = 1_000)
        repeat(ConfirmedUploadProgress.MAX_NO_PROGRESS_ATTEMPTS) { progress.observe(0) }

        assertEquals(8_500L, progress.retryDelayMs(ConfirmedUploadProgress.MAX_JITTER_MS))
        assertEquals(8_000L, progress.retryDelayMs(-1L))
    }
}
