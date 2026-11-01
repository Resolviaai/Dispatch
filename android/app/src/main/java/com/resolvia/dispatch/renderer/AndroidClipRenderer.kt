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
 * Performs multi-segment trimming and concatenation, 9:16 layout presentation,
 * and frame-accurate word-level karaoke caption rendering.
 */
@OptIn(UnstableApi::class)
class AndroidClipRenderer(
    private val context: Context,
    private val coroutineScope: CoroutineScope = CoroutineScope(Dispatchers.Default)
) {
    companion object {
        private const val TAG = "AndroidClipRenderer"
        private const val PROGRESS_POLL_INTERVAL_MS = 250L
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
        val primaryColor: Int = Color.WHITE,
        val highlightColor: Int = Color.YELLOW,
        val fontSizeSp: Float = 36f,
        val backgroundEnabled: Boolean = true
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
            // 1. Parse Project Document: segments & caption track
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

            // Fallback: full source duration or first 10 seconds if no segments
            if (segmentSpans.isEmpty()) {
                val retriever = MediaMetadataRetriever()
                retriever.setDataSource(request.sourceVideoFile.absolutePath)
                val durStr = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                retriever.release()
                val totalMs = durStr?.toLongOrNull() ?: 10_000L
                segmentSpans.add(Pair(0L, totalMs))
            }

            // 2. Parse Caption Track for On-Device Text Overlay
            val captionGroups = parseCaptionGroups(docObj)

            // 3. Build Video Effects List (9:16 layout + optional Captions CanvasOverlay)
            val is720p = request.targetResolution.equals("720p", ignoreCase = true)
            val targetWidth = if (is720p) 720 else 1080
            val targetHeight = if (is720p) 1280 else 1920

            val effectsBuilder = ImmutableList.builder<androidx.media3.common.Effect>()

            // 9:16 Presentation scale to fit or crop depending on layoutMode
            val presentationLayout = if (request.layoutMode.contains("crop", ignoreCase = true) || request.layoutMode.contains("portrait_9_16", ignoreCase = true)) {
                Presentation.LAYOUT_SCALE_TO_FIT_WITH_CROP
            } else {
                Presentation.LAYOUT_SCALE_TO_FIT
            }

            effectsBuilder.add(
                Presentation.createForWidthAndHeight(
                    targetWidth,
                    targetHeight,
                    presentationLayout
                )
            )

            // Add dynamic word-highlighting caption overlay if captions exist
            if (captionGroups.isNotEmpty()) {
                val captionOverlay = object : CanvasOverlay(false) {
                    private val bgPaint = Paint().apply {
                        color = Color.argb(190, 0, 0, 0)
                        style = Paint.Style.FILL
                        isAntiAlias = true
                    }
                    private val textPaint = Paint().apply {
                        color = Color.WHITE
                        textSize = (if (is720p) 26f else 40f) * context.resources.displayMetrics.scaledDensity
                        typeface = Typeface.DEFAULT_BOLD
                        isAntiAlias = true
                        textAlign = Paint.Align.LEFT
                    }
                    private val highlightPaint = Paint().apply {
                        color = Color.rgb(255, 230, 0) // Punchy yellow highlight
                        textSize = (if (is720p) 26f else 40f) * context.resources.displayMetrics.scaledDensity
                        typeface = Typeface.DEFAULT_BOLD
                        isAntiAlias = true
                        textAlign = Paint.Align.LEFT
                    }

                    override fun onDraw(canvas: Canvas, presentationTimeUs: Long) {
                        val timelineSeconds = presentationTimeUs / 1_000_000f
                        val activeGroup = captionGroups.firstOrNull { g ->
                            timelineSeconds >= g.start && timelineSeconds <= g.end
                        } ?: return

                        if (activeGroup.words.isEmpty()) return

                        val centerX = canvas.width / 2f
                        val centerY = canvas.height * 0.78f // 78% down screen

                        val spaceWidth = textPaint.measureText(" ")
                        val wordWidths = activeGroup.words.map { textPaint.measureText(it.text) }
                        val totalWordsWidth = wordWidths.sum()
                        val totalWidth = totalWordsWidth + (activeGroup.words.size - 1).coerceAtLeast(0) * spaceWidth

                        val fontMetrics = textPaint.fontMetrics
                        val paddingX = 24f
                        val paddingY = 16f
                        val rect = RectF(
                            centerX - (totalWidth / 2f) - paddingX,
                            centerY + fontMetrics.top - paddingY,
                            centerX + (totalWidth / 2f) + paddingX,
                            centerY + fontMetrics.bottom + paddingY
                        )

                        if (activeGroup.backgroundEnabled) {
                            canvas.drawRoundRect(rect, 16f, 16f, bgPaint)
                        }

                        // Draw word-by-word with frame-accurate highlight on the word active at timestamp T
                        var currentX = centerX - (totalWidth / 2f)
                        for (i in activeGroup.words.indices) {
                            val word = activeGroup.words[i]
                            val isWordActive = timelineSeconds >= word.start && timelineSeconds <= word.end
                            val paintToUse = if (isWordActive) highlightPaint else textPaint

                            canvas.drawText(word.text, currentX, centerY, paintToUse)
                            currentX += wordWidths[i] + spaceWidth
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
                            val retriever = MediaMetadataRetriever()
                            retriever.setDataSource(request.outputFile.absolutePath)
                            val durStr = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                            val durationSeconds = (durStr?.toFloatOrNull() ?: 0f) / 1000f

                            // Generate thumbnail at 50%
                            if (request.thumbnailFile != null && durationSeconds > 0) {
                                try {
                                    val frameUs = ((durationSeconds / 2f) * 1_000_000f).toLong()
                                    val bitmap = retriever.getFrameAtTime(frameUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                                    if (bitmap != null) {
                                        FileOutputStream(request.thumbnailFile).use { out ->
                                            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
                                        }
                                    }
                                } catch (e: Exception) {
                                    Log.w(TAG, "Thumbnail generation non-fatal error: ${e.message}")
                                }
                            }
                            retriever.release()

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

    private fun parseCaptionGroups(docObj: JsonObject): List<CaptionGroup> {
        val groups = mutableListOf<CaptionGroup>()
        val tracks = docObj.getAsJsonArray("tracks") ?: return groups

        for (i in 0 until tracks.size()) {
            val track = tracks.get(i).asJsonObject
            val kind = track.get("kind")?.asString ?: track.get("type")?.asString ?: ""
            if (kind != "captions") continue

            val groupsArray = track.getAsJsonArray("groups") ?: continue
            for (j in 0 until groupsArray.size()) {
                val g = groupsArray.get(j).asJsonObject
                val gId = g.get("id")?.asString ?: "grp_$j"
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
                    val rawText = g.get("text")?.asString ?: ""
                    if (rawText.isNotEmpty()) {
                        words.add(CaptionWord(rawText, gStart, gEnd))
                    }
                }

                if (gEnd > gStart && words.isNotEmpty()) {
                    groups.add(
                        CaptionGroup(
                            id = gId,
                            start = gStart,
                            end = gEnd,
                            words = words
                        )
                    )
                }
            }
        }
        return groups
    }
}
