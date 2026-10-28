package com.resolvia.dispatch.recorder

import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.util.Log
import com.resolvia.dispatch.sync.RecordingMediaPolicy
import java.io.File
import java.io.FileOutputStream

/**
 * Writes the poster JPEG shown for a recorded segment in the Sessions list.
 *
 * The WebView cannot poster a `<video>` served by the local `/recordings/`
 * interceptor, so a real image file is required. The name is derived from the
 * video by [RecordingMediaPolicy.thumbnailNameFor] rather than stored in a
 * column, which keeps this off the Room schema (and therefore off any migration).
 */
object RecordingPosterWriter {

    private const val TAG = "RecordingPosterWriter"
    private const val MAX_WIDTH = 480
    private const val JPEG_QUALITY = 82

    fun posterFileFor(videoFile: File): File =
        File(videoFile.parentFile, RecordingMediaPolicy.thumbnailNameFor(videoFile.name))

    /**
     * Returns a usable poster for [videoFile], creating it if absent. Never
     * throws: a failure degrades to a missing thumbnail, never to a failed
     * commit or a failed list render.
     */
    fun ensurePoster(videoFile: File): File? {
        if (!videoFile.isFile || videoFile.length() <= 0L) return null
        val poster = posterFileFor(videoFile)
        if (poster.isFile && poster.length() > 0L) return poster

        var retriever: MediaMetadataRetriever? = null
        try {
            retriever = MediaMetadataRetriever()
            retriever.setDataSource(videoFile.absolutePath)
            val frame = retriever.getFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST) ?: return null
            val scaled = scaleToFit(frame)
            FileOutputStream(poster).use { out ->
                val ok = scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
                if (!ok) poster.delete()
            }
            if (scaled !== frame) scaled.recycle()
            frame.recycle()
            return if (poster.isFile && poster.length() > 0L) poster else null
        } catch (e: Exception) {
            Log.w(TAG, "poster for ${videoFile.name} failed: ${e.message}")
            poster.delete()
            return null
        } finally {
            try {
                retriever?.release()
            } catch (_: Exception) {
            }
        }
    }

    private fun scaleToFit(frame: Bitmap): Bitmap {
        if (frame.width <= MAX_WIDTH) return frame
        val ratio = MAX_WIDTH.toFloat() / frame.width
        return Bitmap.createScaledBitmap(
            frame,
            (frame.width * ratio).toInt().coerceAtLeast(1),
            (frame.height * ratio).toInt().coerceAtLeast(1),
            true
        )
    }
}
