package com.resolvia.dispatch.recorder

import java.nio.file.Files
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class RecordingSafetyPolicyTest {
    private val tempDirectory = Files.createTempDirectory("dispatch-recording-policy").toFile()

    @After
    fun cleanUp() {
        tempDirectory.deleteRecursively()
    }

    @Test
    fun erroredAndShortNonEmptySegmentsArePreserved() {
        assertEquals(
            RecordingFinalizeDisposition.PRESERVE_AND_REPORT_ERROR,
            RecordingSafetyPolicy.finalizeDisposition(hasCameraError = true, fileLength = 512, durationMs = 30_000)
        )
        assertEquals(
            RecordingFinalizeDisposition.PRESERVE_AND_REPORT_ERROR,
            RecordingSafetyPolicy.finalizeDisposition(hasCameraError = false, fileLength = 512, durationMs = 500)
        )
    }

    @Test
    fun onlyEmptySuccessfulFinalizationIsDisposable() {
        assertEquals(
            RecordingFinalizeDisposition.DELETE_EMPTY_AND_REPORT_ERROR,
            RecordingSafetyPolicy.finalizeDisposition(hasCameraError = false, fileLength = 0, durationMs = 5_000)
        )
        assertEquals(
            RecordingFinalizeDisposition.COMMIT,
            RecordingSafetyPolicy.finalizeDisposition(hasCameraError = false, fileLength = 512, durationMs = 5_000)
        )
    }

    @Test
    fun storageReserveAndDuplicateFinalizeUseProductionPolicy() {
        assertFalse(RecordingSafetyPolicy.hasEnoughSpace(RecordingSafetyPolicy.MINIMUM_FREE_BYTES - 1))
        assertTrue(RecordingSafetyPolicy.hasEnoughSpace(RecordingSafetyPolicy.MINIMUM_FREE_BYTES))
        assertThrows(RecordingStorageException::class.java) { RecordingSafetyPolicy.requireAvailableSpace(null) }
        assertThrows(RecordingStorageException::class.java) {
            RecordingSafetyPolicy.requireAvailableSpace(RecordingSafetyPolicy.MINIMUM_FREE_BYTES - 1)
        }
        assertEquals(RecordingSafetyPolicy.MINIMUM_FREE_BYTES, RecordingSafetyPolicy.requireAvailableSpace(RecordingSafetyPolicy.MINIMUM_FREE_BYTES))
        assertTrue(RecordingSafetyPolicy.isAlreadyCommittedSegmentStatus("QUEUED_FOR_UPLOAD"))
        assertTrue(RecordingSafetyPolicy.isAlreadyCommittedSegmentStatus("FAILED_CORRUPT"))
        assertFalse(RecordingSafetyPolicy.isAlreadyCommittedSegmentStatus("RECORDING"))
    }

    @Test
    fun finalizedCommitPrefersNonEmptyTempThenExistingFinalFile() {
        val tmp = tempDirectory.resolve("segment.tmp")
        val final = tempDirectory.resolve("segment.mp4")
        assertNull(RecordingSafetyPolicy.finalizedFileCandidate(tmp, final))

        final.writeBytes(byteArrayOf(4))
        assertEquals(final, RecordingSafetyPolicy.finalizedFileCandidate(tmp, final))

        tmp.writeBytes(byteArrayOf(1, 2, 3))
        assertEquals(tmp, RecordingSafetyPolicy.finalizedFileCandidate(tmp, final))
    }
}
