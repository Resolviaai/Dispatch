package com.resolvia.dispatch.sync

import java.nio.file.Files
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RecordingRecoveryPolicyTest {
    private val tempDirectory = Files.createTempDirectory("dispatch-recovery-test").toFile()

    @After
    fun cleanUp() {
        tempDirectory.deleteRecursively()
    }

    @Test
    fun liveSessionIsExcludedFromRecovery() {
        assertFalse(shouldRecoverRecordingSession("session-live", "session-live"))
        assertTrue(shouldRecoverRecordingSession("session-old", "session-live"))
        assertTrue(shouldRecoverRecordingSession("session-old", null))
    }

    @Test
    fun finalizedFileCanRecoverAfterRenameBeforeRoomCommit() {
        val tmp = tempDirectory.resolve("segment.tmp")
        val finalized = tempDirectory.resolve("segment.mp4")
        finalized.writeBytes(byteArrayOf(1, 2, 3))

        assertEquals(finalized, findRecoveryCandidate(tmp, finalized))
    }

    @Test
    fun nonEmptyTemporaryFileIsPreferredAndEmptyFilesAreNotRecoverable() {
        val tmp = tempDirectory.resolve("segment.tmp")
        val finalized = tempDirectory.resolve("segment.mp4")
        finalized.writeBytes(byteArrayOf(4))
        tmp.writeBytes(byteArrayOf(1, 2))

        assertEquals(tmp, findRecoveryCandidate(tmp, finalized))

        tmp.writeBytes(byteArrayOf())
        finalized.writeBytes(byteArrayOf())
        assertNull(findRecoveryCandidate(tmp, finalized))
    }
}
