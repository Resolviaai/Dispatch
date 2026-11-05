package com.resolvia.dispatch.renderer

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaItem.ClippingConfiguration
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.CanvasOverlay
import androidx.media3.effect.Crop
import androidx.media3.effect.OverlayEffect
import androidx.media3.effect.Presentation
import androidx.media3.effect.TextureOverlay
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.EditedMediaItemSequence
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import com.google.common.collect.ImmutableList
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileOutputStream
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Hardware-accelerated on-device video renderer for Dispatch Mobile using Android Jetpack Media3 Transformer.
 * Performs multi-segment trimming and concatenation, 9:16 layout presentation with custom crop positioning,
 * and frame-accurate word-level karaoke caption rendering with 1.08x scale pop, outline stroke, and text shadows.
 */
@OptIn(UnstableApi::class)
class AndroidClipRenderer(
    private val context: Context,
    private val coroutineScope: CoroutineScope = CoroutineScope(Dispatchers.Default)
) {
    companion object {
        private const val TAG = "AndroidClipRenderer"
        private const val PROGRESS_POLL_INTERVAL_MS = 250L

        fun parseHexOrRgbaColor(colorStr: String?, defaultColor: Int): Int {
            if (colorStr.isNullOrBlank()) return defaultColor
            val trimmed = colorStr.trim()
            try {
                if (trimmed.startsWith("#")) {
                    val hex = trimmed.substring(1)
                    when (hex.length) {
                        3 -> {
                            val r = hex[0].toString().repeat(2).toInt(16)
                            val g = hex[1].toString().repeat(2).toInt(16)
                            val b = hex[2].toString().repeat(2).toInt(16)
                            return (0xFF shl 24) or (r shl 16) or (g shl 8) or b
                        }
                        4 -> {
                            val r = hex[0].toString().repeat(2).toInt(16)
                            val g = hex[1].toString().repeat(2).toInt(16)
                            val b = hex[2].toString().repeat(2).toInt(16)
                            val a = hex[3].toString().repeat(2).toInt(16)
                            return (a shl 24) or (r shl 16) or (g shl 8) or b
                        }
                        6 -> {
                            val rgb = hex.toInt(16)
                            return (0xFF shl 24) or rgb
                        }
                        8 -> {
                            val r = hex.substring(0, 2).toInt(16)
                            val g = hex.substring(2, 4).toInt(16)
                            val b = hex.substring(4, 6).toInt(16)
                            val a = hex.substring(6, 8).toInt(16)
                            return (a shl 24) or (r shl 16) or (g shl 8) or b
                        }
                    }
                }
                val rgbaRegex = Regex("""rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)""", RegexOption.IGNORE_CASE)
                val match = rgbaRegex.matchEntire(trimmed)
                if (match != null) {
                    val r = match.groupValues[1].toInt().coerceIn(0, 255)
                    val g = match.groupValues[2].toInt().coerceIn(0, 255)
                    val b = match.groupValues[3].toInt().coerceIn(0, 255)
                    val aStr = match.groupValues[4]
                    val a = if (aStr.isNotEmpty()) {
                        val alphaFloat = aStr.toFloat().coerceIn(0f, 1f)
                        (alphaFloat * 255f).toInt()
                    } else 255
                    return (a shl 24) or (r shl 16) or (g shl 8) or b
                }
            } catch (e: Exception) {
                // Return default fallback
            }
            return defaultColor
        }

        fun applyOpacityToColor(color: Int, opacity: Float): Int {
            if (opacity >= 0.999f) return color
            val clamped = opacity.coerceIn(0f, 1f)
            val origAlpha = (color ushr 24) and 0xFF
            val newAlpha = (origAlpha * clamped).toInt()
            return (newAlpha shl 24) or (color and 0x00FFFFFF)
        }

        fun applyCaseTransform(text: String, transform: String?): String {
            return when (transform?.lowercase(Locale.ROOT)) {
                "uppercase" -> text.uppercase(Locale.ROOT)
                "lowercase" -> text.lowercase(Locale.ROOT)
                "title" -> text.split(" ").joinToString(" ") { word ->
                    word.replaceFirstChar { if (it.isLowerCase()) it.titlecase(Locale.ROOT) else it.toString() }
                }
                else -> text
            }
        }

        fun getFontAssetFilename(fontFamily: String?): String {
            if (fontFamily.isNullOrBlank()) return "Montserrat-Variable.ttf"
            val clean = fontFamily.trim().lowercase(Locale.ROOT)
            return when {
                clean.contains("impact") -> "impact.ttf"
                clean.contains("anton") -> "Anton-Regular.ttf"
                clean.contains("archivo") -> "ArchivoBlack-Regular.ttf"
                clean.contains("bebas") -> "BebasNeue-Regular.ttf"
                clean.contains("banger") -> "Bangers-Regular.ttf"
                clean.contains("luckiest") -> "LuckiestGuy-Regular.ttf"
                clean.contains("marker") -> "PermanentMarker-Regular.ttf"
                clean.contains("titan") -> "TitanOne-Regular.ttf"
                clean.contains("russo") -> "RussoOne-Regular.ttf"
                clean.contains("bungee") -> "Bungee-Regular.ttf"
                clean.contains("barlow") -> "BarlowCondensed-Bold.ttf"
                clean.contains("poppins") -> "Poppins-Bold.ttf"
                clean.contains("inter") -> "Inter-Variable.ttf"
                clean.contains("roboto") -> "Roboto-Variable.ttf"
                clean.contains("open sans") -> "OpenSans-Variable.ttf"
                clean.contains("oswald") -> "Oswald-Variable.ttf"
                clean.contains("space grotesk") -> "SpaceGrotesk-Variable.ttf"
                clean.contains("devanagari") -> "NotoSansDevanagari-Variable.ttf"
                clean.contains("noto") -> "NotoSans-Variable.ttf"
                clean.contains("montserrat") -> "Montserrat-Variable.ttf"
                clean.contains("arial black") -> "ArchivoBlack-Regular.ttf"
                clean.contains("comic") -> "Bangers-Regular.ttf"
                else -> "Montserrat-Variable.ttf"
            }
        }

        private val fontCache = ConcurrentHashMap<String, Typeface>()

        fun getTypeface(context: Context?, fontFamily: String?, isBold: Boolean, isItalic: Boolean): Typeface {
            val family = fontFamily?.trim() ?: "Montserrat"
            val cacheKey = "$family-$isBold-$isItalic"
            return fontCache.computeIfAbsent(cacheKey) {
                val assetFilename = getFontAssetFilename(family)
                if (context != null) {
                    try {
                        Typeface.createFromAsset(context.assets, "fonts/$assetFilename")
                    } catch (_: Exception) {
                        fallbackTypeface(family, isBold, isItalic)
                    }
                } else {
                    fallbackTypeface(family, isBold, isItalic)
                }
            }
        }

        private fun fallbackTypeface(family: String, isBold: Boolean, isItalic: Boolean): Typeface {
            val style = when {
                isBold && isItalic -> Typeface.BOLD_ITALIC
                isBold -> Typeface.BOLD
                isItalic -> Typeface.ITALIC
                else -> Typeface.NORMAL
            }
            return try {
                Typeface.create(family, style) ?: (if (isBold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT)
            } catch (_: Exception) {
                if (isBold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            }
        }

        data class VisualWord(
            val rawWord: CaptionWord,
            val text: String,
            val width: Float
        )

        data class VisualLine(
            val words: List<VisualWord>,
            val lineWidth: Float
        )

        fun partitionWordsIntoVisualLines(
            words: List<CaptionWord>,
            textPaint: Paint,
            maxWidthPx: Float,
            textTransform: String?,
            letterSpacing: Float
        ): List<VisualLine> {
            if (words.isEmpty()) return emptyList()

            val spaceWidth = textPaint.measureText(" ")
            val lines = mutableListOf<VisualLine>()
            var currentLineWords = mutableListOf<VisualWord>()
            var currentLineWidth = 0f

            for (word in words) {
                val transformed = applyCaseTransform(word.text, textTransform).trim()
                if (transformed.isEmpty()) continue

                // Support explicit newlines inside words
                if (transformed.contains("\n")) {
                    val parts = transformed.split("\n")
                    for (pIdx in parts.indices) {
                        val part = parts[pIdx].trim()
                        if (pIdx > 0 && currentLineWords.isNotEmpty()) {
                            val actualWidth = (currentLineWidth - spaceWidth).coerceAtLeast(0f)
                            lines.add(VisualLine(currentLineWords.toList(), actualWidth))
                            currentLineWords = mutableListOf()
                            currentLineWidth = 0f
                        }
                        if (part.isNotEmpty()) {
                            val wWidth = textPaint.measureText(part) + letterSpacing * part.length
                            val vWord = VisualWord(word.copy(text = part), part, wWidth)
                            currentLineWords.add(vWord)
                            currentLineWidth += wWidth + spaceWidth
                        }
                    }
                    continue
                }

                val wWidth = textPaint.measureText(transformed) + letterSpacing * transformed.length
                val vWord = VisualWord(word.copy(text = transformed), transformed, wWidth)

                if (currentLineWords.isNotEmpty() && (currentLineWidth + wWidth > maxWidthPx)) {
                    val actualWidth = (currentLineWidth - spaceWidth).coerceAtLeast(0f)
                    lines.add(VisualLine(currentLineWords.toList(), actualWidth))
                    currentLineWords = mutableListOf(vWord)
                    currentLineWidth = wWidth + spaceWidth
                } else {
                    currentLineWords.add(vWord)
                    currentLineWidth += wWidth + spaceWidth
                }
            }

            if (currentLineWords.isNotEmpty()) {
                val actualWidth = (currentLineWidth - spaceWidth).coerceAtLeast(0f)
                lines.add(VisualLine(currentLineWords.toList(), actualWidth))
            }

            return lines
        }

        fun parseOverlayGroups(docObj: JsonObject): List<CaptionGroup> {
            val groups = mutableListOf<CaptionGroup>()
            val tracks = docObj.getAsJsonArray("tracks") ?: return groups

            for (i in 0 until tracks.size()) {
                val track = tracks.get(i).asJsonObject
                val kind = track.get("kind")?.asString ?: track.get("type")?.asString ?: ""
                val isCaption = (kind == "captions" || kind == "caption")
                val isTextOrOverlay = (kind == "text" || kind == "overlay")
                if (!isCaption && !isTextOrOverlay) continue

                val defaultStyle = track.getAsJsonObject("default_style") ?: track.getAsJsonObject("style") ?: JsonObject()
                val defaultLayout = track.getAsJsonObject("default_layout") ?: track.getAsJsonObject("layout") ?: JsonObject()
                val trackAnim = track.getAsJsonObject("animation") ?: JsonObject()

                val groupsArray = track.getAsJsonArray("groups") ?: continue
                for (j in 0 until groupsArray.size()) {
                    val g = groupsArray.get(j).asJsonObject
                    val gId = g.get("id")?.asString ?: "grp_${kind}_$j"
                    val gStart = g.get("start")?.asFloat ?: 0f
                    val gEnd = g.get("end")?.asFloat ?: 0f

                    val words = mutableListOf<CaptionWord>()
                    val wordsArray = g.getAsJsonArray("words")
                    if (wordsArray != null) {
                        for (k in 0 until wordsArray.size()) {
                            val w = wordsArray.get(k).asJsonObject
                            val text = w.get("text")?.asString ?: w.get("word")?.asString ?: ""
                            val wStart = w.get("start")?.asFloat ?: gStart
                            val wEnd = w.get("end")?.asFloat ?: gEnd
                            if (text.isNotEmpty()) {
                                words.add(CaptionWord(text, wStart, wEnd))
                            }
                        }
                    }

                    if (words.isEmpty()) {
                        val rawText = g.get("text")?.asString ?: g.get("label")?.asString ?: ""
                        if (rawText.isNotEmpty()) {
                            words.add(CaptionWord(rawText, gStart, gEnd))
                        }
                    }

                    if (gEnd > gStart && words.isNotEmpty()) {
                        val groupStyle = g.getAsJsonObject("style") ?: defaultStyle
                        val groupLayout = g.getAsJsonObject("layout") ?: defaultLayout
                        val groupAnim = g.getAsJsonObject("animation") ?: trackAnim

                        // Layout extraction
                        val posX = groupLayout.get("position_x")?.asFloat ?: defaultLayout.get("position_x")?.asFloat ?: 0.5f
                        val defaultPosY = if (isCaption) 0.78f else 0.50f
                        val posY = groupLayout.get("position_y")?.asFloat ?: defaultLayout.get("position_y")?.asFloat ?: defaultPosY
                        val alignment = groupLayout.get("alignment")?.asString ?: defaultLayout.get("alignment")?.asString ?: "center"
                        val maxWidth = groupLayout.get("max_width")?.asFloat ?: defaultLayout.get("max_width")?.asFloat ?: 0.88f

                        // Style extraction
                        val primaryColorStr = groupStyle.get("primary_color")?.asString ?: groupStyle.get("color")?.asString ?: defaultStyle.get("primary_color")?.asString ?: defaultStyle.get("color")?.asString
                        val highlightColorStr = groupStyle.get("highlight_color")?.asString ?: defaultStyle.get("highlight_color")?.asString
                        val fontSize = groupStyle.get("font_size")?.asFloat ?: defaultStyle.get("font_size")?.asFloat ?: (if (isCaption) 52f else 36f)

                        val fontFamily = groupStyle.get("font_family")?.asString ?: defaultStyle.get("font_family")?.asString ?: "Montserrat"
                        val fontWeight = groupStyle.get("font_weight")?.asString ?: defaultStyle.get("font_weight")?.asString ?: "bold"
                        val fontStyle = groupStyle.get("font_style")?.asString ?: defaultStyle.get("font_style")?.asString ?: "normal"
                        val textTransform = groupStyle.get("text_transform")?.asString ?: defaultStyle.get("text_transform")?.asString ?: "none"
                        val lineHeight = groupStyle.get("line_height")?.asFloat ?: defaultStyle.get("line_height")?.asFloat ?: 1.25f
                        val letterSpacing = groupStyle.get("letter_spacing")?.asFloat ?: defaultStyle.get("letter_spacing")?.asFloat ?: 0f

                        val textOpacity = groupStyle.get("text_opacity")?.asFloat ?: groupStyle.get("opacity")?.asFloat ?: 1.0f
                        val parsedPrimaryColor = applyOpacityToColor(parseHexOrRgbaColor(primaryColorStr, -1), textOpacity)
                        val parsedHighlightColor = applyOpacityToColor(parseHexOrRgbaColor(highlightColorStr, 0xFFFFE600.toInt()), textOpacity)

                        val outlineEnabled = groupStyle.get("outline_enabled")?.asBoolean
                            ?: groupStyle.get("stroke_enabled")?.asBoolean
                            ?: defaultStyle.get("outline_enabled")?.asBoolean
                            ?: defaultStyle.get("stroke_enabled")?.asBoolean
                            ?: false
                        val outlineColorStr = groupStyle.get("outline_color")?.asString ?: groupStyle.get("stroke_color")?.asString ?: defaultStyle.get("outline_color")?.asString ?: defaultStyle.get("stroke_color")?.asString
                        val outlineWidth = groupStyle.get("outline_width")?.asFloat ?: groupStyle.get("stroke_width")?.asFloat ?: defaultStyle.get("outline_width")?.asFloat ?: defaultStyle.get("stroke_width")?.asFloat ?: 4f
                        val outlineOpacity = groupStyle.get("outline_opacity")?.asFloat ?: defaultStyle.get("outline_opacity")?.asFloat ?: 1.0f
                        val parsedOutlineColor = applyOpacityToColor(parseHexOrRgbaColor(outlineColorStr, 0xFF000000.toInt()), outlineOpacity)

                        val shadowEnabled = groupStyle.get("shadow_enabled")?.asBoolean ?: defaultStyle.get("shadow_enabled")?.asBoolean ?: true
                        val shadowColorStr = groupStyle.get("shadow_color")?.asString ?: defaultStyle.get("shadow_color")?.asString
                        val shadowOffsetX = groupStyle.get("shadow_offset_x")?.asFloat ?: defaultStyle.get("shadow_offset_x")?.asFloat ?: 2f
                        val shadowOffsetY = groupStyle.get("shadow_offset_y")?.asFloat ?: defaultStyle.get("shadow_offset_y")?.asFloat ?: 4f
                        val shadowBlur = groupStyle.get("shadow_blur")?.asFloat ?: defaultStyle.get("shadow_blur")?.asFloat ?: 6f
                        val shadowOpacity = groupStyle.get("shadow_opacity")?.asFloat ?: defaultStyle.get("shadow_opacity")?.asFloat ?: 1.0f
                        val parsedShadowColor = applyOpacityToColor(parseHexOrRgbaColor(shadowColorStr, 0xC8000000.toInt()), shadowOpacity)

                        val backgroundEnabled = groupStyle.get("background_enabled")?.asBoolean ?: defaultStyle.get("background_enabled")?.asBoolean ?: isCaption
                        val backgroundColorStr = groupStyle.get("background_color")?.asString ?: defaultStyle.get("background_color")?.asString
                        val backgroundOpacity = groupStyle.get("background_opacity")?.asFloat ?: defaultStyle.get("background_opacity")?.asFloat ?: 1.0f
                        val parsedBackgroundColor = applyOpacityToColor(parseHexOrRgbaColor(backgroundColorStr, 0xB4000000.toInt()), backgroundOpacity)

                        val backgroundMode = groupStyle.get("background_mode")?.asString ?: defaultStyle.get("background_mode")?.asString ?: (if (isCaption) "block" else "none")
                        val backgroundCornerRadius = groupStyle.get("background_corner_radius")?.asFloat ?: defaultStyle.get("background_corner_radius")?.asFloat ?: 16f
                        val backgroundPaddingX = groupStyle.get("background_padding_x")?.asFloat ?: defaultStyle.get("background_padding_x")?.asFloat ?: 24f
                        val backgroundPaddingY = groupStyle.get("background_padding_y")?.asFloat ?: defaultStyle.get("background_padding_y")?.asFloat ?: 16f

                        // Animation extraction
                        val activeWordObj = groupAnim.getAsJsonObject("active_word")
                        val activeAnimType = activeWordObj?.get("type")?.asString
                            ?: groupAnim.get("type")?.asString
                            ?: groupAnim.get("active_word_effect")?.asString
                            ?: "pop"
                        val activeAnimDurationMs = activeWordObj?.get("duration_ms")?.asInt
                            ?: groupAnim.get("duration_ms")?.asInt
                            ?: 120
                        val activeScaleMax = activeWordObj?.get("active_scale")?.asFloat
                            ?: groupAnim.get("active_scale")?.asFloat
                            ?: 1.16f
                        val highlightEnabled = groupStyle.get("highlight_enabled")?.asBoolean
                            ?: defaultStyle.get("highlight_enabled")?.asBoolean
                            ?: true

                        groups.add(
                            CaptionGroup(
                                id = gId,
                                start = gStart,
                                end = gEnd,
                                words = words,
                                primaryColor = parsedPrimaryColor,
                                highlightColor = parsedHighlightColor,
                                fontSizeSp = fontSize,
                                backgroundEnabled = backgroundEnabled,
                                isCaption = isCaption,
                                positionX = posX,
                                positionY = posY,
                                alignment = alignment,
                                strokeEnabled = outlineEnabled,
                                strokeColor = parsedOutlineColor,
                                strokeWidthPx = outlineWidth,
                                shadowEnabled = shadowEnabled,
                                shadowColor = parsedShadowColor,
                                shadowOffsetX = shadowOffsetX,
                                shadowOffsetY = shadowOffsetY,
                                shadowRadius = shadowBlur,
                                backgroundColor = parsedBackgroundColor,
                                fontFamily = fontFamily,
                                fontWeight = fontWeight,
                                fontStyle = fontStyle,
                                textTransform = textTransform,
                                maxWidth = maxWidth,
                                lineHeight = lineHeight,
                                letterSpacing = letterSpacing,
                                backgroundMode = backgroundMode,
                                backgroundCornerRadius = backgroundCornerRadius,
                                backgroundPaddingX = backgroundPaddingX,
                                backgroundPaddingY = backgroundPaddingY,
                                activeAnimType = activeAnimType,
                                activeAnimDurationMs = activeAnimDurationMs,
                                activeScaleMax = activeScaleMax,
                                highlightEnabled = highlightEnabled
                            )
                        )
                    }
                }
            }
            return groups
        }

        fun parseCaptionGroups(docObj: JsonObject): List<CaptionGroup> {
            return parseOverlayGroups(docObj)
        }

        fun remapOverlayGroupsToTimeline(
            groups: List<CaptionGroup>,
            segmentSpans: List<Pair<Long, Long>>,
            clipStartSec: Float
        ): List<CaptionGroup> {
            if (groups.isEmpty() || segmentSpans.size <= 1) return groups

            val remapped = mutableListOf<CaptionGroup>()
            var offsetSec = 0f

            for ((segStartMs, segEndMs) in segmentSpans) {
                val sSec = segStartMs / 1000f
                val eSec = segEndMs / 1000f
                val segDur = eSec - sSec

                for (group in groups) {
                    val gSrcStart = clipStartSec + group.start
                    val gSrcEnd = clipStartSec + group.end

                    val overlapStart = maxOf(gSrcStart, sSec)
                    val overlapEnd = minOf(gSrcEnd, eSec)

                    if (overlapEnd - overlapStart > 0.005f) {
                        val remappedWords = mutableListOf<CaptionWord>()
                        for (w in group.words) {
                            val wSrcStart = clipStartSec + w.start
                            val wSrcEnd = clipStartSec + w.end

                            val wOverlapStart = maxOf(wSrcStart, sSec)
                            val wOverlapEnd = minOf(wSrcEnd, eSec)

                            if (wOverlapEnd - wOverlapStart > 0.005f) {
                                val wMappedStart = offsetSec + (wOverlapStart - sSec)
                                val wMappedEnd = offsetSec + (wOverlapEnd - sSec)
                                remappedWords.add(CaptionWord(w.text, wMappedStart, wMappedEnd))
                            }
                        }

                        if (remappedWords.isNotEmpty() || group.words.isEmpty()) {
                            val gMappedStart = if (remappedWords.isNotEmpty()) remappedWords.first().start else offsetSec + (overlapStart - sSec)
                            val gMappedEnd = if (remappedWords.isNotEmpty()) remappedWords.last().end else offsetSec + (overlapEnd - sSec)
                            remapped.add(
                                group.copy(
                                    start = gMappedStart,
                                    end = gMappedEnd,
                                    words = if (remappedWords.isNotEmpty()) remappedWords else group.words
                                )
                            )
                        }
                    }
                }
                offsetSec += segDur
            }
            return remapped
        }
    }

    data class RenderOutcome(
        val success: Boolean,
        val outputPath: String? = null,
        val thumbnailPath: String? = null,
        val durationSeconds: Float = 0f,
        val errorMessage: String? = null
    )

    private val gson = Gson()
    private var activeTransformer: Transformer? = null
    private var progressJob: Job? = null

    data class CaptionWord(
        val text: String,
        val start: Float,
        val end: Float
    )

    data class CaptionGroup(
        val id: String,
        val start: Float,
        val end: Float,
        val words: List<CaptionWord>,
        val primaryColor: Int = 0xFFFFFFFF.toInt(),
        val highlightColor: Int = 0xFFFFE600.toInt(),
        val fontSizeSp: Float = 36f,
        val backgroundEnabled: Boolean = true,
        val isCaption: Boolean = true,
        val positionX: Float = 0.5f,
        val positionY: Float = 0.78f,
        val alignment: String = "center",
        val strokeEnabled: Boolean = false,
        val strokeColor: Int = 0xFF000000.toInt(),
        val strokeWidthPx: Float = 4f,
        val shadowEnabled: Boolean = true,
        val shadowColor: Int = 0xC8000000.toInt(),
        val shadowOffsetX: Float = 2f,
        val shadowOffsetY: Float = 4f,
        val shadowRadius: Float = 6f,
        val backgroundColor: Int = 0xB4000000.toInt(),
        val fontFamily: String = "Montserrat",
        val fontWeight: String = "bold",
        val fontStyle: String = "normal",
        val textTransform: String = "none",
        val maxWidth: Float = 0.88f,
        val lineHeight: Float = 1.25f,
        val letterSpacing: Float = 0f,
        val backgroundMode: String = "block",
        val backgroundCornerRadius: Float = 16f,
        val backgroundPaddingX: Float = 24f,
        val backgroundPaddingY: Float = 16f,
        val activeAnimType: String = "pop",
        val activeAnimDurationMs: Int = 120,
        val activeScaleMax: Float = 1.16f,
        val highlightEnabled: Boolean = true
    )

    data class RenderRequest(
        val jobId: String,
        val clipId: String,
        val renderFingerprint: String,
        val projectDocJson: String,
        val sourceVideoFile: File,
        val outputFile: File,
        val thumbnailFile: File? = null,
        val targetResolution: String = "1080p", // "1080p" or "720p"
        val layoutMode: String = "portrait_9_16",
        val cropAnchorX: Float = 0.5f
    )

    interface RenderCallback {
        fun onProgress(percent: Int)
        fun onCompleted(outputFile: File, thumbnailFile: File?, actualDurationSeconds: Float)
        fun onError(error: Throwable)
    }

    suspend fun renderClip(
        job: com.resolvia.dispatch.data.RenderJobEntity,
        onProgress: (Int) -> Unit
    ): RenderOutcome = kotlinx.coroutines.suspendCancellableCoroutine { cont ->
        val req = RenderRequest(
            jobId = job.jobId,
            clipId = job.clipId,
            renderFingerprint = job.renderFingerprint,
            projectDocJson = job.projectDocumentJson,
            sourceVideoFile = File(job.sourceVideoPath),
            outputFile = File(job.outputVideoPath),
            thumbnailFile = job.thumbnailPath?.let { File(it) },
            targetResolution = job.targetResolution,
            layoutMode = job.layoutMode,
            cropAnchorX = job.cropAnchorX
        )

        startRender(req, object : RenderCallback {
            override fun onProgress(percent: Int) {
                onProgress(percent)
            }

            override fun onCompleted(outputFile: File, thumbnailFile: File?, actualDurationSeconds: Float) {
                if (cont.isActive) {
                    cont.resume(
                        RenderOutcome(
                            success = true,
                            outputPath = outputFile.absolutePath,
                            thumbnailPath = thumbnailFile?.absolutePath,
                            durationSeconds = actualDurationSeconds
                        ),
                        onCancellation = null
                    )
                }
            }

            override fun onError(error: Throwable) {
                if (cont.isActive) {
                    cont.resume(
                        RenderOutcome(
                            success = false,
                            errorMessage = error.message ?: "Unknown render error"
                        ),
                        onCancellation = null
                    )
                }
            }
        })

        cont.invokeOnCancellation {
            cancel()
        }
    }

    /**
     * Start on-device rendering for the given project document and source video.
     */
    fun startRender(request: RenderRequest, callback: RenderCallback) {
        if (!request.sourceVideoFile.exists()) {
            callback.onError(IllegalArgumentException("Source video file does not exist: ${request.sourceVideoFile.absolutePath}"))
            return
        }

        try {
            // 1. Parse Project Document: segments & overlay tracks
            val docObj = try {
                gson.fromJson(request.projectDocJson, JsonObject::class.java)
            } catch (e: Exception) {
                JsonObject()
            }

            val timelineObj = docObj.getAsJsonObject("timeline")
            val segmentsArray = timelineObj?.getAsJsonArray("segments") ?: JsonArray()

            val segmentSpans = mutableListOf<Pair<Long, Long>>() // startMs, endMs
            for (i in 0 until segmentsArray.size()) {
                val seg = segmentsArray.get(i).asJsonObject
                val s = seg.get("source_start")?.asFloat ?: 0f
                val e = seg.get("source_end")?.asFloat ?: 0f
                if (e > s) {
                    segmentSpans.add(Pair((s * 1000f).toLong(), (e * 1000f).toLong()))
                }
            }

            // Read source video properties (duration, dimensions, upright rotation)
            val retriever = MediaMetadataRetriever()
            retriever.setDataSource(request.sourceVideoFile.absolutePath)
            val durStr = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
            val rawW = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 1920
            val rawH = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 1080
            val rot = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull() ?: 0
            retriever.release()

            if (segmentSpans.isEmpty()) {
                val totalMs = durStr?.toLongOrNull() ?: 10_000L
                segmentSpans.add(Pair(0L, totalMs))
            }

            val srcW = if (rot == 90 || rot == 270) rawH else rawW
            val srcH = if (rot == 90 || rot == 270) rawW else rawH

            // 2. Parse Caption & Static Text/Overlay Tracks (remap to presentation time for multi-piece EDL)
            val rawOverlayGroups = parseOverlayGroups(docObj)
            val clipStartSec = if (segmentSpans.isNotEmpty()) segmentSpans.first().first / 1000f else 0f
            val overlayGroups = if (segmentSpans.size > 1) {
                remapOverlayGroupsToTimeline(rawOverlayGroups, segmentSpans, clipStartSec)
            } else {
                rawOverlayGroups
            }

            // 3. Build Video Effects List (Horizontal Crop using cropAnchorX + 9:16 layout + Overlays)
            val is720p = request.targetResolution.equals("720p", ignoreCase = true)
            val targetWidth = if (is720p) 720 else 1080
            val targetHeight = if (is720p) 1280 else 1920

            val effectsBuilder = ImmutableList.builder<androidx.media3.common.Effect>()

            val isPortraitTarget = request.layoutMode.contains("portrait", ignoreCase = true) ||
                                  request.layoutMode.contains("9_16", ignoreCase = true) ||
                                  request.layoutMode.contains("crop", ignoreCase = true)

            if (isPortraitTarget) {
                val targetAspect = 9f / 16f
                val srcAspect = srcW.toFloat() / srcH.toFloat().coerceAtLeast(1f)

                if (srcAspect > targetAspect) {
                    // Source is wider than 9:16 (e.g. 16:9). Crop horizontally according to cropAnchorX
                    val cropFractionW = targetAspect / srcAspect
                    val slackW = (1.0f - cropFractionW).coerceAtLeast(0f)
                    val anchor = request.cropAnchorX.coerceIn(0f, 1f)
                    val normLeft = anchor * slackW
                    val normRight = normLeft + cropFractionW
                    val ndcLeft = -1.0f + 2.0f * normLeft
                    val ndcRight = -1.0f + 2.0f * normRight
                    effectsBuilder.add(Crop(ndcLeft, ndcRight, -1.0f, 1.0f))
                } else if (srcAspect < targetAspect) {
                    // Source is narrower/taller than 9:16. Crop vertically centered
                    val cropFractionH = srcAspect / targetAspect
                    val slackH = (1.0f - cropFractionH).coerceAtLeast(0f)
                    val normTop = 0.5f * slackH
                    val normBottom = normTop + cropFractionH
                    val ndcBottom = -1.0f + 2.0f * (1.0f - normBottom)
                    val ndcTop = -1.0f + 2.0f * (1.0f - normTop)
                    effectsBuilder.add(Crop(-1.0f, 1.0f, ndcBottom, ndcTop))
                }

                // Scale cropped 9:16 frame to exact output resolution
                effectsBuilder.add(
                    Presentation.createForWidthAndHeight(
                        targetWidth,
                        targetHeight,
                        Presentation.LAYOUT_SCALE_TO_FIT
                    )
                )
            } else {
                effectsBuilder.add(
                    Presentation.createForWidthAndHeight(
                        targetWidth,
                        targetHeight,
                        Presentation.LAYOUT_SCALE_TO_FIT
                    )
                )
            }

            // Dynamic word-highlighting caption and text overlay if any groups exist
            if (overlayGroups.isNotEmpty()) {
                val captionOverlay = object : CanvasOverlay(false) {
                    override fun onDraw(canvas: Canvas, presentationTimeUs: Long) {
                        val timelineSeconds = presentationTimeUs / 1_000_000f
                        val activeGroups = overlayGroups.filter { g ->
                            timelineSeconds >= g.start && timelineSeconds <= g.end
                        }
                        if (activeGroups.isEmpty()) return

                        val canvasScale = canvas.width / 1080f

                        for (group in activeGroups) {
                            if (group.words.isEmpty()) continue

                            val baseFontSize = group.fontSizeSp * canvasScale
                            val maxContentWidth = canvas.width * group.maxWidth

                            val isBold = group.fontWeight.contains("bold", ignoreCase = true) ||
                                         group.fontWeight == "700" || group.fontWeight == "800" || group.fontWeight == "900"
                            val isItalic = group.fontStyle.equals("italic", ignoreCase = true)

                            val resolvedTypeface = getTypeface(context, group.fontFamily, isBold, isItalic)

                            val textPaint = Paint().apply {
                                color = group.primaryColor
                                textSize = baseFontSize
                                typeface = resolvedTypeface
                                isAntiAlias = true
                                textAlign = Paint.Align.LEFT
                                if (group.shadowEnabled) {
                                    setShadowLayer(
                                        group.shadowRadius * canvasScale,
                                        group.shadowOffsetX * canvasScale,
                                        group.shadowOffsetY * canvasScale,
                                        group.shadowColor
                                    )
                                }
                            }

                            val activeTextPaint = Paint().apply {
                                color = group.highlightColor
                                textSize = baseFontSize // Same layout size: scale pop handled by canvas matrix
                                typeface = resolvedTypeface
                                isAntiAlias = true
                                textAlign = Paint.Align.LEFT
                                if (group.shadowEnabled) {
                                    setShadowLayer(
                                        group.shadowRadius * canvasScale,
                                        group.shadowOffsetX * canvasScale,
                                        group.shadowOffsetY * canvasScale,
                                        group.shadowColor
                                    )
                                }
                            }

                            val strokePaint = if (group.strokeEnabled) {
                                Paint().apply {
                                    color = group.strokeColor
                                    style = Paint.Style.STROKE
                                    strokeWidth = group.strokeWidthPx * canvasScale
                                    textSize = baseFontSize
                                    typeface = resolvedTypeface
                                    isAntiAlias = true
                                    textAlign = Paint.Align.LEFT
                                }
                            } else null

                            val activeStrokePaint = if (group.strokeEnabled) {
                                Paint().apply {
                                    color = group.strokeColor
                                    style = Paint.Style.STROKE
                                    strokeWidth = group.strokeWidthPx * canvasScale
                                    textSize = baseFontSize
                                    typeface = resolvedTypeface
                                    isAntiAlias = true
                                    textAlign = Paint.Align.LEFT
                                }
                            } else null

                            val lines = partitionWordsIntoVisualLines(
                                group.words,
                                textPaint,
                                maxContentWidth,
                                group.textTransform,
                                group.letterSpacing * canvasScale
                            )
                            if (lines.isEmpty()) continue

                            val fontMetrics = textPaint.fontMetrics
                            val lineHeight = (fontMetrics.descent - fontMetrics.ascent) * group.lineHeight
                            val totalBlockHeight = lines.size * lineHeight

                            val blockCenterY = canvas.height * group.positionY
                            val blockTopY = blockCenterY - (totalBlockHeight / 2f)

                            // 1. Draw Background Box if enabled
                            if (group.backgroundEnabled && group.backgroundMode != "none") {
                                val bgPaint = Paint().apply {
                                    color = group.backgroundColor
                                    style = Paint.Style.FILL
                                    isAntiAlias = true
                                }
                                val padX = group.backgroundPaddingX * canvasScale
                                val padY = group.backgroundPaddingY * canvasScale
                                val cornerRad = group.backgroundCornerRadius * canvasScale

                                if (group.backgroundMode == "line") {
                                    // Per-line pills
                                    for (lIdx in lines.indices) {
                                        val line = lines[lIdx]
                                        val lineCenterY = blockTopY + (lIdx + 0.5f) * lineHeight
                                        val lineStartX = when (group.alignment.lowercase(Locale.ROOT)) {
                                            "left" -> canvas.width * group.positionX - (maxContentWidth / 2f)
                                            "right" -> canvas.width * group.positionX + (maxContentWidth / 2f) - line.lineWidth
                                            else -> (canvas.width * group.positionX) - (line.lineWidth / 2f)
                                        }
                                        val pillRect = RectF(
                                            lineStartX - padX,
                                            lineCenterY - (lineHeight / 2f) - padY,
                                            lineStartX + line.lineWidth + padX,
                                            lineCenterY + (lineHeight / 2f) + padY
                                        )
                                        canvas.drawRoundRect(pillRect, cornerRad, cornerRad, bgPaint)
                                    }
                                } else {
                                    // Block mode (encloses all lines)
                                    var minLeft = Float.MAX_VALUE
                                    var maxRight = Float.MIN_VALUE
                                    for (line in lines) {
                                        val lineStartX = when (group.alignment.lowercase(Locale.ROOT)) {
                                            "left" -> canvas.width * group.positionX - (maxContentWidth / 2f)
                                            "right" -> canvas.width * group.positionX + (maxContentWidth / 2f) - line.lineWidth
                                            else -> (canvas.width * group.positionX) - (line.lineWidth / 2f)
                                        }
                                        minLeft = minOf(minLeft, lineStartX)
                                        maxRight = maxOf(maxRight, lineStartX + line.lineWidth)
                                    }
                                    val blockRect = RectF(
                                        minLeft - padX,
                                        blockTopY - padY,
                                        maxRight + padX,
                                        blockTopY + totalBlockHeight + padY
                                    )
                                    canvas.drawRoundRect(blockRect, cornerRad, cornerRad, bgPaint)
                                }
                            }

                            // 2. Draw Lines and Words
                            val spaceWidth = textPaint.measureText(" ")
                            for (lIdx in lines.indices) {
                                val line = lines[lIdx]
                                val lineCenterY = blockTopY + (lIdx + 0.5f) * lineHeight
                                val baselineY = lineCenterY - (fontMetrics.ascent + fontMetrics.descent) / 2f

                                val lineStartX = when (group.alignment.lowercase(Locale.ROOT)) {
                                    "left" -> canvas.width * group.positionX - (maxContentWidth / 2f)
                                    "right" -> canvas.width * group.positionX + (maxContentWidth / 2f) - line.lineWidth
                                    else -> (canvas.width * group.positionX) - (line.lineWidth / 2f)
                                }

                                var curX = lineStartX
                                for (vWord in line.words) {
                                    val raw = vWord.rawWord
                                    val isWordActive = group.isCaption && group.highlightEnabled && timelineSeconds >= raw.start && timelineSeconds <= raw.end

                                    var scale = 1.0f
                                    var translateY = 0f

                                    if (isWordActive && group.activeAnimType != "none") {
                                        val animDurSec = group.activeAnimDurationMs / 1000f
                                        val elapsed = (timelineSeconds - raw.start).coerceAtLeast(0f)
                                        if (elapsed < animDurSec) {
                                            val progress = (elapsed / animDurSec).coerceIn(0f, 1f)
                                            val punch = group.activeScaleMax - 1.0f
                                            if (group.activeAnimType == "bounce") {
                                                scale = 1.0f + punch * cos(progress * (PI / 2).toFloat())
                                                translateY = -8f * canvasScale * sin(progress * PI.toFloat())
                                            } else { // "pop"
                                                scale = 1.0f + punch * cos(progress * (PI / 2).toFloat())
                                            }
                                        } else {
                                            scale = 1.08f // Gentle steady karaoke scale pop
                                        }
                                    } else if (isWordActive) {
                                        scale = 1.08f
                                    }

                                    val fillPaint = if (isWordActive) activeTextPaint else textPaint
                                    val strokeP = if (isWordActive) activeStrokePaint else strokePaint

                                    if (scale != 1.0f || translateY != 0f) {
                                        val wordCenterX = curX + vWord.width / 2f
                                        val wordCenterY = baselineY + (fontMetrics.ascent + fontMetrics.descent) / 2f
                                        canvas.save()
                                        canvas.translate(0f, translateY)
                                        canvas.scale(scale, scale, wordCenterX, wordCenterY)
                                        strokeP?.let { canvas.drawText(vWord.text, curX, baselineY, it) }
                                        canvas.drawText(vWord.text, curX, baselineY, fillPaint)
                                        canvas.restore()
                                    } else {
                                        strokeP?.let { canvas.drawText(vWord.text, curX, baselineY, it) }
                                        canvas.drawText(vWord.text, curX, baselineY, fillPaint)
                                    }

                                    curX += vWord.width + spaceWidth
                                }
                            }
                        }
                    }
                }

                effectsBuilder.add(
                    OverlayEffect(
                        ImmutableList.of<TextureOverlay>(captionOverlay)
                    )
                )
            }

            val itemEffects = Effects(
                ImmutableList.of(), // Audio processors
                effectsBuilder.build()
            )

            // 4. Build EditedMediaItems for multi-segment sequence
            val sequenceItems = mutableListOf<EditedMediaItem>()
            val mediaItemUri = Uri.fromFile(request.sourceVideoFile)

            for ((startMs, endMs) in segmentSpans) {
                val clipConfig = ClippingConfiguration.Builder()
                    .setStartPositionMs(startMs)
                    .setEndPositionMs(endMs)
                    .build()

                val mediaItem = MediaItem.Builder()
                    .setUri(mediaItemUri)
                    .setClippingConfiguration(clipConfig)
                    .build()

                val item = EditedMediaItem.Builder(mediaItem)
                    .setEffects(itemEffects)
                    .build()
                sequenceItems.add(item)
            }

            val sequence = EditedMediaItemSequence.Builder(sequenceItems).build()
            val composition = Composition.Builder(ImmutableList.of(sequence)).build()

            // 5. Build Media3 Transformer with H.264 & AAC hardware encoders
            val tempOutput = File(request.outputFile.parentFile, "${request.outputFile.name}.tmp")
            if (tempOutput.exists()) tempOutput.delete()

            val transformer = Transformer.Builder(context)
                .setVideoMimeType(MimeTypes.VIDEO_H264)
                .setAudioMimeType(MimeTypes.AUDIO_AAC)
                .addListener(object : Transformer.Listener {
                    override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                        progressJob?.cancel()
                        Log.i(TAG, "Media3 export completed successfully: ${tempOutput.length()} bytes")

                        try {
                            // Atomic replace to final output
                            if (request.outputFile.exists()) request.outputFile.delete()
                            tempOutput.renameTo(request.outputFile)

                            // Validate with MediaMetadataRetriever
                            val valRetriever = MediaMetadataRetriever()
                            valRetriever.setDataSource(request.outputFile.absolutePath)
                            val finalDurStr = valRetriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                            val durationSeconds = (finalDurStr?.toFloatOrNull() ?: 0f) / 1000f

                            // Generate thumbnail at 50%
                            if (request.thumbnailFile != null && durationSeconds > 0) {
                                try {
                                    val frameUs = ((durationSeconds / 2f) * 1_000_000f).toLong()
                                    val bitmap = valRetriever.getFrameAtTime(frameUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                                    if (bitmap != null) {
                                        FileOutputStream(request.thumbnailFile).use { out ->
                                            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
                                        }
                                    }
                                } catch (e: Exception) {
                                    Log.w(TAG, "Thumbnail generation non-fatal error: ${e.message}")
                                }
                            }
                            valRetriever.release()

                            callback.onProgress(100)
                            callback.onCompleted(request.outputFile, request.thumbnailFile, durationSeconds)
                        } catch (e: Exception) {
                            callback.onError(e)
                        }
                    }

                    override fun onError(composition: Composition, exportResult: ExportResult, exportException: ExportException) {
                        progressJob?.cancel()
                        tempOutput.delete()
                        Log.e(TAG, "Media3 export failed: ${exportException.message}", exportException)
                        callback.onError(exportException)
                    }
                })
                .build()

            activeTransformer = transformer

            // 6. Start Export & Progress Polling
            transformer.start(composition, tempOutput.absolutePath)

            progressJob = coroutineScope.launch(Dispatchers.Default) {
                val progressHolder = ProgressHolder()
                while (isActive) {
                    delay(PROGRESS_POLL_INTERVAL_MS)
                    val progressState = transformer.getProgress(progressHolder)
                    if (progressState == Transformer.PROGRESS_STATE_AVAILABLE) {
                        val pct = max(0, min(99, progressHolder.progress))
                        callback.onProgress(pct)
                    }
                }
            }

        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize Media3 render: ${e.message}", e)
            callback.onError(e)
        }
    }

    /**
     * Cancel active render job if in progress.
     */
    fun cancel() {
        progressJob?.cancel()
        activeTransformer?.cancel()
        activeTransformer = null
    }
}
