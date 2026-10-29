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
     * Poster file name derived from a finalized segment, e.g.
     * `sess_x_seg_0001.mp4` -> `sess_x_seg_0001_thumb.jpg`. Derived rather than
     * stored so no Room schema change (and therefore no new migration) is needed.
     */
    fun thumbnailNameFor(videoFileName: String): String =
        videoFileName.substringBeforeLast('.') + "_thumb.jpg"

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
        /**
     * RFC 7233 single-range resolution.
     *
     * Returns null when the header is absent, not a `bytes` range, malformed, or
     * contains multiple ranges (a server may ignore Range and send 200, which is
     * always valid). Returns [ByteRangeResult.Unsatisfiable] when the client asked
     * for bytes that cannot exist, which must be answered with 416 + `Content-Range:
     * bytes *\/length` rather than silently rewritten into a whole-file read.
     */
    fun resolveRange(rangeHeader: String?, fileLength: Long): ByteRangeResult? {
        if (rangeHeader == null) return null
        val header = rangeHeader.trim()
        if (!header.startsWith("bytes=", ignoreCase = true) || fileLength <= 0L) return null
        val spec = header.substring(6).trim()
        if (spec.isEmpty() || spec.contains(',')) return null
        val dash = spec.indexOf('-')
        if (dash < 0) return null
        val first = spec.substring(0, dash).trim()
        val last = spec.substring(dash + 1).trim()
        return try {
            if (first.isEmpty()) {
                // Suffix range: the LAST n bytes, not the first n.
                val suffix = last.toLongOrNull() ?: return null
                if (suffix <= 0L) return ByteRangeResult.Unsatisfiable
                val start = if (suffix >= fileLength) 0L else fileLength - suffix
                ByteRangeResult.Satisfiable(start, fileLength - 1)
            } else {
                val start = first.toLongOrNull() ?: return null
                val end = if (last.isEmpty()) fileLength - 1 else (last.toLongOrNull() ?: return null)
                if (start < 0L || start >= fileLength || end < start) {
                    ByteRangeResult.Unsatisfiable
                } else {
                    ByteRangeResult.Satisfiable(start, minOf(end, fileLength - 1))
                }
            }
        } catch (e: Exception) {
            null
        }
    }
}

/** Outcome of resolving a client Range header. */
sealed interface ByteRangeResult {
    data class Satisfiable(val start: Long, val end: Long) : ByteRangeResult
    object Unsatisfiable : ByteRangeResult
}
