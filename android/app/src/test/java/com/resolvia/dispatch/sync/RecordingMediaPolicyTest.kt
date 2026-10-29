package com.resolvia.dispatch.sync

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Regression coverage for the `/recordings/` WebView media handler's pure
 * policies (§5): path containment, finalized-file enforcement, MIME types,
 * and byte-range arithmetic used for seek/scrub.
 */
class RecordingMediaPolicyTest {

    private val tempDirectory = Files.createTempDirectory("dispatch-media-test").toFile()

    private fun newRoot(): File {
        val root = File(tempDirectory, "recordings-" + java.util.UUID.randomUUID())
        root.mkdirs()
        root.canonicalFile
        return root
    }

    @Test
    fun mimeTypeIsCorrectPerMediaType() {
        assertEquals("video/mp4", RecordingMediaPolicy.mediaMimeFor("seg_1.mp4"))
        assertEquals("image/jpeg", RecordingMediaPolicy.mediaMimeFor("seg_1.jpg"))
        assertEquals("image/png", RecordingMediaPolicy.mediaMimeFor("seg_1.png"))
        assertEquals("application/octet-stream", RecordingMediaPolicy.mediaMimeFor("seg_1.bin"))
    }

    @Test
    fun finalizedFileInsideRootIsResolvable() {
        val ext = newRoot()
        val internal = newRoot()
        val segment = File(ext, "seg_final.mp4").apply { writeBytes(byteArrayOf(1, 2, 3)) }

        val resolved = RecordingMediaPolicy.resolveRecordingPath(
            java.net.URLEncoder.encode("seg_final.mp4", "UTF-8"), ext, internal
        )
        assertEquals(segment.canonicalFile, resolved?.canonicalFile)
    }

    @Test
    fun internalRootFallbackIsResolvable() {
        val ext = newRoot()
        val internal = newRoot()
        File(internal, "seg_int.mp4").writeBytes(byteArrayOf(9))

        // Both recording roots are searched, order is ext first then internal.
        val resolved = RecordingMediaPolicy.resolveRecordingPath("seg_int.mp4", ext, internal)
        assertEquals(File(internal, "seg_int.mp4").canonicalFile, resolved?.canonicalFile)
    }

    @Test
    fun missingFilesAreNotResolvable() {
        val ext = newRoot()
        val internal = newRoot()
        assertNull(RecordingMediaPolicy.resolveRecordingPath("ghost.mp4", ext, internal))
    }

    @Test
    fun traversalAndSubdirectoryAccessIsRejected() {
        val ext = newRoot()
        val internal = newRoot()
        val nested = File(ext, "nested").apply { mkdirs() }
        File(nested, "secret.mp4").writeBytes(byteArrayOf(1))
        File(tempDirectory, "outside_secret.mp4").writeBytes(byteArrayOf(2))

        assertNull(RecordingMediaPolicy.resolveRecordingPath("../outside_secret.mp4", ext, internal))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("nested/secret.mp4", ext, internal))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("nested%2Fsecret.mp4", ext, internal))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("..%5Coutside_secret.mp4", ext, internal))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("nested:secret", ext, internal))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("", ext, internal))
    }

    @Test
    fun inFlightTemporaryFilesAreRefused() {
        val ext = newRoot()
        val internal = newRoot()
        File(ext, "seg_1.tmp").writeBytes(byteArrayOf(1, 2))
        assertNull(RecordingMediaPolicy.resolveRecordingPath("seg_1.tmp", ext, internal))
    }

    @Test
    fun openEndedByteRangeRunsToFileEnd() {
        val fileLength = 1_000_000L
        assertEquals(
            ByteRangeResult.Satisfiable(0L, fileLength - 1),
            RecordingMediaPolicy.resolveRange("bytes=0-", fileLength)
        )
        assertEquals(
            ByteRangeResult.Satisfiable(500L, fileLength - 1),
            RecordingMediaPolicy.resolveRange("bytes=500-", fileLength)
        )
    }

    @Test
    fun boundedByteRangeIsRespectedAndClamped() {
        val fileLength = 1000L
        assertEquals(ByteRangeResult.Satisfiable(100L, 299L), RecordingMediaPolicy.resolveRange("bytes=100-299", fileLength))
        // Over-long end is clamped to the file end.
        assertEquals(ByteRangeResult.Satisfiable(100L, 999L), RecordingMediaPolicy.resolveRange("bytes=100-9999", fileLength))
    }

    @Test
    fun reversedOrOutOfBoundsRangeIsUnsatisfiable() {
        val fileLength = 1000L
        // RFC 7233: an unsatisfiable range is a 416, never a silent full-file read.
        assertEquals(ByteRangeResult.Unsatisfiable, RecordingMediaPolicy.resolveRange("bytes=500-100", fileLength))
        assertEquals(ByteRangeResult.Unsatisfiable, RecordingMediaPolicy.resolveRange("bytes=1000-", fileLength))
        assertEquals(ByteRangeResult.Unsatisfiable, RecordingMediaPolicy.resolveRange("bytes=-0", fileLength))
    }

    @Test
    fun suffixRangeMeansTheLastNBytes() {
        val fileLength = 1000L
        // Previously treated as 0..N, which served the wrong bytes entirely.
        assertEquals(ByteRangeResult.Satisfiable(900L, 999L), RecordingMediaPolicy.resolveRange("bytes=-100", fileLength))
        assertEquals(ByteRangeResult.Satisfiable(0L, 999L), RecordingMediaPolicy.resolveRange("bytes=-5000", fileLength))
    }

    @Test
    fun absentOrInvalidRangeHeaderFallsBackToFullResponse() {
        val fileLength = 1000L
        assertNull(RecordingMediaPolicy.resolveRange(null, fileLength))
        assertNull(RecordingMediaPolicy.resolveRange("items=0-5", fileLength))
        assertNull(RecordingMediaPolicy.resolveRange("bytes=-", 0L)) // empty file: serve 200
        assertNull(RecordingMediaPolicy.resolveRange("bytes=0-1,5-9", fileLength)) // multi-range: serve 200
        assertNull(RecordingMediaPolicy.resolveRange("bytes=abc-def", fileLength))
    }
}
