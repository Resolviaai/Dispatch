package com.resolvia.dispatch.sync

/** Tracks only server-confirmed offsets and bounds repeated non-advancing responses. */
internal class ConfirmedUploadProgress(initialOffset: Long, private val totalBytes: Long) {
    var offset: Long = initialOffset.coerceIn(0L, totalBytes)
        private set
    var consecutiveNoProgress: Int = 0
        private set

    val retryExhausted: Boolean
        get() = consecutiveNoProgress >= MAX_NO_PROGRESS_ATTEMPTS

    /** Returns true when a valid authoritative server offset changes our saved offset. */
    fun observe(serverOffset: Long?): Boolean {
        if (serverOffset == null || serverOffset !in 0L..totalBytes) {
            consecutiveNoProgress++
            return false
        }
        if (serverOffset == offset) {
            consecutiveNoProgress++
            return false
        }
        offset = serverOffset
        consecutiveNoProgress = 0
        return true
    }

    fun retryDelayMs(jitterMs: Long): Long {
        val exponent = (consecutiveNoProgress - 1).coerceAtLeast(0).coerceAtMost(3)
        val backoff = (1_000L shl exponent).coerceAtMost(MAX_BACKOFF_MS)
        return backoff + jitterMs.coerceIn(0L, MAX_JITTER_MS)
    }

    companion object {
        const val MAX_NO_PROGRESS_ATTEMPTS = 4
        const val MAX_BACKOFF_MS = 8_000L
        const val MAX_JITTER_MS = 500L
    }
}
