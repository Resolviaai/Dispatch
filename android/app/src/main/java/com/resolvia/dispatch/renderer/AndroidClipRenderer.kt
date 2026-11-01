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
import kotlin.math.max
import kotlin.math.min

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

        fun parseOverlayGroups(docObj: JsonObject): List<CaptionGroup> {
            val groups = mutableListOf<CaptionGroup>()
            val tracks = docObj.getAsJsonArray("tracks") ?: return groups

            for (i in 0 until tracks.size()) {
                val track = tracks.get(i).asJsonObject
                val kind = track.get("kind")?.asString ?: track.get("type")?.asString ?: ""
                val isCaption = (kind == "captions" || kind == "caption")
                val isTextOrOverlay = (kind == "text" || kind == "overlay")
                if (!isCaption && !isTextOrOverlay) continue

                val defaultStyle = track.getAsJsonObject("default_style") ?: JsonObject()
                val defaultLayout = track.getAsJsonObject("default_layout") ?: JsonObject()

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

                        // Layout extraction
                        val posX = groupLayout.get("position_x")?.asFloat ?: defaultLayout.get("position_x")?.asFloat ?: 0.5f
                        val defaultPosY = if (isCaption) 0.78f else 0.50f
                        val posY = groupLayout.get("position_y")?.asFloat ?: defaultLayout.get("position_y")?.asFloat ?: defaultPosY
                        val alignment = groupLayout.get("alignment")?.asString ?: defaultLayout.get("alignment")?.asString ?: "center"

                        // Style extraction
                        val primaryColorStr = groupStyle.get("primary_color")?.asString ?: groupStyle.get("color")?.asString
                        val highlightColorStr = groupStyle.get("highlight_color")?.asString
                        val fontSize = groupStyle.get("font_size")?.asFloat ?: (if (isCaption) 36f else 32f)

                        val outlineEnabled = groupStyle.get("outline_enabled")?.asBoolean
                            ?: groupStyle.get("stroke_enabled")?.asBoolean
                            ?: false
                        val outlineColorStr = groupStyle.get("outline_color")?.asString ?: groupStyle.get("stroke_color")?.asString
                        val outlineWidth = groupStyle.get("outline_width")?.asFloat ?: groupStyle.get("stroke_width")?.asFloat ?: 4f

                        val shadowEnabled = groupStyle.get("shadow_enabled")?.asBoolean ?: true
                        val shadowColorStr = groupStyle.get("shadow_color")?.asString
                        val shadowOffsetX = groupStyle.get("shadow_offset_x")?.asFloat ?: 2f
                        val shadowOffsetY = groupStyle.get("shadow_offset_y")?.asFloat ?: 4f
                        val shadowBlur = groupStyle.get("shadow_blur")?.asFloat ?: 6f

                        val backgroundEnabled = groupStyle.get("background_enabled")?.asBoolean ?: isCaption
                        val backgroundColorStr = groupStyle.get("background_color")?.asString

                        groups.add(
                            CaptionGroup(
                                id = gId,
                                start = gStart,
                                end = gEnd,
                                words = words,
                                primaryColor = parseHexOrRgbaColor(primaryColorStr, -1),
                                highlightColor = parseHexOrRgbaColor(highlightColorStr, 0xFFFFE600.toInt()),
                                fontSizeSp = fontSize,
                                backgroundEnabled = backgroundEnabled,
                                isCaption = isCaption,
                                positionX = posX,
                                positionY = posY,
                                alignment = alignment,
                                strokeEnabled = outlineEnabled,
                                strokeColor = parseHexOrRgbaColor(outlineColorStr, 0xFF000000.toInt()),
                                strokeWidthPx = outlineWidth,
                                shadowEnabled = shadowEnabled,
                                shadowColor = parseHexOrRgbaColor(shadowColorStr, 0xC8000000.toInt()),
                                shadowOffsetX = shadowOffsetX,
                                shadowOffsetY = shadowOffsetY,
                                shadowRadius = shadowBlur,
                                backgroundColor = parseHexOrRgbaColor(backgroundColorStr, 0xB4000000.toInt())
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
        val backgroundColor: Int = 0xB4000000.toInt()
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

            // 2. Parse Caption & Static Text/Overlay Tracks
            val overlayGroups = parseOverlayGroups(docObj)

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
                    private val density = context.resources.displayMetrics.scaledDensity

                    override fun onDraw(canvas: Canvas, presentationTimeUs: Long) {
                        val timelineSeconds = presentationTimeUs / 1_000_000f
                        val activeGroups = overlayGroups.filter { g ->
                            timelineSeconds >= g.start && timelineSeconds <= g.end
                        }
                        if (activeGroups.isEmpty()) return

                        for (group in activeGroups) {
                            if (group.words.isEmpty()) continue

                            val baseFontSize = (if (is720p) group.fontSizeSp * 0.75f else group.fontSizeSp) * density
                            val activeFontSize = baseFontSize * 1.08f // 1.08x active word scale pop

                            val textPaint = Paint().apply {
                                color = group.primaryColor
                                textSize = baseFontSize
                                typeface = Typeface.DEFAULT_BOLD
                                isAntiAlias = true
                                textAlign = Paint.Align.LEFT
                                if (group.shadowEnabled) {
                                    setShadowLayer(group.shadowRadius * density, group.shadowOffsetX * density, group.shadowOffsetY * density, group.shadowColor)
                                }
                            }

                            val activeTextPaint = Paint().apply {
                                color = group.highlightColor
                                textSize = activeFontSize
                                typeface = Typeface.DEFAULT_BOLD
                                isAntiAlias = true
                                textAlign = Paint.Align.LEFT
                                if (group.shadowEnabled) {
                                    setShadowLayer(group.shadowRadius * density, group.shadowOffsetX * density, group.shadowOffsetY * density, group.shadowColor)
                                }
                            }

                            val strokePaint = if (group.strokeEnabled) {
                                Paint().apply {
                                    color = group.strokeColor
                                    style = Paint.Style.STROKE
                                    strokeWidth = group.strokeWidthPx * density
                                    textSize = baseFontSize
                                    typeface = Typeface.DEFAULT_BOLD
                                    isAntiAlias = true
                                    textAlign = Paint.Align.LEFT
                                }
                            } else null

                            val activeStrokePaint = if (group.strokeEnabled) {
                                Paint().apply {
                                    color = group.strokeColor
                                    style = Paint.Style.STROKE
                                    strokeWidth = group.strokeWidthPx * density
                                    textSize = activeFontSize
                                    typeface = Typeface.DEFAULT_BOLD
                                    isAntiAlias = true
                                    textAlign = Paint.Align.LEFT
                                }
                            } else null

                            val bgPaint = if (group.backgroundEnabled) {
                                Paint().apply {
                                    color = group.backgroundColor
                                    style = Paint.Style.FILL
                                    isAntiAlias = true
                                }
                            } else null

                            val spaceWidth = textPaint.measureText(" ")
                            val wordWidths = group.words.map { word ->
                                val isWordActive = group.isCaption && timelineSeconds >= word.start && timelineSeconds <= word.end
                                if (isWordActive) activeTextPaint.measureText(word.text) else textPaint.measureText(word.text)
                            }
                            val totalWordsWidth = wordWidths.sum()
                            val totalWidth = totalWordsWidth + (group.words.size - 1).coerceAtLeast(0) * spaceWidth

                            val anchorX = canvas.width * group.positionX
                            val centerY = canvas.height * group.positionY
                            val startX = when (group.alignment.lowercase()) {
                                "left" -> anchorX
                                "right" -> anchorX - totalWidth
                                else -> anchorX - (totalWidth / 2f)
                            }

                            if (bgPaint != null) {
                                val fontMetrics = textPaint.fontMetrics
                                val paddingX = 24f * density
                                val paddingY = 16f * density
                                val pillRect = RectF(
                                    startX - paddingX,
                                    centerY + fontMetrics.top - paddingY,
                                    startX + totalWidth + paddingX,
                                    centerY + fontMetrics.bottom + paddingY
                                )
                                canvas.drawRoundRect(pillRect, 16f * density, 16f * density, bgPaint)
                            }

                            var currentX = startX
                            for (wIdx in group.words.indices) {
                                val word = group.words[wIdx]
                                val isWordActive = group.isCaption && timelineSeconds >= word.start && timelineSeconds <= word.end
                                val fillPaint = if (isWordActive) activeTextPaint else textPaint
                                val strokeP = if (isWordActive) activeStrokePaint else strokePaint

                                strokeP?.let { canvas.drawText(word.text, currentX, centerY, it) }
                                canvas.drawText(word.text, currentX, centerY, fillPaint)
                                currentX += wordWidths[wIdx] + spaceWidth
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
