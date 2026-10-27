package com.resolvia.dispatch.sync

import java.io.File

/**
 * Pure policies for serving finalized recording media to the WebView
 * (`/recordings/` asset endpoint). Extracted from MainActivity so the
 * path-containment contract, MIME mapping, and byte-range arithmetic can be
 * unit tested without an Android device.
 */
object RecordingMediaPolicy {

    /** MIME mapping for served recording files (defaults to octet-stream). */
    fun mediaMimeFor(fileName: String): String = when {
        fileName.endsWith(".mp4", ignoreCase = true) -> "video/mp4"
        fileName.endsWith(".jpg", ignoreCase = true) || fileName.endsWith(".jpeg", ignoreCase = true) -> "image/jpeg"
        fileName.endsWith(".png", ignoreCase = true) -> "image/png"
        else -> "application/octet-stream"
    }

    /**
     * Resolve a `/recordings/<name>` request path to a readable media file.
     *
     * Contract:
     * - URL-decodes the segment name.
     * - Only flat file names are accepted (no subpaths, no separators).
     * - Path traversal (`..`), colon tricks, and in-flight `.tmp` segment
     *   files are refused so half-written recordings are never presented as
     *   playable assets.
     * - The resolved canonical file must exist, be readable, a regular file,
     *   and remain strictly inside one of the recording roots.
     */
    fun resolveRecordingPath(path: String, extRecDir: File, intRecDir: File): File? {
        return try {
            val decoded = java.net.URLDecoder.decode(path.trim(), "UTF-8")
            if (decoded.isEmpty()) return null
            val name = decoded.replace('\\', '/').trim('/')
            if (name.isEmpty() || name.contains("..") || name.contains(':')) return null
            if (name.contains('/') || name.endsWith(".tmp")) return null

            val roots = listOf(extRecDir, intRecDir).map { it.canonicalFile }
            roots
                .map { root -> File(root, name).canonicalFile }
                .firstOrNull { resolved ->
                    resolved.exists() && resolved.canRead() && !resolved.isDirectory &&
                        roots.any { root -> resolved.path.startsWith(root.path + File.separator) }
                }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * Parse an HTTP `Range: bytes=...` header for a file of `fileLength`
     * bytes. Returns the clamped inclusive (start, end) pair that the media
     * handler must serve as 206 Partial Content, or null when the header is
     * absent/invalid (caller falls back to a 200 full-body response).
     *
     * Mirrors the WebView serving handler: suffix ranges (`bytes=-500`)
     * currently start at zero, an open end means EOF, and out-of-bounds
     * requests are clamped rather than rejected so playback/scrubbing always
     * resolves.
     */
    fun parseByteRange(rangeHeader: String?, fileLength: Long): Pair<Long, Long>? {
        if (rangeHeader == null) return null
        val header = rangeHeader.trim()
        if (!header.startsWith("bytes=") || fileLength <= 0L) return null
        return try {
            val range = header.removePrefix("bytes=").trim()
            val parts = range.split("-", limit = 2)
            var start = parts[0].toLongOrNull() ?: 0L
            var end = if (parts.size > 1 && parts[1].isNotBlank()) {
                parts[1].toLongOrNull() ?: (fileLength - 1)
            } else {
                fileLength - 1
            }
            if (end >= fileLength) end = fileLength - 1
            if (start > end) start = 0L
            if (start < 0) start = 0L
            start to end
        } catch (e: Exception) {
            null
        }
    }
}
