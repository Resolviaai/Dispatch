package com.resolvia.dispatch.media

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AutoCutEngineTest {

    @Test
    fun resolveExtractorRange_appliesClipStartOffsetForTrimmedClipRelativeMedia() {
        // Trimmed 12s clip file representing source window [30.0s, 42.0s]
        val range = AutoCutEngine.resolveExtractorRange(
            clipStartSec = 30.0,
            clipEndSec = 42.0,
            mediaDurationUs = 12_000_000L
        )
        assertTrue(range.isClipRelativeMedia)
        assertEquals(0L, range.seekStartUs)
        assertEquals(12_000_000L, range.endLimitUs)
        assertEquals(30.0, range.sourceOffsetSec, 0.0001)

        // Source timestamp 35.5s maps cleanly to 5.5s inside the extracted clip file
        assertEquals(5_500_000L, AutoCutEngine.mapSourceTimeToExtractorSampleUs(35.5, range))
    }

    @Test
    fun resolveExtractorRange_fullSourceFileSeeksToClipStartAndKeepsSourceCoords() {
        val range = AutoCutEngine.resolveExtractorRange(
            clipStartSec = 30.0,
            clipEndSec = 42.0,
            mediaDurationUs = 90_000_000L
        )
        assertFalse(range.isClipRelativeMedia)
        assertEquals(30_000_000L, range.seekStartUs)
        assertEquals(42_000_000L, range.endLimitUs)
        assertEquals(0.0, range.sourceOffsetSec, 0.0001)

        // Source timestamp 35.5s stays 35.5s in full-source MediaExtractor coordinates
        assertEquals(35_500_000L, AutoCutEngine.mapSourceTimeToExtractorSampleUs(35.5, range))
    }

    @Test
    fun mapCutIntervalsAndBuildKeepSegments_identicalBetweenClipAndSourceRelative() {
        val clipRelativeCuts = listOf(
            AutoCutEngine.CutInterval(2.0, 4.0),
            AutoCutEngine.CutInterval(7.0, 8.5)
        )
        val sourceRelativeCuts = listOf(
            AutoCutEngine.CutInterval(12.0, 14.0),
            AutoCutEngine.CutInterval(17.0, 18.5)
        )

        val keepFromClip = AutoCutEngine.buildKeepSegments(
            cuts = clipRelativeCuts,
            clipStartSec = 10.0,
            clipEndSec = 20.0,
            coordinateSystem = AutoCutEngine.TimelineCoordinateSystem.CLIP_RELATIVE
        )
        val keepFromSource = AutoCutEngine.buildKeepSegments(
            cuts = sourceRelativeCuts,
            clipStartSec = 10.0,
            clipEndSec = 20.0,
            coordinateSystem = AutoCutEngine.TimelineCoordinateSystem.SOURCE_RELATIVE
        )

        assertEquals(3, keepFromClip.size)
        assertEquals(keepFromSource, keepFromClip)
        assertEquals(AutoCutEngine.KeepSegment(10.0, 12.0, 0.0, 2.0), keepFromClip[0])
        assertEquals(AutoCutEngine.KeepSegment(14.0, 17.0, 2.0, 5.0), keepFromClip[1])
        assertEquals(AutoCutEngine.KeepSegment(18.5, 20.0, 5.0, 6.5), keepFromClip[2])
    }

    @Test
    fun monotonicPtsRebaser_maintainsStrictMonotonicPtsAcrossCutBoundaries() {
        val rebaser = AutoCutEngine.MonotonicPtsRebaser()

        // Piece 0: 10s..15s source
        rebaser.beginSegment(sourceStartUs = 10_000_000L, sourceEndUs = 15_000_000L)
        val p0 = rebaser.rebaseSamplePtsUs(10_000_000L)
        val p1 = rebaser.rebaseSamplePtsUs(12_500_000L)
        val p2 = rebaser.rebaseSamplePtsUs(14_960_000L)
        rebaser.endSegment(15_000_000L)

        assertEquals(0L, p0)
        assertEquals(2_500_000L, p1)
        assertEquals(4_960_000L, p2)

        // Piece 1: 25s..28s source (even if decoder delivers a duplicate/backward raw PTS, output advances monotonically)
        rebaser.beginSegment(sourceStartUs = 25_000_000L, sourceEndUs = 28_000_000L)
        val p3 = rebaser.rebaseSamplePtsUs(25_000_000L)
        val p4Duplicate = rebaser.rebaseSamplePtsUs(25_000_000L, frameDurationUs = 23_219L)
        val p5 = rebaser.rebaseSamplePtsUs(26_000_000L)

        assertEquals(5_000_000L, p3)
        assertTrue("Expected monotonic increase after duplicate raw PTS", p4Duplicate > p3)
        assertEquals(5_023_219L, p4Duplicate)
        assertEquals(6_000_000L, p5)
    }

    @Test
    fun classifyCodecOutput_handlesFormatChangeCsdAndCompleteEosDraining() {
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_FORMAT_CHANGED,
            AutoCutEngine.classifyCodecOutput(AutoCutEngine.CODEC_INFO_OUTPUT_FORMAT_CHANGED, 0)
        )
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_BUFFERS_CHANGED,
            AutoCutEngine.classifyCodecOutput(AutoCutEngine.CODEC_INFO_OUTPUT_BUFFERS_CHANGED, 0)
        )
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_TRY_AGAIN,
            AutoCutEngine.classifyCodecOutput(AutoCutEngine.CODEC_INFO_TRY_AGAIN_LATER, 0)
        )
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_CSD_SKIPPED,
            AutoCutEngine.classifyCodecOutput(0, AutoCutEngine.CODEC_FLAG_CODEC_CONFIG)
        )
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_FRAME,
            AutoCutEngine.classifyCodecOutput(1, 0)
        )
        assertEquals(
            AutoCutEngine.CodecDrainAction.OUTPUT_EOS,
            AutoCutEngine.classifyCodecOutput(2, AutoCutEngine.CODEC_FLAG_END_OF_STREAM)
        )
    }

    @Test
    fun decodePcmBytesToNormalizedSamples_decodes16BitFloatAnd8BitAfterFormatChange() {
        // 16-bit PCM: [0, 16384, -32768]
        val bb16 = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN)
        bb16.putShort(0).putShort(16384).putShort(Short.MIN_VALUE)
        val s16 = AutoCutEngine.decodePcmBytesToNormalizedSamples(bb16.array(), AutoCutEngine.PCM_ENCODING_16BIT)
        assertArrayEquals(doubleArrayOf(0.0, 0.5, -1.0), s16, 0.001)

        // Float PCM: [0.25f, -0.75f]
        val bbFloat = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
        bbFloat.putFloat(0.25f).putFloat(-0.75f)
        val sFloat = AutoCutEngine.decodePcmBytesToNormalizedSamples(bbFloat.array(), AutoCutEngine.PCM_ENCODING_FLOAT)
        assertArrayEquals(doubleArrayOf(0.25, -0.75), sFloat, 0.0001)

        // 8-bit unsigned PCM: [128 -> 0.0, 255 -> ~+0.992, 0 -> -1.0]
        val s8 = AutoCutEngine.decodePcmBytesToNormalizedSamples(
            byteArrayOf(128.toByte(), 255.toByte(), 0.toByte()),
            AutoCutEngine.PCM_ENCODING_8BIT
        )
        assertEquals(0.0, s8[0], 0.0001)
        assertEquals(127.0 / 128.0, s8[1], 0.001)
        assertEquals(-1.0, s8[2], 0.0001)
    }

    @Test
    fun categorizeTimestampedSilences_preservesSourceOffsetAndTrailingTailWindow() {
        val windows = mutableListOf<AutoCutEngine.TimestampedDbWindow>()
        // Clip starts at 30.0s, ends at 34.0s
        // Loud speech 30.0..31.0 (-12 dB), then silent tail 31.0..34.0 (-65 dB)
        for (i in 0 until 20) {
            val s = 30.0 + i * 0.05
            windows.add(AutoCutEngine.TimestampedDbWindow(s, s + 0.05, -12.0))
        }
        for (i in 20 until 80) {
            val s = 30.0 + i * 0.05
            windows.add(AutoCutEngine.TimestampedDbWindow(s, s + 0.05, -65.0))
        }

        val proposals = AutoCutEngine.categorizeTimestampedSilences(
            windows = windows,
            clipStartSec = 30.0,
            clipEndSec = 34.0
        )

        assertEquals(1, proposals.size)
        assertTrue(proposals[0].source_start >= 31.0)
        assertTrue("Expected tail silence to extend close to 34.0s clip end, got ${proposals[0].source_end}", proposals[0].source_end >= 33.7)
    }
}
