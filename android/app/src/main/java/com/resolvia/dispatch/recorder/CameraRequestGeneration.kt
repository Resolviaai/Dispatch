package com.resolvia.dispatch.recorder

import java.util.concurrent.atomic.AtomicLong

/** Tracks the newest camera-control request so cancelled or late futures cannot overwrite state. */
internal class CameraRequestGeneration {
    private val generation = AtomicLong(0)

    fun begin(): Long = generation.incrementAndGet()

    fun invalidate() {
        generation.incrementAndGet()
    }

    fun isCurrent(requestGeneration: Long): Boolean = generation.get() == requestGeneration
}
