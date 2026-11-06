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
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * On-device speech and dead-air analysis engine for Dispatch Mobile.
 * Performs hardware-accelerated audio decoding to PCM, windowed RMS energy computation,
 * and 3-tier silence proposal categorization (AUTO-REMOVE, REVIEW, PRESERVE).
 *
 * Runs 100% locally on Android without requiring PC connection or cloud round trips.
 */
object AutoCutEngine {

    private const val TAG = "AutoCutEngine"
    private const val WINDOW_MS = 50L // 50ms energy analysis windows
    private const val SILENCE_THRESHOLD_DB = -38.0 // Audio below -38 dB considered silence / pause
    private const val MIN_SILENCE_SEC = 0.5 // Minimum pause to trigger a proposal
    private const val AUTO_REMOVE_SEC = 1.2 // Pauses > 1.2s are suggested for auto-removal
    private const val TARGET_PAUSE_PAD_SEC = 0.6 // Natural breathing room retained after cut

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
     * Decodes the audio track and extracts silence proposals.
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

            val mime = audioFormat.getString(MediaFormat.KEY_MIME) ?: "audio/mp4a-latm"
            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(audioFormat, null, null, 0)
            codec.start()

            val sampleRate = if (audioFormat.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
                audioFormat.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            } else 44100
            val channelCount = if (audioFormat.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
                audioFormat.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            } else 2

            val bufferInfo = MediaCodec.BufferInfo()
            var isEos = false
            val pcmWindows = mutableListOf<Double>() // dB per 50ms window

            val samplesPerWindow = (sampleRate * (WINDOW_MS / 1000.0) * channelCount).toInt()
            var sampleAccumulator = DoubleArray(samplesPerWindow)
            var sampleCountInWindow = 0

            val timeoutUs = 10_000L

            while (!isEos) {
                val inIndex = codec.dequeueInputBuffer(timeoutUs)
                if (inIndex >= 0) {
                    val inBuffer = codec.getInputBuffer(inIndex)
                    if (inBuffer != null) {
                        inBuffer.clear()
                        val sampleSize = extractor.readSampleData(inBuffer, 0)
                        if (sampleSize < 0) {
                            codec.queueInputBuffer(inIndex, 0, 0, 0L, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            isEos = true
                        } else {
                            val pts = extractor.sampleTime
                            codec.queueInputBuffer(inIndex, 0, sampleSize, pts, 0)
                            extractor.advance()
                        }
                    }
                }

                var outIndex = codec.dequeueOutputBuffer(bufferInfo, timeoutUs)
                while (outIndex >= 0) {
                    val outBuffer = codec.getOutputBuffer(outIndex)
                    if (outBuffer != null && bufferInfo.size > 0) {
                        outBuffer.position(bufferInfo.offset)
                        outBuffer.limit(bufferInfo.offset + bufferInfo.size)
                        val shortBuffer = outBuffer.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()

                        while (shortBuffer.hasRemaining()) {
                            val sample = shortBuffer.get().toDouble() / 32768.0
                            sampleAccumulator[sampleCountInWindow++] = sample

                            if (sampleCountInWindow >= samplesPerWindow) {
                                var sumSq = 0.0
                                for (s in sampleAccumulator) {
                                    sumSq += s * s
                                }
                                val rms = sqrt(sumSq / sampleAccumulator.size)
                                val db = 20.0 * log10(max(rms, 1e-5))
                                pcmWindows.add(db)

                                sampleCountInWindow = 0
                            }
                        }
                    }
                    codec.releaseOutputBuffer(outIndex, false)
                    if ((bufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        isEos = true
                        break
                    }
                    outIndex = codec.dequeueOutputBuffer(bufferInfo, 0)
                }
            }

            return categorizeSilences(pcmWindows, WINDOW_MS, clipStartSec, clipEndSec)

        } catch (e: Exception) {
            Log.e(TAG, "Waveform analysis failed: ${e.message}", e)
            return emptyList()
        } finally {
            try {
                codec?.stop()
            } catch (ignored: Exception) {}
            try {
                codec?.release()
            } catch (ignored: Exception) {}
            try {
                extractor.release()
            } catch (ignored: Exception) {}
        }
    }

    private fun categorizeSilences(
        pcmWindows: List<Double>,
        windowMs: Long,
        clipStartSec: Double,
        clipEndSec: Double
    ): List<Proposal> {
        val spans = mutableListOf<SilenceSpan>()
        var inSilence = false
        var silenceStartIdx = 0
        var totalDb = 0.0
        var silenceWindowCount = 0

        for (i in pcmWindows.indices) {
            val db = pcmWindows[i]
            val isSilent = db < SILENCE_THRESHOLD_DB

            if (isSilent) {
                if (!inSilence) {
                    inSilence = true
                    silenceStartIdx = i
                    totalDb = db
                    silenceWindowCount = 1
                } else {
                    totalDb += db
                    silenceWindowCount++
                }
            } else {
                if (inSilence) {
                    inSilence = false
                    val startSec = (silenceStartIdx * windowMs) / 1000.0
                    val endSec = (i * windowMs) / 1000.0
                    val dur = endSec - startSec
                    if (dur >= MIN_SILENCE_SEC) {
                        spans.add(SilenceSpan(startSec, endSec, dur, totalDb / silenceWindowCount))
                    }
                }
            }
        }

        if (inSilence) {
            val startSec = (silenceStartIdx * windowMs) / 1000.0
            val endSec = (pcmWindows.size * windowMs) / 1000.0
            val dur = endSec - startSec
            if (dur >= MIN_SILENCE_SEC) {
                spans.add(SilenceSpan(startSec, endSec, dur, totalDb / max(1, silenceWindowCount)))
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
                    source_start = Math.round(cutStart * 1000.0) / 1000.0,
                    source_end = Math.round(cutEnd * 1000.0) / 1000.0,
                    duration = Math.round(cutDur * 1000.0) / 1000.0,
                    reason = reason,
                    confidence = if (isAutoRemove) 0.96 else 0.82,
                    decision = decision
                )
            )
            proposalIdx++
        }

        return proposals
    }

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
