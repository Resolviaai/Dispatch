package com.resolvia.dispatch.media

import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToLong
import kotlin.math.sqrt

/**
 * On-device speech and dead-air analysis engine for Dispatch Mobile.
 * Performs hardware-accelerated audio decoding to PCM, windowed RMS energy computation,
 * and 3-tier silence proposal categorization (AUTO-REMOVE, REVIEW, PRESERVE).
 *
 * Also provides timeline coordinate mapping (`clipStartSec` source offset across
 * clip-relative and source-relative EDLs), monotonic PTS rebasing across cut boundaries,
 * format-change/CSD tracking, and complete EOS codec draining.
 *
 * Runs 100% locally on Android without requiring PC connection or cloud round trips.
 */
object AutoCutEngine {

    private const val TAG = "AutoCutEngine"
    internal const val WINDOW_MS = 50L // 50ms energy analysis windows
    private const val SILENCE_THRESHOLD_DB = -38.0 // Audio below -38 dB considered silence / pause
    private const val MIN_SILENCE_SEC = 0.5 // Minimum pause to trigger a proposal
    private const val AUTO_REMOVE_SEC = 1.2 // Pauses > 1.2s are suggested for auto-removal
    private const val TARGET_PAUSE_PAD_SEC = 0.6 // Natural breathing room retained after cut

    internal const val PCM_ENCODING_16BIT = 2
    internal const val PCM_ENCODING_8BIT = 3
    internal const val PCM_ENCODING_FLOAT = 4

    internal const val CODEC_INFO_TRY_AGAIN_LATER = -1
    internal const val CODEC_INFO_OUTPUT_FORMAT_CHANGED = -2
    internal const val CODEC_INFO_OUTPUT_BUFFERS_CHANGED = -3
    internal const val CODEC_FLAG_CODEC_CONFIG = 2
    internal const val CODEC_FLAG_END_OF_STREAM = 4
    private const val MAX_EOS_POLL_ITERATIONS = 500
    private const val EXTRACTOR_LOOKAHEAD_US = 250_000L

    enum class TimelineCoordinateSystem {
        CLIP_RELATIVE,
        SOURCE_RELATIVE
    }

    data class CutInterval(
        val startSec: Double,
        val endSec: Double
    )

    data class KeepSegment(
        val sourceStartSec: Double,
        val sourceEndSec: Double,
        val outputStartSec: Double,
        val outputEndSec: Double
    )

    data class ExtractorRange(
        val seekStartUs: Long,
        val endLimitUs: Long,
        val sourceOffsetSec: Double,
        val isClipRelativeMedia: Boolean
    )

    data class TimestampedDbWindow(
        val startSec: Double,
        val endSec: Double,
        val db: Double
    )

    data class AudioPcmFormatState(
        val sampleRate: Int = 44100,
        val channelCount: Int = 2,
        val pcmEncoding: Int = PCM_ENCODING_16BIT,
        val csd0: ByteArray? = null,
        val csd1: ByteArray? = null
    ) {
        val samplesPerWindow: Int
            get() = max(1, (sampleRate * (WINDOW_MS / 1000.0) * channelCount).toInt())

        override fun equals(other: Any?): Boolean {
            if (this === other) return true
            if (other !is AudioPcmFormatState) return false
            return sampleRate == other.sampleRate &&
                channelCount == other.channelCount &&
                pcmEncoding == other.pcmEncoding &&
                csd0.contentEquals(other.csd0) &&
                csd1.contentEquals(other.csd1)
        }

        override fun hashCode(): Int {
            var result = sampleRate
            result = 31 * result + channelCount
            result = 31 * result + pcmEncoding
            result = 31 * result + (csd0?.contentHashCode() ?: 0)
            result = 31 * result + (csd1?.contentHashCode() ?: 0)
            return result
        }
    }

    enum class CodecDrainAction {
        OUTPUT_FRAME,
        OUTPUT_CSD_SKIPPED,
        OUTPUT_FORMAT_CHANGED,
        OUTPUT_BUFFERS_CHANGED,
        OUTPUT_TRY_AGAIN,
        OUTPUT_EOS
    }

    /**
     * Maintains strictly monotonic output presentation timestamps across cut segment
     * boundaries so neither audio nor video frames freeze, overlap, or jump backward.
     */
    class MonotonicPtsRebaser(private val minStepUs: Long = 1L) {
        var segmentOutputBaseUs: Long = 0L
            private set
        var segmentSourceStartUs: Long = 0L
            private set
        var segmentSourceEndUs: Long = Long.MAX_VALUE
            private set
        var lastEmittedPtsUs: Long = Long.MIN_VALUE
            private set
        private var lastRawSegmentPtsUs: Long = Long.MIN_VALUE
        private var observedFrameDeltaUs: Long = 33_333L

        fun beginSegment(
            sourceStartUs: Long,
            sourceEndUs: Long = Long.MAX_VALUE,
            explicitOutputBaseUs: Long? = null
        ) {
            segmentSourceStartUs = max(0L, sourceStartUs)
            segmentSourceEndUs = max(segmentSourceStartUs, sourceEndUs)
            if (explicitOutputBaseUs != null) {
                segmentOutputBaseUs = if (lastEmittedPtsUs != Long.MIN_VALUE) {
                    max(explicitOutputBaseUs, lastEmittedPtsUs + minStepUs)
                } else {
                    max(0L, explicitOutputBaseUs)
                }
            } else if (lastEmittedPtsUs != Long.MIN_VALUE) {
                segmentOutputBaseUs = max(segmentOutputBaseUs, lastEmittedPtsUs + max(minStepUs, observedFrameDeltaUs))
            }
            lastRawSegmentPtsUs = Long.MIN_VALUE
        }

        fun rebaseSamplePtsUs(rawSamplePtsUs: Long, frameDurationUs: Long = observedFrameDeltaUs): Long {
            if (lastRawSegmentPtsUs != Long.MIN_VALUE && rawSamplePtsUs > lastRawSegmentPtsUs) {
                val delta = rawSamplePtsUs - lastRawSegmentPtsUs
                if (delta in 1L..500_000L) {
                    observedFrameDeltaUs = delta
                }
            }
            lastRawSegmentPtsUs = max(lastRawSegmentPtsUs, rawSamplePtsUs)

            val relativeUs = max(0L, rawSamplePtsUs - segmentSourceStartUs)
            var candidate = segmentOutputBaseUs + relativeUs
            if (lastEmittedPtsUs != Long.MIN_VALUE && candidate <= lastEmittedPtsUs) {
                candidate = lastEmittedPtsUs + max(minStepUs, frameDurationUs.coerceAtLeast(minStepUs))
            }
            lastEmittedPtsUs = candidate
            return candidate
        }

        fun endSegment(actualEndPtsUs: Long? = null) {
            val expectedDurationUs = if (segmentSourceEndUs < Long.MAX_VALUE && segmentSourceEndUs > segmentSourceStartUs) {
                segmentSourceEndUs - segmentSourceStartUs
            } else if (actualEndPtsUs != null && actualEndPtsUs > segmentSourceStartUs) {
                actualEndPtsUs - segmentSourceStartUs
            } else if (lastEmittedPtsUs != Long.MIN_VALUE) {
                (lastEmittedPtsUs - segmentOutputBaseUs) + observedFrameDeltaUs
            } else {
                0L
            }
            val nextBase = segmentOutputBaseUs + expectedDurationUs
            segmentOutputBaseUs = if (lastEmittedPtsUs != Long.MIN_VALUE) {
                max(nextBase, lastEmittedPtsUs + minStepUs)
            } else {
                nextBase
            }
        }
    }

    data class SilenceSpan(
        val startSec: Double,
        val endSec: Double,
        val durationSec: Double,
        val avgDb: Double
    )

    data class Proposal(
        val id: String,
        val type: String,
        val source_start: Double,
        val source_end: Double,
        val duration: Double,
        val reason: String,
        val confidence: Double,
        val decision: String // "AUTO-REMOVE" | "REVIEW" | "PRESERVE"
    )

    /**
     * Resolves how [clipStartSec] and [clipEndSec] map onto the MediaExtractor timebase.
     *
     * When the media file is already trimmed / clip-relative (its duration is shorter than
     * [clipStartSec] + 0.25s while [clipStartSec] > 0), MediaExtractor reads from 0s and each
     * sample timestamp is shifted back to source time via `sourceOffsetSec = clipStartSec`.
     * When the media file is the full source recording, MediaExtractor seeks directly into
     * `clipStartSec` and sample timestamps remain in native source coordinates (`sourceOffsetSec = 0.0`).
     */
    fun resolveExtractorRange(
        clipStartSec: Double,
        clipEndSec: Double,
        mediaDurationUs: Long
    ): ExtractorRange {
        val safeStart = max(0.0, clipStartSec)
        val hasFiniteEnd = clipEndSec.isFinite() && clipEndSec < Double.MAX_VALUE / 2.0
        val mediaDurationSec = if (mediaDurationUs > 0L) mediaDurationUs / 1_000_000.0 else -1.0

        val isClipRelativeMedia = mediaDurationSec > 0.0 &&
            safeStart > 0.0 &&
            mediaDurationSec < (safeStart + 0.25)

        return if (isClipRelativeMedia) {
            val clipSpanSec = if (hasFiniteEnd && clipEndSec > safeStart) {
                min(clipEndSec - safeStart, mediaDurationSec)
            } else {
                mediaDurationSec
            }
            ExtractorRange(
                seekStartUs = 0L,
                endLimitUs = (clipSpanSec * 1_000_000.0).roundToLong(),
                sourceOffsetSec = safeStart,
                isClipRelativeMedia = true
            )
        } else {
            val boundedEndUs = if (hasFiniteEnd) {
                val effectiveEnd = max(safeStart, clipEndSec)
                val rawEndUs = (effectiveEnd * 1_000_000.0).roundToLong()
                if (mediaDurationUs > 0L) min(rawEndUs, mediaDurationUs) else rawEndUs
            } else {
                if (mediaDurationUs > 0L) mediaDurationUs else Long.MAX_VALUE
            }
            ExtractorRange(
                seekStartUs = (safeStart * 1_000_000.0).roundToLong(),
                endLimitUs = boundedEndUs,
                sourceOffsetSec = 0.0,
                isClipRelativeMedia = false
            )
        }
    }

    /**
     * Maps a source-coordinate timestamp into MediaExtractor sample time (microseconds)
     * according to the resolved [ExtractorRange].
     */
    fun mapSourceTimeToExtractorSampleUs(
        sourceTimeSec: Double,
        extractorRange: ExtractorRange
    ): Long {
        val localSec = max(0.0, sourceTimeSec - extractorRange.sourceOffsetSec)
        val us = (localSec * 1_000_000.0).roundToLong()
        return if (extractorRange.endLimitUs < Long.MAX_VALUE) {
            us.coerceIn(0L, extractorRange.endLimitUs)
        } else {
            us
        }
    }

    /**
     * Normalizes cut intervals from either CLIP_RELATIVE or SOURCE_RELATIVE coordinates
     * into sorted, merged, source-coordinate intervals.
     */
    fun mapCutIntervalsToSource(
        cuts: List<CutInterval>,
        clipStartSec: Double,
        coordinateSystem: TimelineCoordinateSystem
    ): List<CutInterval> {
        if (cuts.isEmpty()) return emptyList()
        val offset = when (coordinateSystem) {
            TimelineCoordinateSystem.CLIP_RELATIVE -> max(0.0, clipStartSec)
            TimelineCoordinateSystem.SOURCE_RELATIVE -> 0.0
        }
        val shifted = cuts.mapNotNull { c ->
            val s = c.startSec + offset
            val e = c.endSec + offset
            if (e > s + 1e-4) CutInterval(s, e) else null
        }.sortedBy { it.startSec }

        if (shifted.isEmpty()) return emptyList()
        val merged = mutableListOf<CutInterval>()
        var current = shifted[0]
        for (i in 1 until shifted.size) {
            val next = shifted[i]
            if (next.startSec <= current.endSec + 1e-3) {
                current = CutInterval(current.startSec, max(current.endSec, next.endSec))
            } else {
                merged.add(current)
                current = next
            }
        }
        merged.add(current)
        return merged
    }

    /**
     * Converts cut intervals into contiguous surviving [KeepSegment]s with exact source
     * and output presentation offsets.
     */
    fun buildKeepSegments(
        cuts: List<CutInterval>,
        clipStartSec: Double,
        clipEndSec: Double,
        coordinateSystem: TimelineCoordinateSystem,
        mediaDurationSec: Double? = null
    ): List<KeepSegment> {
        val safeStart = max(0.0, clipStartSec)
        val boundedEnd = if (mediaDurationSec != null && mediaDurationSec > 0.0) {
            min(clipEndSec, mediaDurationSec)
        } else {
            clipEndSec
        }
        if (boundedEnd <= safeStart + 1e-4) return emptyList()

        val sourceCuts = mapCutIntervalsToSource(cuts, safeStart, coordinateSystem)
        val keeps = mutableListOf<KeepSegment>()
        var cursor = safeStart
        var outCursor = 0.0

        for (cut in sourceCuts) {
            val cutS = cut.startSec.coerceIn(safeStart, boundedEnd)
            val cutE = cut.endSec.coerceIn(safeStart, boundedEnd)
            if (cutE <= cutS + 1e-4) continue
            if (cutS > cursor + 1e-4) {
                val dur = cutS - cursor
                keeps.add(
                    KeepSegment(
                        sourceStartSec = roundMs(cursor),
                        sourceEndSec = roundMs(cutS),
                        outputStartSec = roundMs(outCursor),
                        outputEndSec = roundMs(outCursor + dur)
                    )
                )
                outCursor += dur
            }
            cursor = max(cursor, cutE)
        }

        if (boundedEnd > cursor + 1e-4) {
            val dur = boundedEnd - cursor
            keeps.add(
                KeepSegment(
                    sourceStartSec = roundMs(cursor),
                    sourceEndSec = roundMs(boundedEnd),
                    outputStartSec = roundMs(outCursor),
                    outputEndSec = roundMs(outCursor + dur)
                )
            )
        }

        return keeps
    }

    internal fun classifyCodecOutput(
        outIndex: Int,
        bufferFlags: Int,
        codecConfigFlag: Int = CODEC_FLAG_CODEC_CONFIG,
        eosFlag: Int = CODEC_FLAG_END_OF_STREAM
    ): CodecDrainAction {
        return when {
            outIndex == CODEC_INFO_TRY_AGAIN_LATER -> CodecDrainAction.OUTPUT_TRY_AGAIN
            outIndex == CODEC_INFO_OUTPUT_FORMAT_CHANGED -> CodecDrainAction.OUTPUT_FORMAT_CHANGED
            outIndex == CODEC_INFO_OUTPUT_BUFFERS_CHANGED -> CodecDrainAction.OUTPUT_BUFFERS_CHANGED
            outIndex < 0 -> CodecDrainAction.OUTPUT_TRY_AGAIN
            (bufferFlags and codecConfigFlag) != 0 -> CodecDrainAction.OUTPUT_CSD_SKIPPED
            (bufferFlags and eosFlag) != 0 -> CodecDrainAction.OUTPUT_EOS
            else -> CodecDrainAction.OUTPUT_FRAME
        }
    }

    internal fun decodePcmBytesToNormalizedSamples(
        bytes: ByteArray,
        pcmEncoding: Int
    ): DoubleArray {
        if (bytes.isEmpty()) return DoubleArray(0)
        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        return when (pcmEncoding) {
            PCM_ENCODING_FLOAT -> {
                val fb = bb.asFloatBuffer()
                val out = DoubleArray(fb.remaining())
                var i = 0
                while (fb.hasRemaining()) {
                    out[i++] = fb.get().toDouble().coerceIn(-1.0, 1.0)
                }
                out
            }
            PCM_ENCODING_8BIT -> {
                val out = DoubleArray(bytes.size)
                for (i in bytes.indices) {
                    val unsigned = bytes[i].toInt() and 0xFF
                    out[i] = (unsigned - 128) / 128.0
                }
                out
            }
            else -> {
                val sb = bb.asShortBuffer()
                val out = DoubleArray(sb.remaining())
                var i = 0
                while (sb.hasRemaining()) {
                    out[i++] = sb.get().toDouble() / 32768.0
                }
                out
            }
        }
    }

    internal fun readFormatState(
        format: MediaFormat,
        previous: AudioPcmFormatState = AudioPcmFormatState()
    ): AudioPcmFormatState {
        val sr = if (format.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
            format.getInteger(MediaFormat.KEY_SAMPLE_RATE).coerceAtLeast(8000)
        } else previous.sampleRate
        val ch = if (format.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
            format.getInteger(MediaFormat.KEY_CHANNEL_COUNT).coerceAtLeast(1)
        } else previous.channelCount
        val enc = if (format.containsKey(MediaFormat.KEY_PCM_ENCODING)) {
            format.getInteger(MediaFormat.KEY_PCM_ENCODING)
        } else previous.pcmEncoding
        val csd0 = if (format.containsKey("csd-0")) {
            val buf = format.getByteBuffer("csd-0")
            buf?.let {
                val dup = it.duplicate()
                ByteArray(dup.remaining()).also { arr -> dup.get(arr) }
            }
        } else previous.csd0
        val csd1 = if (format.containsKey("csd-1")) {
            val buf = format.getByteBuffer("csd-1")
            buf?.let {
                val dup = it.duplicate()
                ByteArray(dup.remaining()).also { arr -> dup.get(arr) }
            }
        } else previous.csd1

        return AudioPcmFormatState(
            sampleRate = sr,
            channelCount = ch,
            pcmEncoding = enc,
            csd0 = csd0,
            csd1 = csd1
        )
    }

    /**
     * Decodes the audio track and extracts silence proposals with exact source-time mapping,
     * dynamic format-change handling, CSD filtering, and full MediaCodec EOS draining.
     */
    fun analyzeMedia(
        context: Context,
        inputUriOrPath: String,
        clipStartSec: Double = 0.0,
        clipEndSec: Double = Double.MAX_VALUE
    ): List<Proposal> {
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null

        try {
            if (inputUriOrPath.startsWith("content://")) {
                val uri = Uri.parse(inputUriOrPath)
                val pfd = context.contentResolver.openFileDescriptor(uri, "r")
                    ?: return emptyList()
                pfd.use {
                    extractor.setDataSource(it.fileDescriptor)
                }
            } else {
                val file = File(inputUriOrPath)
                if (!file.exists()) return emptyList()
                extractor.setDataSource(file.absolutePath)
            }

            var audioTrackIndex = -1
            var audioFormat: MediaFormat? = null

            for (i in 0 until extractor.trackCount) {
                val format = extractor.getTrackFormat(i)
                val mime = format.getString(MediaFormat.KEY_MIME) ?: ""
                if (mime.startsWith("audio/")) {
                    audioTrackIndex = i
                    audioFormat = format
                    break
                }
            }

            if (audioTrackIndex < 0 || audioFormat == null) {
                return emptyList()
            }

            extractor.selectTrack(audioTrackIndex)

            val trackDurationUs = if (audioFormat.containsKey(MediaFormat.KEY_DURATION)) {
                audioFormat.getLong(MediaFormat.KEY_DURATION)
            } else -1L

            val extractorRange = resolveExtractorRange(clipStartSec, clipEndSec, trackDurationUs)
            if (extractorRange.seekStartUs > 0L) {
                extractor.seekTo(extractorRange.seekStartUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
            }

            val mime = audioFormat.getString(MediaFormat.KEY_MIME) ?: "audio/mp4a-latm"
            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(audioFormat, null, null, 0)
            codec.start()

            var formatState = readFormatState(audioFormat)
            var samplesPerWindow = formatState.samplesPerWindow
            var sampleAccumulator = DoubleArray(samplesPerWindow)
            var sampleCountInWindow = 0
            var currentWindowStartSec = -1.0

            val bufferInfo = MediaCodec.BufferInfo()
            var inputEos = false
            var outputEos = false
            var emptyEosPolls = 0
            val windows = mutableListOf<TimestampedDbWindow>()
            val timeoutUs = 10_000L

            fun flushPartialWindow(endSecHint: Double) {
                if (sampleCountInWindow <= 0 || currentWindowStartSec < 0.0) {
                    sampleCountInWindow = 0
                    return
                }
                var sumSq = 0.0
                for (idx in 0 until sampleCountInWindow) {
                    val s = sampleAccumulator[idx]
                    sumSq += s * s
                }
                val rms = sqrt(sumSq / sampleCountInWindow)
                val db = 20.0 * log10(max(rms, 1e-5))
                val fallbackDur = sampleCountInWindow.toDouble() /
                    max(1, formatState.sampleRate * formatState.channelCount)
                val wEnd = max(currentWindowStartSec + 0.001, endSecHint.takeIf { it > currentWindowStartSec }
                    ?: (currentWindowStartSec + fallbackDur))
                windows.add(TimestampedDbWindow(currentWindowStartSec, wEnd, db))
                sampleCountInWindow = 0
                currentWindowStartSec = -1.0
            }

            while (!outputEos) {
                if (!inputEos) {
                    val inIndex = codec.dequeueInputBuffer(timeoutUs)
                    if (inIndex >= 0) {
                        val inBuffer = codec.getInputBuffer(inIndex)
                        if (inBuffer != null) {
                            inBuffer.clear()
                            val sampleSize = extractor.readSampleData(inBuffer, 0)
                            val samplePtsUs = extractor.sampleTime
                            val pastEndLimit = extractorRange.endLimitUs < Long.MAX_VALUE &&
                                samplePtsUs > extractorRange.endLimitUs + EXTRACTOR_LOOKAHEAD_US

                            if (sampleSize < 0 || pastEndLimit) {
                                codec.queueInputBuffer(
                                    inIndex,
                                    0,
                                    0,
                                    0L,
                                    MediaCodec.BUFFER_FLAG_END_OF_STREAM
                                )
                                inputEos = true
                            } else {
                                val flags = extractor.sampleFlags
                                codec.queueInputBuffer(inIndex, 0, sampleSize, samplePtsUs, flags)
                                extractor.advance()
                            }
                        }
                    }
                }

                var outIndex = codec.dequeueOutputBuffer(bufferInfo, timeoutUs)
                drainOutput@ while (true) {
                    when (classifyCodecOutput(outIndex, bufferInfo.flags)) {
                        CodecDrainAction.OUTPUT_TRY_AGAIN -> {
                            if (inputEos) {
                                emptyEosPolls++
                                if (emptyEosPolls >= MAX_EOS_POLL_ITERATIONS) {
                                    outputEos = true
                                }
                            }
                            break@drainOutput
                        }
                        CodecDrainAction.OUTPUT_FORMAT_CHANGED -> {
                            emptyEosPolls = 0
                            val updatedFormat = codec.outputFormat
                            flushPartialWindow(currentWindowStartSec + (WINDOW_MS / 1000.0))
                            formatState = readFormatState(updatedFormat, formatState)
                            samplesPerWindow = formatState.samplesPerWindow
                            sampleAccumulator = DoubleArray(samplesPerWindow)
                            sampleCountInWindow = 0
                        }
                        CodecDrainAction.OUTPUT_BUFFERS_CHANGED -> {
                            emptyEosPolls = 0
                        }
                        CodecDrainAction.OUTPUT_CSD_SKIPPED -> {
                            emptyEosPolls = 0
                            codec.releaseOutputBuffer(outIndex, false)
                        }
                        CodecDrainAction.OUTPUT_FRAME, CodecDrainAction.OUTPUT_EOS -> {
                            emptyEosPolls = 0
                            val isLast = (bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0
                            val outBuffer = codec.getOutputBuffer(outIndex)
                            if (outBuffer != null && bufferInfo.size > 0) {
                                outBuffer.position(bufferInfo.offset)
                                outBuffer.limit(bufferInfo.offset + bufferInfo.size)
                                val rawBytes = ByteArray(bufferInfo.size)
                                outBuffer.get(rawBytes)
                                val samples = decodePcmBytesToNormalizedSamples(rawBytes, formatState.pcmEncoding)

                                val totalChannelSamplesPerSec = max(1, formatState.sampleRate * formatState.channelCount).toDouble()
                                val bufferStartSourceSec = (max(0L, bufferInfo.presentationTimeUs) / 1_000_000.0) +
                                    extractorRange.sourceOffsetSec

                                for (sIdx in samples.indices) {
                                    if (sampleCountInWindow == 0) {
                                        currentWindowStartSec = bufferStartSourceSec + (sIdx / totalChannelSamplesPerSec)
                                    }
                                    sampleAccumulator[sampleCountInWindow++] = samples[sIdx]

                                    if (sampleCountInWindow >= samplesPerWindow) {
                                        var sumSq = 0.0
                                        for (s in sampleAccumulator) {
                                            sumSq += s * s
                                        }
                                        val rms = sqrt(sumSq / sampleAccumulator.size)
                                        val db = 20.0 * log10(max(rms, 1e-5))
                                        val windowEndSec = currentWindowStartSec + (samplesPerWindow / totalChannelSamplesPerSec)
                                        windows.add(TimestampedDbWindow(currentWindowStartSec, windowEndSec, db))
                                        sampleCountInWindow = 0
                                        currentWindowStartSec = -1.0
                                    }
                                }
                            }
                            codec.releaseOutputBuffer(outIndex, false)
                            if (isLast) {
                                outputEos = true
                                break@drainOutput
                            }
                        }
                    }
                    outIndex = codec.dequeueOutputBuffer(bufferInfo, 0)
                }
            }

            if (sampleCountInWindow > 0 && currentWindowStartSec >= 0.0) {
                val totalChannelSamplesPerSec = max(1, formatState.sampleRate * formatState.channelCount).toDouble()
                flushPartialWindow(currentWindowStartSec + (sampleCountInWindow / totalChannelSamplesPerSec))
            }

            val safeStart = max(0.0, clipStartSec)
            val effectiveEnd = if (clipEndSec.isFinite() && clipEndSec < Double.MAX_VALUE / 2.0) {
                clipEndSec
            } else {
                Double.MAX_VALUE
            }
            return categorizeTimestampedSilences(windows, safeStart, effectiveEnd)
        } catch (e: Exception) {
            Log.e(TAG, "Waveform analysis failed: ${e.message}", e)
            return emptyList()
        } finally {
            try {
                codec?.stop()
            } catch (_: Exception) {}
            try {
                codec?.release()
            } catch (_: Exception) {}
            try {
                extractor.release()
            } catch (_: Exception) {}
        }
    }

    internal fun categorizeSilences(
        pcmWindows: List<Double>,
        windowMs: Long,
        clipStartSec: Double,
        clipEndSec: Double
    ): List<Proposal> {
        val timestamped = pcmWindows.mapIndexed { idx, db ->
            val s = (idx * windowMs) / 1000.0
            val e = ((idx + 1) * windowMs) / 1000.0
            TimestampedDbWindow(s, e, db)
        }
        return categorizeTimestampedSilences(timestamped, clipStartSec, clipEndSec)
    }

    fun categorizeTimestampedSilences(
        windows: List<TimestampedDbWindow>,
        clipStartSec: Double,
        clipEndSec: Double
    ): List<Proposal> {
        if (windows.isEmpty()) return emptyList()

        val spans = mutableListOf<SilenceSpan>()
        var inSilence = false
        var silenceStartSec = 0.0
        var silenceLastEndSec = 0.0
        var totalDb = 0.0
        var silenceWindowCount = 0

        for (w in windows) {
            if (w.endSec <= w.startSec) continue
            val isSilent = w.db < SILENCE_THRESHOLD_DB
            if (isSilent) {
                if (!inSilence) {
                    inSilence = true
                    silenceStartSec = w.startSec
                    silenceLastEndSec = w.endSec
                    totalDb = w.db
                    silenceWindowCount = 1
                } else {
                    // Split span if there is a discontinuity gap > 250ms between windows
                    if (w.startSec - silenceLastEndSec > 0.25) {
                        val dur = silenceLastEndSec - silenceStartSec
                        if (dur >= MIN_SILENCE_SEC) {
                            spans.add(SilenceSpan(silenceStartSec, silenceLastEndSec, dur, totalDb / max(1, silenceWindowCount)))
                        }
                        silenceStartSec = w.startSec
                        totalDb = w.db
                        silenceWindowCount = 1
                    } else {
                        totalDb += w.db
                        silenceWindowCount++
                    }
                    silenceLastEndSec = w.endSec
                }
            } else {
                if (inSilence) {
                    inSilence = false
                    val dur = silenceLastEndSec - silenceStartSec
                    if (dur >= MIN_SILENCE_SEC) {
                        spans.add(SilenceSpan(silenceStartSec, silenceLastEndSec, dur, totalDb / max(1, silenceWindowCount)))
                    }
                }
            }
        }

        if (inSilence) {
            val dur = silenceLastEndSec - silenceStartSec
            if (dur >= MIN_SILENCE_SEC) {
                spans.add(SilenceSpan(silenceStartSec, silenceLastEndSec, dur, totalDb / max(1, silenceWindowCount)))
            }
        }

        val proposals = mutableListOf<Proposal>()
        var proposalIdx = 1

        for (span in spans) {
            if (span.endSec < clipStartSec || span.startSec > clipEndSec) continue

            val boundedStart = max(span.startSec, clipStartSec)
            val boundedEnd = min(span.endSec, clipEndSec)
            val dur = boundedEnd - boundedStart

            if (dur < MIN_SILENCE_SEC) continue

            // Trim out the excess pause, leaving TARGET_PAUSE_PAD_SEC
            val cutStart = boundedStart + (TARGET_PAUSE_PAD_SEC / 2.0)
            val cutEnd = boundedEnd - (TARGET_PAUSE_PAD_SEC / 2.0)
            val cutDur = max(0.0, cutEnd - cutStart)

            if (cutDur < 0.2) continue

            val isAutoRemove = dur >= AUTO_REMOVE_SEC
            val decision = if (isAutoRemove) "AUTO-REMOVE" else "REVIEW"
            val reason = if (isAutoRemove) {
                String.format("Long dead-air pause (%.1fs trimmed down to %.1fs)", dur, TARGET_PAUSE_PAD_SEC)
            } else {
                String.format("Extended hesitation (%.1fs trimmed down to %.1fs)", dur, TARGET_PAUSE_PAD_SEC)
            }

            proposals.add(
                Proposal(
                    id = "local_cut_$proposalIdx",
                    type = "dead_air",
                    source_start = roundMs(cutStart),
                    source_end = roundMs(cutEnd),
                    duration = roundMs(cutDur),
                    reason = reason,
                    confidence = if (isAutoRemove) 0.96 else 0.82,
                    decision = decision
                )
            )
            proposalIdx++
        }

        return proposals
    }

    private fun roundMs(value: Double): Double = Math.round(value * 1000.0) / 1000.0

    /**
     * Serializes proposals into JSON string matching AutoCutProposal[] web interface.
     */
    fun toJsonString(proposals: List<Proposal>): String {
        val gson = Gson()
        val array = JsonArray()
        for (p in proposals) {
            val obj = JsonObject()
            obj.addProperty("id", p.id)
            obj.addProperty("type", p.type)
            obj.addProperty("source_start", p.source_start)
            obj.addProperty("source_end", p.source_end)
            obj.addProperty("duration", p.duration)
            obj.addProperty("reason", p.reason)
            obj.addProperty("confidence", p.confidence)
            obj.addProperty("decision", p.decision)
            array.add(obj)
        }
        return gson.toJson(array)
    }
}
