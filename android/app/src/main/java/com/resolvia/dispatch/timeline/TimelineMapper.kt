package com.resolvia.dispatch.timeline

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonNull
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.JsonPrimitive
import org.json.JSONArray
import org.json.JSONObject
import java.math.BigDecimal
import java.math.BigInteger
import java.security.MessageDigest
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

@JvmInline
value class SourceTime(val seconds: Double) {
    init {
        require(seconds.isFinite()) { "SourceTime must be finite" }
    }
}

@JvmInline
value class ProxyTime(val seconds: Double) {
    init {
        require(seconds.isFinite()) { "ProxyTime must be finite" }
    }
}

@JvmInline
value class ConcatTime(val seconds: Double) {
    init {
        require(seconds.isFinite()) { "ConcatTime must be finite" }
    }
}

data class TimelineSegment(
    val sourceStart: SourceTime,
    val sourceEnd: SourceTime,
    val id: String = ""
) {
    companion object {
        fun of(start: Double, end: Double, id: String = ""): TimelineSegment =
            TimelineSegment(SourceTime(start), SourceTime(end), id)
    }
}

sealed class PlaybackDescriptor {
    data class SourceVideo(val sourceDuration: Double? = null) : PlaybackDescriptor()
    data class EditWindow(val windowStart: SourceTime, val windowEnd: SourceTime) : PlaybackDescriptor()
    data class RenderedClip(val segments: List<TimelineSegment>) : PlaybackDescriptor()
}

enum class SnapGapMode {
    NONE,
    NEXT,
    PREVIOUS,
    NEAREST
}

sealed class SourceToConcatResult {
    data class Ok(
        val time: ConcatTime,
        val segmentIndex: Int,
        val snapped: Boolean = false
    ) : SourceToConcatResult()

    data class Gap(
        val reason: String,
        val sourceTime: SourceTime,
        val nearestSegmentStart: SourceTime?,
        val nearestSegmentEnd: SourceTime?,
        val nearestConcatTime: ConcatTime
    ) : SourceToConcatResult()
}

sealed class ConcatToSourceResult {
    data class Ok(
        val time: SourceTime,
        val segmentIndex: Int,
        val clamped: Boolean = false
    ) : ConcatToSourceResult()

    data class OutOfRange(
        val reason: String,
        val concatTime: ConcatTime,
        val minConcatTime: ConcatTime,
        val maxConcatTime: ConcatTime,
        val clampedTime: SourceTime
    ) : ConcatToSourceResult()
}

sealed class WindowMappingResult<out T> {
    data class Ok<out T>(
        val time: T,
        val clamped: Boolean = false
    ) : WindowMappingResult<T>()

    data class OutOfWindow<out T>(
        val reason: String,
        val windowStart: SourceTime,
        val windowEnd: SourceTime,
        val clampedTime: T
    ) : WindowMappingResult<T>()
}

sealed class PlaybackMappingResult {
    data class Ok(
        val timeSeconds: Double,
        val clamped: Boolean = false,
        val snapped: Boolean = false,
        val segmentIndex: Int? = null
    ) : PlaybackMappingResult()

    data class Gap(
        val reason: String,
        val sourceTime: SourceTime,
        val nearestSegmentStart: SourceTime?,
        val nearestSegmentEnd: SourceTime?,
        val nearestConcatTime: ConcatTime
    ) : PlaybackMappingResult()

    data class OutOfWindow(
        val reason: String,
        val windowStart: SourceTime,
        val windowEnd: SourceTime,
        val clampedTimeSeconds: Double
    ) : PlaybackMappingResult()

    data class OutOfRange(
        val reason: String,
        val minTimeSeconds: Double,
        val maxTimeSeconds: Double?,
        val clampedTimeSeconds: Double
    ) : PlaybackMappingResult()
}

/**
 * Pure, deterministic cross-runtime timeline mapper and RFC-8785 canonicalizer / fingerprinter.
 *
 * Mirrored identically by:
 *   - frontend/src/lib/timelineMapper.ts
 *   - cloud-worker/src/timelineContract.ts
 *   - dispatch/segments.py & dispatch/lib/timeline_mapper.py
 */
object TimelineMapper {
    const val TIMELINE_CONTRACT_VERSION = 1
    const val CANONICALIZER_VERSION = "rfc8785-v1"
    const val MIN_PIECE_DURATION = 0.1
    const val MIN_TOTAL_DURATION = 5.0
    const val MAX_TOTAL_DURATION = 180.0
    private const val TIME_EPSILON = 1e-9

    fun roundMs(seconds: Double): Double {
        require(seconds.isFinite()) { "seconds must be a finite number" }
        val rounded = floor(seconds * 1000.0 + 0.5) / 1000.0
        return if (rounded == 0.0) 0.0 else rounded
    }

    fun normalizeSegments(segments: List<TimelineSegment>): List<TimelineSegment> {
        require(segments.isNotEmpty()) { "segments must be a non-empty list" }
        val normalized = ArrayList<TimelineSegment>(segments.size)
        var prevEnd: Double? = null

        for (i in segments.indices) {
            val seg = segments[i]
            val s = roundMs(seg.sourceStart.seconds)
            val e = roundMs(seg.sourceEnd.seconds)
            val id = seg.id.trim().ifEmpty { "seg-${i + 1}" }

            require(s >= 0.0 && e > s) { "segment $i has invalid bounds [$s, $e]" }
            if (prevEnd != null) {
                require(s >= prevEnd - TIME_EPSILON) { "segments must be sorted and non-overlapping" }
            }

            normalized.add(TimelineSegment(SourceTime(s), SourceTime(e), id))
            prevEnd = e
        }
        return normalized
    }

    fun keptDuration(segments: List<TimelineSegment>): Double {
        val canonical = normalizeSegments(segments)
        var total = 0.0
        for (seg in canonical) {
            total += (seg.sourceEnd.seconds - seg.sourceStart.seconds)
        }
        return roundMs(total)
    }

    fun sourceToConcat(
        sourceTime: SourceTime,
        segments: List<TimelineSegment>,
        snapGapTo: SnapGapMode = SnapGapMode.NONE
    ): SourceToConcatResult {
        val t = roundMs(sourceTime.seconds)
        val canonical = normalizeSegments(segments)
        var offset = 0.0

        for (i in canonical.indices) {
            val seg = canonical[i]
            val segStart = seg.sourceStart.seconds
            val segEnd = seg.sourceEnd.seconds

            if (t < segStart - TIME_EPSILON) {
                val prevEnd = if (i > 0) canonical[i - 1].sourceEnd else null
                val nextStart = seg.sourceStart
                val reason = if (i == 0) "before_first_segment" else "deleted_gap"
                val nearestConcat = ConcatTime(roundMs(offset))

                when (snapGapTo) {
                    SnapGapMode.NEXT -> return SourceToConcatResult.Ok(
                        time = nearestConcat,
                        segmentIndex = i,
                        snapped = true
                    )
                    SnapGapMode.PREVIOUS -> if (prevEnd != null) {
                        return SourceToConcatResult.Ok(
                            time = nearestConcat,
                            segmentIndex = i - 1,
                            snapped = true
                        )
                    }
                    SnapGapMode.NEAREST -> {
                        if (prevEnd == null) {
                            return SourceToConcatResult.Ok(
                                time = nearestConcat,
                                segmentIndex = 0,
                                snapped = true
                            )
                        }
                        val distPrev = t - prevEnd.seconds
                        val distNext = nextStart.seconds - t
                        val targetIdx = if (distNext <= distPrev) i else i - 1
                        return SourceToConcatResult.Ok(
                            time = nearestConcat,
                            segmentIndex = targetIdx,
                            snapped = true
                        )
                    }
                    SnapGapMode.NONE -> Unit
                }

                return SourceToConcatResult.Gap(
                    reason = reason,
                    sourceTime = SourceTime(t),
                    nearestSegmentStart = nextStart,
                    nearestSegmentEnd = prevEnd,
                    nearestConcatTime = nearestConcat
                )
            }

            if (t <= segEnd + TIME_EPSILON) {
                val local = min(max(t - segStart, 0.0), segEnd - segStart)
                return SourceToConcatResult.Ok(
                    time = ConcatTime(roundMs(offset + local)),
                    segmentIndex = i,
                    snapped = false
                )
            }

            offset = roundMs(offset + (segEnd - segStart))
        }

        val lastEnd = canonical.last().sourceEnd
        val totalConcat = ConcatTime(roundMs(offset))
        if (snapGapTo == SnapGapMode.PREVIOUS || snapGapTo == SnapGapMode.NEAREST) {
            return SourceToConcatResult.Ok(
                time = totalConcat,
                segmentIndex = canonical.lastIndex,
                snapped = true
            )
        }

        return SourceToConcatResult.Gap(
            reason = "after_last_segment",
            sourceTime = SourceTime(t),
            nearestSegmentStart = null,
            nearestSegmentEnd = lastEnd,
            nearestConcatTime = totalConcat
        )
    }

    fun concatToSource(
        concatTime: ConcatTime,
        segments: List<TimelineSegment>,
        clamp: Boolean = false
    ): ConcatToSourceResult {
        val t = roundMs(concatTime.seconds)
        val canonical = normalizeSegments(segments)
        var total = 0.0
        for (seg in canonical) {
            total += (seg.sourceEnd.seconds - seg.sourceStart.seconds)
        }
        total = roundMs(total)

        if (t < -TIME_EPSILON) {
            if (clamp) {
                return ConcatToSourceResult.Ok(
                    time = canonical.first().sourceStart,
                    segmentIndex = 0,
                    clamped = true
                )
            }
            return ConcatToSourceResult.OutOfRange(
                reason = "before_start",
                concatTime = ConcatTime(t),
                minConcatTime = ConcatTime(0.0),
                maxConcatTime = ConcatTime(total),
                clampedTime = canonical.first().sourceStart
            )
        }

        if (t > total + TIME_EPSILON) {
            if (clamp) {
                return ConcatToSourceResult.Ok(
                    time = canonical.last().sourceEnd,
                    segmentIndex = canonical.lastIndex,
                    clamped = true
                )
            }
            return ConcatToSourceResult.OutOfRange(
                reason = "after_end",
                concatTime = ConcatTime(t),
                minConcatTime = ConcatTime(0.0),
                maxConcatTime = ConcatTime(total),
                clampedTime = canonical.last().sourceEnd
            )
        }

        val bounded = min(max(t, 0.0), total)
        var offset = 0.0
        val lastIdx = canonical.lastIndex

        for (i in canonical.indices) {
            val seg = canonical[i]
            val segStart = seg.sourceStart.seconds
            val segEnd = seg.sourceEnd.seconds
            val dur = roundMs(segEnd - segStart)
            val nextOffset = roundMs(offset + dur)

            if (bounded < nextOffset - TIME_EPSILON || i == lastIdx) {
                val local = min(max(bounded - offset, 0.0), dur)
                val src = min(segEnd, roundMs(segStart + local))
                return ConcatToSourceResult.Ok(
                    time = SourceTime(src),
                    segmentIndex = i,
                    clamped = false
                )
            }
            offset = nextOffset
        }

        return ConcatToSourceResult.Ok(
            time = canonical.last().sourceEnd,
            segmentIndex = lastIdx,
            clamped = false
        )
    }

    private fun validateWindow(windowStart: SourceTime, windowEnd: SourceTime): Triple<SourceTime, SourceTime, Double> {
        val ws = roundMs(windowStart.seconds)
        val we = roundMs(windowEnd.seconds)
        require(ws >= 0.0) { "windowStart must be non-negative" }
        require(we > ws) { "windowEnd must be greater than windowStart" }
        return Triple(SourceTime(ws), SourceTime(we), roundMs(we - ws))
    }

    fun sourceToProxy(
        sourceTime: SourceTime,
        windowStart: SourceTime,
        windowEnd: SourceTime,
        clamp: Boolean = false
    ): WindowMappingResult<ProxyTime> {
        val s = roundMs(sourceTime.seconds)
        val (ws, we, dur) = validateWindow(windowStart, windowEnd)

        if (s < ws.seconds - TIME_EPSILON) {
            if (clamp) {
                return WindowMappingResult.Ok(ProxyTime(0.0), clamped = true)
            }
            return WindowMappingResult.OutOfWindow(
                reason = "before_window",
                windowStart = ws,
                windowEnd = we,
                clampedTime = ProxyTime(0.0)
            )
        }

        if (s > we.seconds + TIME_EPSILON) {
            if (clamp) {
                return WindowMappingResult.Ok(ProxyTime(dur), clamped = true)
            }
            return WindowMappingResult.OutOfWindow(
                reason = "after_window",
                windowStart = ws,
                windowEnd = we,
                clampedTime = ProxyTime(dur)
            )
        }

        val mapped = min(max(roundMs(s - ws.seconds), 0.0), dur)
        return WindowMappingResult.Ok(ProxyTime(mapped), clamped = false)
    }

    fun proxyToSource(
        proxyTime: ProxyTime,
        windowStart: SourceTime,
        windowEnd: SourceTime,
        clamp: Boolean = false
    ): WindowMappingResult<SourceTime> {
        val p = roundMs(proxyTime.seconds)
        val (ws, we, dur) = validateWindow(windowStart, windowEnd)

        if (p < -TIME_EPSILON) {
            if (clamp) {
                return WindowMappingResult.Ok(ws, clamped = true)
            }
            return WindowMappingResult.OutOfWindow(
                reason = "before_window",
                windowStart = ws,
                windowEnd = we,
                clampedTime = ws
            )
        }

        if (p > dur + TIME_EPSILON) {
            if (clamp) {
                return WindowMappingResult.Ok(we, clamped = true)
            }
            return WindowMappingResult.OutOfWindow(
                reason = "after_window",
                windowStart = ws,
                windowEnd = we,
                clampedTime = we
            )
        }

        val mapped = min(max(roundMs(ws.seconds + p), ws.seconds), we.seconds)
        return WindowMappingResult.Ok(SourceTime(mapped), clamped = false)
    }

    fun c2ToC3Time(
        proxyTime: ProxyTime,
        windowStart: SourceTime,
        windowEnd: SourceTime? = null
    ): SourceTime {
        val p = roundMs(proxyTime.seconds)
        val ws = roundMs(windowStart.seconds)
        require(ws >= 0.0) { "windowStart must be non-negative" }
        require(p >= 0.0) { "proxyTime $p is before edit window start (0.0)" }
        if (windowEnd != null) {
            val we = roundMs(windowEnd.seconds)
            require(we > ws) { "windowEnd must be greater than windowStart" }
            val windowDur = roundMs(we - ws)
            require(p <= windowDur + 1e-6) { "proxyTime $p exceeds edit window duration $windowDur" }
            return SourceTime(min(we, roundMs(ws + p)))
        }
        return SourceTime(roundMs(ws + p))
    }

    fun c3ToC2Time(
        sourceTime: SourceTime,
        windowStart: SourceTime,
        windowEnd: SourceTime? = null
    ): ProxyTime {
        val s = roundMs(sourceTime.seconds)
        val ws = roundMs(windowStart.seconds)
        require(ws >= 0.0) { "windowStart must be non-negative" }
        require(s >= ws - 1e-6) { "sourceTime $s is before edit window start $ws" }
        if (windowEnd != null) {
            val we = roundMs(windowEnd.seconds)
            require(we > ws) { "windowEnd must be greater than windowStart" }
            require(s <= we + 1e-6) { "sourceTime $s exceeds edit window end $we" }
        }
        return ProxyTime(max(0.0, roundMs(s - ws)))
    }

    fun playbackTimeToSource(
        playbackTimeSeconds: Double,
        descriptor: PlaybackDescriptor,
        clamp: Boolean = false
    ): PlaybackMappingResult {
        return when (descriptor) {
            is PlaybackDescriptor.SourceVideo -> {
                val t = roundMs(playbackTimeSeconds)
                val maxDur = descriptor.sourceDuration?.let { d ->
                    val rd = roundMs(d)
                    require(rd > 0.0) { "sourceDuration must be greater than zero" }
                    rd
                }
                if (t < -TIME_EPSILON) {
                    if (clamp) return PlaybackMappingResult.Ok(0.0, clamped = true)
                    return PlaybackMappingResult.OutOfRange("before_start", 0.0, maxDur, 0.0)
                }
                if (maxDur != null && t > maxDur + TIME_EPSILON) {
                    if (clamp) return PlaybackMappingResult.Ok(maxDur, clamped = true)
                    return PlaybackMappingResult.OutOfRange("after_end", 0.0, maxDur, maxDur)
                }
                val bounded = if (maxDur != null) min(max(t, 0.0), maxDur) else max(t, 0.0)
                PlaybackMappingResult.Ok(roundMs(bounded), clamped = false)
            }
            is PlaybackDescriptor.EditWindow -> {
                when (val res = proxyToSource(ProxyTime(playbackTimeSeconds), descriptor.windowStart, descriptor.windowEnd, clamp)) {
                    is WindowMappingResult.Ok -> PlaybackMappingResult.Ok(res.time.seconds, clamped = res.clamped)
                    is WindowMappingResult.OutOfWindow -> PlaybackMappingResult.OutOfWindow(
                        reason = res.reason,
                        windowStart = res.windowStart,
                        windowEnd = res.windowEnd,
                        clampedTimeSeconds = res.clampedTime.seconds
                    )
                }
            }
            is PlaybackDescriptor.RenderedClip -> {
                when (val res = concatToSource(ConcatTime(playbackTimeSeconds), descriptor.segments, clamp)) {
                    is ConcatToSourceResult.Ok -> PlaybackMappingResult.Ok(
                        timeSeconds = res.time.seconds,
                        clamped = res.clamped,
                        segmentIndex = res.segmentIndex
                    )
                    is ConcatToSourceResult.OutOfRange -> PlaybackMappingResult.OutOfRange(
                        reason = res.reason,
                        minTimeSeconds = res.minConcatTime.seconds,
                        maxTimeSeconds = res.maxConcatTime.seconds,
                        clampedTimeSeconds = res.clampedTime.seconds
                    )
                }
            }
        }
    }

    fun sourceToPlaybackTime(
        sourceTime: SourceTime,
        descriptor: PlaybackDescriptor,
        clamp: Boolean = false,
        snapGapTo: SnapGapMode = SnapGapMode.NONE
    ): PlaybackMappingResult {
        return when (descriptor) {
            is PlaybackDescriptor.SourceVideo -> {
                val t = roundMs(sourceTime.seconds)
                val maxDur = descriptor.sourceDuration?.let { d ->
                    val rd = roundMs(d)
                    require(rd > 0.0) { "sourceDuration must be greater than zero" }
                    rd
                }
                if (t < -TIME_EPSILON) {
                    if (clamp) return PlaybackMappingResult.Ok(0.0, clamped = true)
                    return PlaybackMappingResult.OutOfRange("before_start", 0.0, maxDur, 0.0)
                }
                if (maxDur != null && t > maxDur + TIME_EPSILON) {
                    if (clamp) return PlaybackMappingResult.Ok(maxDur, clamped = true)
                    return PlaybackMappingResult.OutOfRange("after_end", 0.0, maxDur, maxDur)
                }
                val bounded = if (maxDur != null) min(max(t, 0.0), maxDur) else max(t, 0.0)
                PlaybackMappingResult.Ok(roundMs(bounded), clamped = false)
            }
            is PlaybackDescriptor.EditWindow -> {
                when (val res = sourceToProxy(sourceTime, descriptor.windowStart, descriptor.windowEnd, clamp)) {
                    is WindowMappingResult.Ok -> PlaybackMappingResult.Ok(res.time.seconds, clamped = res.clamped)
                    is WindowMappingResult.OutOfWindow -> PlaybackMappingResult.OutOfWindow(
                        reason = res.reason,
                        windowStart = res.windowStart,
                        windowEnd = res.windowEnd,
                        clampedTimeSeconds = res.clampedTime.seconds
                    )
                }
            }
            is PlaybackDescriptor.RenderedClip -> {
                when (val res = sourceToConcat(sourceTime, descriptor.segments, snapGapTo)) {
                    is SourceToConcatResult.Ok -> PlaybackMappingResult.Ok(
                        timeSeconds = res.time.seconds,
                        snapped = res.snapped,
                        segmentIndex = res.segmentIndex
                    )
                    is SourceToConcatResult.Gap -> PlaybackMappingResult.Gap(
                        reason = res.reason,
                        sourceTime = res.sourceTime,
                        nearestSegmentStart = res.nearestSegmentStart,
                        nearestSegmentEnd = res.nearestSegmentEnd,
                        nearestConcatTime = res.nearestConcatTime
                    )
                }
            }
        }
    }

    fun canonicalMediaSpans(ranges: List<TimelineSegment>): List<Pair<Double, Double>> {
        if (ranges.isEmpty()) return emptyList()
        val normalized = ranges.map { seg ->
            val s = roundMs(seg.sourceStart.seconds)
            val e = roundMs(seg.sourceEnd.seconds)
            require(s >= 0.0 && e > s) { "invalid segment bounds: [$s, $e]" }
            s to e
        }.sortedBy { it.first }

        val merged = mutableListOf(normalized[0])
        for (i in 1 until normalized.size) {
            val (s, e) = normalized[i]
            val prev = merged.last()
            if (s < prev.second - TIME_EPSILON) {
                throw IllegalArgumentException("overlapping segments detected: [${prev.first}, ${prev.second}] and [$s, $e]")
            } else if (abs(s - prev.second) <= 1e-6) {
                merged[merged.lastIndex] = prev.first to e
            } else {
                merged.add(s to e)
            }
        }
        return merged.map { (s, e) -> roundMs(s) to roundMs(e) }
    }

    fun canonicalSpansStr(ranges: List<TimelineSegment>): String {
        val spans = canonicalMediaSpans(ranges)
        return "[" + spans.joinToString(",") { (s, e) ->
            String.format(Locale.US, "%.3f-%.3f", s, e)
        } + "]"
    }

    fun formatRfc8785Double(value: Double): String {
        require(value.isFinite()) { "non-finite numbers (NaN/Infinity) are not valid in RFC-8785 JSON" }
        if (value == 0.0) return "0"

        val sign = if (value < 0.0) "-" else ""
        val sRepr = java.lang.Double.toString(abs(value))
        val digits: String
        val n: Int

        val eIdx = sRepr.indexOf('E').let { if (it >= 0) it else sRepr.indexOf('e') }
        if (eIdx >= 0) {
            val mantissa = sRepr.substring(0, eIdx)
            val exp = sRepr.substring(eIdx + 1).toInt()
            digits = mantissa.replace(".", "").trimEnd('0')
            n = exp + 1
        } else {
            val dotIdx = sRepr.indexOf('.')
            if (dotIdx >= 0) {
                val intPart = sRepr.substring(0, dotIdx)
                val fracPart = sRepr.substring(dotIdx + 1)
                if (intPart != "0") {
                    digits = (intPart + fracPart).trimEnd('0')
                    n = intPart.length
                } else {
                    val stripped = fracPart.trimStart('0')
                    val leadingZeros = fracPart.length - stripped.length
                    digits = stripped.trimEnd('0')
                    n = -leadingZeros
                }
            } else {
                digits = sRepr.trimEnd('0')
                n = sRepr.length
            }
        }

        val k = digits.length
        return when {
            k <= n && n <= 21 -> sign + digits + "0".repeat(n - k)
            n in 1..21 -> sign + digits.substring(0, n) + "." + digits.substring(n)
            n in -5..0 -> sign + "0." + "0".repeat(-n) + digits
            else -> {
                val expVal = n - 1
                val expSign = if (expVal >= 0) "+" else "-"
                val mant = if (k == 1) digits else "${digits[0]}.${digits.substring(1)}"
                "$sign${mant}e$expSign${abs(expVal)}"
            }
        }
    }

    private fun escapeRfc8785String(str: String): String {
        val sb = StringBuilder(str.length + 2)
        sb.append('"')
        for (i in str.indices) {
            val ch = str[i]
            when {
                ch == '"' -> sb.append("\\\"")
                ch == '\\' -> sb.append("\\\\")
                ch == '\b' -> sb.append("\\b")
                ch == '\u000C' -> sb.append("\\f")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch.code < 0x20 -> sb.append(String.format(Locale.US, "\\u%04x", ch.code))
                else -> sb.append(ch)
            }
        }
        sb.append('"')
        return sb.toString()
    }

    /**
     * RFC 8785 (JCS) canonical JSON serialization supporting Kotlin primitives/collections,
     * Gson JsonElement trees, and org.json JSONObject/JSONArray trees.
     */
    fun canonicalJsonStringify(value: Any?): String {
        return when (value) {
            null, is JsonNull -> "null"
            is Boolean -> if (value) "true" else "false"
            is String -> escapeRfc8785String(value)
            is Int -> value.toString()
            is Short -> value.toInt().toString()
            is Byte -> value.toInt().toString()
            is Long -> {
                if (value in -9007199254740991L..9007199254740991L) {
                    value.toString()
                } else {
                    formatRfc8785Double(value.toDouble())
                }
            }
            is Float -> {
                require(value.isFinite()) { "non-finite numbers (NaN/Infinity) are not valid in RFC-8785 JSON" }
                formatRfc8785Double(value.toString().toDouble())
            }
            is Double -> formatRfc8785Double(value)
            is BigInteger -> {
                val l = value.toLong()
                if (BigInteger.valueOf(l) == value && l in -9007199254740991L..9007199254740991L) {
                    l.toString()
                } else {
                    formatRfc8785Double(value.toDouble())
                }
            }
            is BigDecimal -> formatRfc8785Double(value.toDouble())
            is Number -> formatRfc8785Double(value.toDouble())
            is JsonPrimitive -> when {
                value.isBoolean -> if (value.asBoolean) "true" else "false"
                value.isString -> escapeRfc8785String(value.asString)
                value.isNumber -> {
                    val raw = value.asString
                    if (raw == "NaN" || raw == "Infinity" || raw == "-Infinity") {
                        throw IllegalArgumentException("non-finite numbers (NaN/Infinity) are not valid in RFC-8785 JSON")
                    }
                    formatRfc8785Double(value.asDouble)
                }
                else -> "null"
            }
            is JsonArray -> buildString {
                append('[')
                for (i in 0 until value.size()) {
                    if (i > 0) append(',')
                    append(canonicalJsonStringify(value.get(i)))
                }
                append(']')
            }
            is JsonObject -> {
                val entries = value.entrySet().sortedWith { a, b -> a.key.compareTo(b.key) }
                buildString {
                    append('{')
                    var first = true
                    for ((k, v) in entries) {
                        if (!first) append(',')
                        first = false
                        append(escapeRfc8785String(k))
                        append(':')
                        append(canonicalJsonStringify(v))
                    }
                    append('}')
                }
            }
            is JSONArray -> buildString {
                append('[')
                for (i in 0 until value.length()) {
                    if (i > 0) append(',')
                    append(canonicalJsonStringify(value.get(i)))
                }
                append(']')
            }
            is JSONObject -> {
                val keys = value.keys().asSequence().toList().sortedWith { a, b -> a.compareTo(b) }
                buildString {
                    append('{')
                    var first = true
                    for (k in keys) {
                        if (!first) append(',')
                        first = false
                        append(escapeRfc8785String(k))
                        append(':')
                        append(canonicalJsonStringify(value.get(k)))
                    }
                    append('}')
                }
            }
            is Iterable<*> -> buildString {
                append('[')
                var first = true
                for (item in value) {
                    if (!first) append(',')
                    first = false
                    append(canonicalJsonStringify(item))
                }
                append(']')
            }
            is Array<*> -> buildString {
                append('[')
                for (i in value.indices) {
                    if (i > 0) append(',')
                    append(canonicalJsonStringify(value[i]))
                }
                append(']')
            }
            is Map<*, *> -> {
                val entries = value.entries
                    .map { (k, v) -> (k?.toString() ?: "null") to v }
                    .sortedWith { a, b -> a.first.compareTo(b.first) }
                buildString {
                    append('{')
                    var first = true
                    for ((k, v) in entries) {
                        if (!first) append(',')
                        first = false
                        append(escapeRfc8785String(k))
                        append(':')
                        append(canonicalJsonStringify(v))
                    }
                    append('}')
                }
            }
            else -> {
                if (value == JSONObject.NULL) {
                    "null"
                } else {
                    throw IllegalArgumentException("unsupported type for RFC-8785 canonical JSON: ${value::class.java.name}")
                }
            }
        }
    }

    fun canonicalizeJsonText(rawJson: String): String {
        val parsed = JsonParser.parseString(rawJson)
        return canonicalJsonStringify(parsed)
    }

    fun sha256Hex(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hashBytes = digest.digest(input.toByteArray(Charsets.UTF_8))
        return buildString(64) {
            for (b in hashBytes) {
                val v = b.toInt() and 0xFF
                if (v < 16) append('0')
                append(v.toString(16))
            }
        }
    }

    fun computeMediaFingerprint(sourceAssetId: String, ranges: List<TimelineSegment>): String {
        val trimmed = sourceAssetId.trim()
        require(trimmed.isNotEmpty()) { "sourceAssetId must be a non-empty string" }
        val spansStr = canonicalSpansStr(ranges)
        return sha256Hex("v1:media:$trimmed:$spansStr")
    }

    fun computeRenderFingerprint(
        mediaFingerprint: String,
        layoutMode: String,
        cropAnchorX: Double,
        layers: List<Any?> = emptyList(),
        settings: Map<String, Any?>? = null
    ): String {
        require(mediaFingerprint.length == 64) { "mediaFingerprint must be a valid 64-character SHA-256 hex string" }
        val layout = layoutMode.trim().ifEmpty { "portrait_9_16" }
        val cropStr = String.format(Locale.US, "%.3f", roundMs(cropAnchorX))
        val layersStr = canonicalJsonStringify(layers)
        val layersHash = sha256Hex(layersStr)

        val settingsStr = canonicalJsonStringify(settings ?: emptyMap<String, Any?>())
        var payload = "v1:render:$mediaFingerprint:$layout:$cropStr:$layersHash"
        if (settingsStr != "{}") {
            payload += ":${sha256Hex(settingsStr)}"
        }
        return sha256Hex(payload)
    }

    fun computeRenderFingerprintFromJson(
        mediaFingerprint: String,
        layoutMode: String,
        cropAnchorX: Double,
        layers: JsonElement? = null,
        settings: JsonElement? = null
    ): String {
        require(mediaFingerprint.length == 64) { "mediaFingerprint must be a valid 64-character SHA-256 hex string" }
        val layout = layoutMode.trim().ifEmpty { "portrait_9_16" }
        val cropStr = String.format(Locale.US, "%.3f", roundMs(cropAnchorX))
        val layersElement: JsonElement = if (layers != null && layers.isJsonArray) layers else JsonArray()
        val layersStr = canonicalJsonStringify(layersElement)
        val layersHash = sha256Hex(layersStr)

        val settingsElement: JsonElement = if (settings != null && settings.isJsonObject) settings else JsonObject()
        val settingsStr = canonicalJsonStringify(settingsElement)
        var payload = "v1:render:$mediaFingerprint:$layout:$cropStr:$layersHash"
        if (settingsStr != "{}") {
            payload += ":${sha256Hex(settingsStr)}"
        }
        return sha256Hex(payload)
    }
}
